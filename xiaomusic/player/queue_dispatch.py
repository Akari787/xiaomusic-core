"""Dispatch to the speaker: windows of tracks (ADR-0007 SpeakerQueue) or a single track.

Mixin of :class:`xiaomusic.device_player.XiaoMusicDevice`; relies on the
attributes it sets up in ``__init__``.
"""

import asyncio
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass
from xiaomusic.const import (
    DEFAULT_AUDIO_ID,
    NEED_USE_PLAY_MUSIC_API,
)
from xiaomusic.speaker_queue import (
    DEFAULT_WINDOW_SIZE,
    LOOP_LIST,
    LOOP_SINGLE,
    plan_window,
)


class QueueDispatchMixin:
    async def group_player_play(
        self,
        url,
        name="",
        *,
        navigation_generation: int | None = None,
    ):
        """同一组设备播放"""
        device_id_list = self.xiaomusic.device_manager.get_group_device_id_list(
            self.group_name
        )
        self._start_fetch = None
        queue = None
        if name:
            try:
                queue = await self._plan_queue_dispatch(url, name)
            except Exception as exc:
                # Planning must never stop playback: send the track alone.
                self.log.warning("queue_plan_failed name=%s error=%r", name, exc)
        if queue is not None:
            payload, loop_type = queue
            results = await asyncio.gather(
                *[
                    self._play_queue_one(
                        device_id,
                        payload,
                        loop_type,
                        navigation_generation=navigation_generation,
                    )
                    for device_id in device_id_list
                ]
            )
            self.log.info(f"group_player_play queue {name} {device_id_list} {results}")
            return results
        self._queue_dispatch = None
        speaker_url = self._speaker_url(url)
        if navigation_generation is None:
            tasks = [
                self.play_one_url(device_id, speaker_url, name)
                for device_id in device_id_list
            ]
        else:
            tasks = [
                self.play_one_url(
                    device_id,
                    speaker_url,
                    name,
                    navigation_generation=navigation_generation,
                )
                for device_id in device_id_list
            ]
        results = await asyncio.gather(*tasks)
        self.log.info(f"group_player_play {url} {device_id_list} {results}")
        return results

    def _speaker_url(self, url):
        """Hand the speaker a stable StreamGateway address (ADR-0007).

        Callers keep working with the original URL (fallback decisions,
        logs); only the dispatched address changes. Unhandled URLs (relay,
        radio, m3u8) pass through unchanged.
        """
        gateway = getattr(self.xiaomusic, "stream_gateway", None)
        if gateway is None:
            return url
        try:
            speaker_url = gateway.wrap(
                url,
                did=self.did,
                queue=str(self._play_session_id),
                index=max(int(self._current_index), 0),
            )
        except Exception as exc:
            self.log.warning("stream_gateway wrap failed: %s", type(exc).__name__)
            return url
        if speaker_url != url:
            self._expect_start_fetch(
                self._play_session_id,
                str(self._play_session_id),
                max(int(self._current_index), 0),
            )
            self.log.info(
                "stream_gateway dispatch did=%s queue=%s index=%s",
                self.did,
                self._play_session_id,
                max(int(self._current_index), 0),
            )
        return speaker_url

    def _supersede_queue(self, *, reason: str) -> None:
        """A user command was accepted: the window on the speaker no longer
        decides what plays next. Its fetches are ignored from now on, so a
        sentinel or local advance cannot trigger an automatic dispatch while
        the user's command waits to run (ADR-0008)."""
        queue = getattr(self, "_queue_dispatch", None)
        if queue and not queue.get("superseded"):
            queue["superseded"] = True
            self.log.info(
                "queue_superseded did=%s queue=%s reason=%s", self.did, queue.get("sid"), reason
            )

    def _active_queue(self) -> dict | None:
        """The dispatched window if it belongs to the current play session."""
        queue = getattr(self, "_queue_dispatch", None)
        if not queue or queue.get("sid") != self._play_session_id:
            return None
        return queue

    def _external_queue_name(self) -> str:
        """Current playlist item after a v1 API play bootstrapped a playlist
        session, so that play goes through queue dispatch as well
        (ADR-0007 step 5). "" for a one-off URL without playlist context:
        on_external_url_play clears the session before bootstrapping."""
        names = self._get_playlist_names()
        index = self._current_index
        if 0 <= index < len(names) and names[index] == self.get_cur_music():
            return names[index]
        return ""

    async def _queue_item_url(self, name: str) -> str:
        """URL for a non-current window item, or "" to end the window there.

        Songs resolved through a music API need a fresh address at play
        time, so they are left to a later dispatch.
        """
        library = self.xiaomusic.music_library
        try:
            if library.is_web_music(name) and library.is_need_use_play_music_api(name):
                return ""
            url, _origin = await library.get_music_url(name)
        except Exception as exc:
            self.log.warning("queue_item_url_failed name=%s error=%s", name, type(exc).__name__)
            return ""
        return str(url or "")

    async def _plan_queue_dispatch(self, url: str, name: str):
        """Build a window starting at ``name`` (the current playlist item).

        Returns ``(payload, loop_type)``, or None to dispatch ``url`` alone:
        hardware without the music API, queue dispatch disabled, no gateway,
        ``name`` not the current playlist item, or a current URL the gateway
        does not handle.
        """
        try:
            size = int(
                getattr(getattr(self, "config", None), "queue_window_size", DEFAULT_WINDOW_SIZE)
            )
        except (TypeError, ValueError):
            size = DEFAULT_WINDOW_SIZE
        if size <= 0 or self._resolve_play_url_dispatch_mode() != "play_by_music_url":
            return None
        gateway = getattr(self.xiaomusic, "stream_gateway", None)
        if gateway is None:
            return None
        names = self._get_playlist_names()
        index = self._current_index
        if not 0 <= index < len(names) or names[index] != name:
            return None

        window = plan_window(self.device.play_type, len(names), index, size)
        sid = self._play_session_id
        queue_id = str(sid)
        entries: list[tuple[str, str]] = []
        snapshot: dict[int, str] = {}
        sentinel_next = window.sentinel_next
        for i in window.indices:
            item_url = url if i == index else await self._queue_item_url(names[i])
            wrapped = (
                gateway.wrap(item_url, did=self.did, queue=queue_id, index=i)
                if item_url
                else ""
            )
            if i == index and (not wrapped or wrapped == item_url):
                # The v1 API hands over a delivery URL (/api/proxy/plugin-url)
                # the gateway does not parse; the playlist item's own address
                # is the same track and is served by the gateway.
                item_url = await self._queue_item_url(names[i])
                wrapped = (
                    gateway.wrap(item_url, did=self.did, queue=queue_id, index=i)
                    if item_url
                    else ""
                )
            if not wrapped or wrapped == item_url:
                if i == index:
                    return None
                # Not served by the gateway: no fetch would mark it, so the
                # window ends here and the next dispatch starts with it.
                sentinel_next = i
                break
            entries.append((names[i], wrapped))
            snapshot[i] = names[i]
        if sid != self._play_session_id:
            return None
        if sentinel_next is not None:
            entries.append(("", gateway.sentinel_url(self.did, queue_id, sentinel_next)))

        # A one-item list under repeat replays from the speaker's buffer
        # without a new fetch, so a fallback timer would only cut it short.
        loops_alone = len(entries) == 1 and window.loop_type in (LOOP_SINGLE, LOOP_LIST)
        self._queue_dispatch = {
            "sid": sid,
            "names": snapshot,
            "sentinel_next": sentinel_next,
            "loop_type": window.loop_type,
            "has_successor": len(entries) > 1,
            "stale": False,
            # "grace": the speaker moves on by itself, the timer is a late
            # fallback; "none": it repeats from its buffer (no fetch), no
            # timer; "lead": the server must switch (after a mode change).
            "timer": "none" if loops_alone else "grace",
        }
        self._expect_start_fetch(sid, queue_id, index)
        audio_id = await self._get_audio_id(name)
        payload = self._build_music_queue_payload(entries, str(audio_id), start_index=0)
        self.log.info(
            "queue_dispatch did=%s queue=%s start=%d items=%d sentinel_next=%s loop_type=%d",
            self.did,
            queue_id,
            index,
            len(snapshot),
            sentinel_next,
            window.loop_type,
        )
        return payload, window.loop_type

    async def _ensure_loop_type(self, device_id: str, loop_type: int) -> None:
        sent = getattr(self, "_loop_type_sent", None)
        if sent is None:
            sent = self._loop_type_sent = {}
        if sent.get(device_id) == loop_type:
            return
        try:
            await self.speaker.set_loop(device_id, loop_type)
            sent[device_id] = loop_type
            self.log.info("set_loop device_id=%s loop_type=%d", device_id, loop_type)
        except Exception as exc:
            sent.pop(device_id, None)
            self.log.warning("set_loop_failed device_id=%s error=%s", device_id, exc)

    async def _play_queue_one(
        self,
        device_id,
        payload: dict,
        loop_type: int,
        *,
        navigation_generation: int | None = None,
    ):
        """Dispatch a window to one device; None on failure (like play_one_url)."""
        ret = None
        try:
            if not self._manual_navigation_is_current(navigation_generation):
                self.log.info(
                    "manual_nav_stale generation=%s stage=before_queue_dispatch",
                    navigation_generation,
                )
                return None
            await self._ensure_loop_type(device_id, loop_type)
            ret = await self.speaker.play_queue(device_id, payload)
            self.log.info("play_queue device_id=%s ret=%s", device_id, ret)
        except Exception as e:
            self.log.exception(f"Execption {e}")
        return ret

    def _resolve_play_url_dispatch_mode(self) -> str:
        if self.hardware in NEED_USE_PLAY_MUSIC_API:
            return "play_by_music_url"
        return "play_by_url"

    async def play_one_url(
        self,
        device_id,
        url,
        name,
        *,
        navigation_generation: int | None = None,
    ):
        """在单个设备上播放URL"""
        ret = None
        try:
            audio_id = await self._get_audio_id(name)
            if not self._manual_navigation_is_current(navigation_generation):
                self.log.info(
                    "manual_nav_stale generation=%s stage=before_device_dispatch target=%s",
                    navigation_generation,
                    name,
                )
                return None
            dispatch_mode = self._resolve_play_url_dispatch_mode()
            self.log.info(
                "play_one_url dispatch_mode=%s hardware=%s device_id=%s",
                dispatch_mode,
                self.hardware,
                device_id,
            )
            if dispatch_mode == "play_by_music_url":
                ret = await self.speaker.play_music_url(device_id, url, audio_id=audio_id)
                self.log.info(
                    f"play_one_url play_by_music_url device_id:{device_id} ret:{ret} url:{url} audio_id:{audio_id}"
                )
            else:
                ret = await self.speaker.play_url(device_id, url)
                self.log.info(
                    f"play_one_url play_by_url device_id:{device_id} ret:{ret} url:{url}"
                )
        except Exception as e:
            self.log.exception(f"Execption {e}")
        return ret

    @staticmethod
    def _build_music_queue_payload(
        entries: list[tuple[str, str]],
        first_audio_id: str,
        start_index: int = 0,
        play_behavior: str = "REPLACE_ALL",
    ) -> dict:
        """player_play_music payload with several audio items (same shape as
        miservice play_by_music_url, which only ever sends one). Item i gets
        audio_id first_audio_id + i; start_index selects startaudioid.
        Verified on OH2P: the speaker advances through the items locally and
        honours startaudioid (ADR-0007)."""
        base = int(first_audio_id) if str(first_audio_id).isdigit() else int(DEFAULT_AUDIO_ID)
        audio_items = [
            {
                "item_id": {
                    "audio_id": str(base + i),
                    "cp": {"album_id": "-1", "episode_index": 0, "id": "355454500", "name": "xiaowei"},
                },
                "stream": {"url": url},
            }
            for i, (_name, url) in enumerate(entries)
        ]
        music = {
            "payload": {
                "audio_type": "",
                "audio_items": audio_items,
                "list_params": {"listId": "-1", "loadmore_offset": 0, "origin": "xiaowei", "type": "MUSIC"},
            },
            "play_behavior": play_behavior,
        }
        start = min(max(int(start_index), 0), max(len(audio_items) - 1, 0))
        return {"startaudioid": str(base + start), "music": json.dumps(music)}

    async def _get_audio_id(self, name):
        """Audio id sent with play_by_music_url. The speaker only needs a
        stable id; it plays the stream URL, not the catalogue entry."""
        return DEFAULT_AUDIO_ID
