"""Follow the speaker through its own queue via StreamGateway fetches (ADR-0007 PlaybackFollower) and confirm starts by fetch.

Mixin of :class:`xiaomusic.device_player.XiaoMusicDevice`; relies on the
attributes it sets up in ``__init__``.
"""

import asyncio
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass
from xiaomusic.const import (
    PLAY_TYPE_ONE,
    PLAY_TYPE_SIN,
)
from xiaomusic.events import DEVICE_CONFIG_CHANGED
from xiaomusic.playback.runtime_state import (
    PlaybackPhase,
    TrackReference,
    follow_track,
)
from xiaomusic.playback.task_registry import (
    TaskKind,
)
from xiaomusic.player.constants import (
    START_FETCH_TIMEOUT_SEC,
    TRACK_END_LEAD_SEC,
)
from xiaomusic.speaker_queue import (
    loop_type_for,
)
from xiaomusic.stream_gateway import SOURCE_SILENCE


class PlaybackFollowerMixin:
    def _expect_start_fetch(self, sid: int, queue_id: str, index: int) -> None:
        """The next dispatch is gateway-served: its start is confirmed by the
        speaker fetching (queue_id, index)."""
        self._start_fetch = {"sid": sid, "queue": str(queue_id), "index": int(index), "fetched": False}

    async def _confirm_when_fetched(self, *, name: str, sid: int, token, on_started) -> None:
        """Run ``on_started`` once the speaker has fetched the dispatched start
        item; report a failed start when it does not within
        START_FETCH_TIMEOUT_SEC. Dispatches the gateway does not serve (radio,
        site relay) have no such signal and count as started at once.
        """
        expected = getattr(self, "_start_fetch", None)
        if not expected or expected["sid"] != sid or expected["fetched"]:
            await on_started()
            return

        def _current() -> bool:
            return sid == self._play_session_id and not self._is_lifecycle_token_stale(token)

        async def _watch() -> None:
            waited = 0.0
            step = 0.25
            while not expected["fetched"]:
                if not _current():
                    return
                if waited >= START_FETCH_TIMEOUT_SEC:
                    self.log.warning(
                        "start_not_fetched did=%s session_id=%s name=%s", self.did, sid, name
                    )
                    await self._handle_play_failure(
                        name=name, sid=sid, reason="start_not_fetched", token=token
                    )
                    return
                await asyncio.sleep(step)
                waited += step
            if _current():
                self.log.info(
                    "start_fetched did=%s session_id=%s name=%s waited_s=%.2f",
                    self.did,
                    sid,
                    name,
                    waited,
                )
                await on_started()

        self._ensure_playback_tasks().start(
            TaskKind.PLAYBACK_CONFIRMATION,
            self._task_generation(token, sid=sid),
            _watch(),
            metadata={"name": name, "sid": sid},
        )

    def on_track_fetched(self, ref) -> None:
        """StreamGateway listener: the speaker fetched a dispatched item."""
        expected = getattr(self, "_start_fetch", None)
        if (
            expected
            and ref.source != SOURCE_SILENCE
            and str(ref.queue) == expected["queue"]
            and int(ref.index) == expected["index"]
        ):
            expected["fetched"] = True
        queue = self._active_queue()
        if queue is not None and queue.get("superseded"):
            self.log.info(
                "queue_fetch_ignored_superseded did=%s queue=%s index=%s source=%s",
                ref.did,
                ref.queue,
                ref.index,
                ref.source,
            )
            return
        if queue is None or str(ref.queue) != str(queue["sid"]):
            self.log.info(
                "queue_fetch_ignored did=%s queue=%s index=%s cur_sid=%s",
                ref.did,
                ref.queue,
                ref.index,
                self._play_session_id,
            )
            return
        self._ensure_playback_tasks().start(
            TaskKind.QUEUE_FOLLOW,
            self._task_generation(sid=queue["sid"]),
            self._handle_queue_fetch(ref, queue),
            metadata={"sid": queue["sid"], "index": ref.index, "source": ref.source},
        )

    async def _handle_queue_fetch(self, ref, queue: dict) -> None:
        from xiaomusic.playback.command_arbiter import IntentKind

        sid = queue["sid"]
        if self._active_queue() is not queue or queue.get("superseded"):
            return
        names = self._get_playlist_names()

        if ref.source == SOURCE_SILENCE:
            # The window has played out; the speaker is on the sentinel.
            target = ""
            if not queue["stale"] and 0 <= ref.index < len(names):
                target = names[ref.index]
            elif self.device.play_type == PLAY_TYPE_SIN:
                await self.stop(arg1="notts")
                return
            self.log.info(
                "queue_window_exhausted did=%s queue=%s next=%s target=%s",
                self.did,
                sid,
                ref.index,
                target or "<next>",
            )
            self._submit_auto_retry(
                IntentKind.AUTO_NEXT,
                source_token=self._capture_lifecycle_token(),
                sid=sid,
                payload={"target_name": target},
                reason="queue_window_exhausted",
            )
            return

        name = queue["names"].get(ref.index)
        if name is None:
            return
        if name == self.get_cur_music():
            # The dispatched start item, or the speaker starting the current
            # track again (single loop re-fetches on every repeat).
            if self.get_runtime_state().phase == PlaybackPhase.PLAYING:
                self._start_time = time.time()
                # Position is not part of the runtime key: push it so the
                # WebUI progress bar restarts now, not at its next poll.
                self._publish_state_changed()
            return
        if not queue["stale"] and 0 <= ref.index < len(names) and names[ref.index] == name:
            await self._follow_queue_track(ref.index, name, sid)
            return

        # Play mode or list changed after the window was sent: the speaker's
        # next item may not be what the current mode wants next.
        if self.device.play_type == PLAY_TYPE_SIN:
            self.log.info("queue_follow_stale_stop did=%s name=%s", self.did, name)
            await self.stop(arg1="notts")
            return
        wanted = self.get_next_music()
        if wanted == name:
            index = self._find_playlist_index(display_name=name)
            if index >= 0:
                await self._follow_queue_track(index, name, sid)
                return
        self.log.info(
            "queue_follow_stale_resync did=%s fetched=%s wanted=%s", self.did, name, wanted
        )
        if not wanted:
            # Sequential play has reached the end of the list.
            await self.stop(arg1="notts")
            return
        self._submit_auto_retry(
            IntentKind.AUTO_NEXT,
            source_token=self._capture_lifecycle_token(),
            sid=sid,
            payload={"target_name": wanted},
            reason="queue_stale_resync",
        )

    async def _follow_queue_track(self, index: int, name: str, sid: int) -> None:
        """The speaker moved on by itself: make ``name`` the current track.

        Every field the WebUI reads (title, index, start time, duration) is
        set before the runtime state changes, so the one state push it
        triggers already carries the complete new track.
        """
        fetched_at = time.time()
        token = self._capture_lifecycle_token()
        sec = await self.xiaomusic.music_library.get_music_duration(name)
        if sid != self._play_session_id or self._is_lifecycle_token_stale(token):
            return
        state = self.get_runtime_state()
        if state.phase != PlaybackPhase.PLAYING:
            self.log.info(
                "queue_follow_skipped did=%s phase=%s name=%s",
                self.did,
                state.phase.value,
                name,
            )
            return
        item = self._play_list_items[index]
        entity_id = str(item.get("entity_id") or "").strip()
        playlist_item_id = str(item.get("item_id") or "").strip()

        self._set_runtime_track_reference(
            playlist_name=str(self.device.cur_playlist or "").strip(),
            display_name=name,
            entity_id=entity_id,
            playlist_item_id=playlist_item_id,
            current_index=index,
        )
        self._start_time = fetched_at
        # Set directly: _set_duration would push a frame that still carries
        # the previous runtime track.
        self._duration = max(float(sec or 0), 0.0)
        self._set_runtime_state(  # PLAYING -> PLAYING with a new track: pushes
            follow_track(
                state,
                track=TrackReference(
                    entity_id=entity_id,
                    playlist_item_id=playlist_item_id,
                    display_name=name,
                    source="legacy",
                ),
                updated_at=fetched_at,
            )
        )
        self.log.info("queue_follow did=%s queue=%s index=%d name=%s", self.did, sid, index, name)

        if sec <= 0.1:
            self._start_duration_probe(name, sid, token=token)
            return
        elapsed = max(time.time() - self._start_time, 0.0)
        await self.set_next_music_timeout(
            max(sec - TRACK_END_LEAD_SEC - elapsed, 0.1), token=token
        )
        if self.event_bus:
            self.event_bus.publish(DEVICE_CONFIG_CHANGED)

    async def _on_play_type_changed_for_queue(self, play_type: int) -> None:
        """Apply a play mode change to the queue already on the speaker
        without interrupting the current track (ADR-0007 decision 3)."""
        queue = self._active_queue()
        if queue is None:
            return
        queue["stale"] = True
        loop_type = loop_type_for(play_type)
        for device_id in self.xiaomusic.device_manager.get_group_device_id_list(
            self.group_name
        ):
            await self._ensure_loop_type(device_id, loop_type)
        if play_type == PLAY_TYPE_ONE:
            queue["timer"] = "none"  # the speaker now repeats this track
        elif queue["has_successor"] or play_type == PLAY_TYPE_SIN:
            queue["timer"] = "grace"  # it moves on or stops by itself
        else:
            queue["timer"] = "lead"  # one-item list: the server must switch
        self.log.info(
            "queue_play_type_changed did=%s play_type=%s loop_type=%d timer=%s",
            self.did,
            play_type,
            loop_type,
            queue["timer"],
        )
        if queue["timer"] == "none":
            await self.cancel_next_timer()
            return
        duration = float(getattr(self, "_duration", 0) or 0)
        if duration <= 0.1 or not self.is_playing:
            return
        elapsed = max(time.time() - self._start_time, 0.0)
        await self.set_next_music_timeout(max(duration - TRACK_END_LEAD_SEC - elapsed, 0.1))
