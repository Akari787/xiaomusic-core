"""Per-device command arbiter, its executors and manual next/previous.

Mixin of :class:`xiaomusic.device_player.XiaoMusicDevice`; relies on the
attributes it sets up in ``__init__``.
"""

import asyncio
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass
from xiaomusic.const import (
    PLAY_TYPE_SEQ,
)
from xiaomusic.playback.runtime_state import (
    LifecycleToken,
)


class CommandArbiterMixin:
    def _get_or_create_arbiter(self):
        """Lazy-create the per-device command arbiter on first manual intent.

        Must be called from the owning event loop.
        """
        if self._command_arbiter is None:
            from xiaomusic.playback.command_arbiter import DeviceCommandArbiter

            self._command_arbiter = DeviceCommandArbiter(self._arbiter_executor)
        return self._command_arbiter

    def _submit_auto_retry(
        self,
        kind,
        *,
        source_token,
        sid: int,
        payload: dict | None = None,
        reason: str = "",
    ) -> bool:
        """Submit an AUTO_NEXT or RETRY intent via the command arbiter.

        Guards:
        - sid must match ``_play_session_id``.
        - source_token must be strict-current (``_is_lifecycle_token_stale``).

        After a successful submit, ``_accept_command`` bumps c by 1, making
        the source_token naturally strict-stale for any subsequent calls —
        no explicit dedup set needed.  On failure (guard, ArbiterClosedError)
        c is unchanged, allowing retry.

        The expected accepted token (same q, c+1, same a) is constructed from
        the *current* runtime state and passed inside the payload for the
        executor to guard against.  ``_accept_command`` is called synchronously
        **after** a successful submit (no yield), so the actual state matches
        the expected token by the time the executor runs.

        Zero lifecycle writes on submit failure: c/q/sid/attempt/phase
        unchanged.
        """
        # ── Pre-guards ─────────────────────────────────────────────
        if sid != self._play_session_id:
            self.log.info(
                "auto_retry_submit_sid_mismatch kind=%s source_sid=%s cur_sid=%s",
                kind,
                sid,
                self._play_session_id,
            )
            return False

        if self._is_lifecycle_token_stale(source_token):
            self.log.info(
                "auto_retry_submit_token_stale kind=%s",
                kind,
            )
            return False

        # ── Construct expected accepted token ───────────────────────
        current = self.get_runtime_state()
        expected_accepted_token = LifecycleToken(
            queue_session_id=current.queue_session_id,
            command_generation=current.command_generation + 1,
            track_attempt_id=current.track_attempt_id,
        )

        # ── Submit to arbiter ───────────────────────────────────────
        from xiaomusic.playback.command_arbiter import ArbiterClosedError

        arbiter = self._get_or_create_arbiter()
        submit_payload: dict = {
            "sid": sid,
            "accepted_token": expected_accepted_token,
            "reason": reason,
        }
        if payload:
            submit_payload.update(payload)

        try:
            receipt = arbiter.submit(kind, submit_payload)
        except ArbiterClosedError:
            self.log.info(
                "auto_retry_submit_arbiter_closed kind=%s",
                kind,
            )
            return False
        if not receipt.accepted:
            # A user command is waiting; it decides what plays next (ADR-0008).
            self.log.info(
                "auto_retry_submit_refused_user_intent_waiting kind=%s reason=%s",
                kind,
                reason,
            )
            return False

        # ── Only on success: accept command (bump c) ────────────────
        self._accept_command(updated_at=time.time())

        self.log.info(
            "auto_retry_submitted kind=%s source_sid=%s reason=%s",
            kind,
            sid,
            reason,
        )
        return True

    async def _arbiter_executor(self, intent) -> None:
        """Serial executor for DeviceCommandArbiter.

        Routes to the appropriate private physical method:
        - STOP  → _execute_stop_intent
        - PLAY  → _execute_play_intent
        - NEXT / PREVIOUS → manual navigation dispatch

        Exceptions propagate to arbiter.last_error; no fake completion.
        """
        from xiaomusic.playback.command_arbiter import IntentKind

        if intent.kind == IntentKind.STOP:
            await self._execute_stop_intent(intent.payload or {})
            return
        if intent.kind == IntentKind.PLAY:
            await self._execute_play_intent(intent.payload or {})
            return
        if intent.kind == IntentKind.AUTO_NEXT:
            await self._execute_auto_next_intent(intent)
            return
        if intent.kind == IntentKind.RETRY:
            await self._execute_retry_intent(intent)
            return
        if intent.kind not in (IntentKind.NEXT, IntentKind.PREVIOUS):
            return

        payload = intent.payload or {}
        generation = payload.get("generation")
        name = payload.get("target")

        await self._wait_manual_navigation_settle()

        arbiter = self._command_arbiter
        if arbiter is not None:
            pending_seq = arbiter.pending_sequence
            if pending_seq is not None and pending_seq > intent.sequence:
                self.log.info(
                    "arbiter_executor_skip_newer_pending seq=%d pending=%d",
                    intent.sequence,
                    pending_seq,
                )
                return

        if not self._manual_navigation_is_current(generation):
            self.log.info(
                "arbiter_executor_skip_stale_generation generation=%s",
                generation,
            )
            return

        self.log.info(
            "arbiter_executor_dispatch sequence=%d generation=%s target=%s",
            intent.sequence,
            generation,
            name,
        )
        await self._play(
            name,
            preserve_playlist=True,
            confirm_start_in_background=True,
            fast_stop=True,
            navigation_generation=generation,
            command_already_accepted=True,
        )

    async def _execute_stop_intent(self, payload: dict) -> None:
        """Physical stop work. Only called by arbiter executor.

        Uses barrier guard (_is_barrier_stale) which checks sid,
        queue_session_id, and track_attempt_id — but NOT command_generation
        (so after-barrier regular intents don't kill the barrier).
        Exceptions propagate to arbiter.last_error — no fake completion.
        """
        sid = payload.get("sid")
        token = payload.get("token")

        # Initial guard: barrier stale → no-op
        if self._is_barrier_stale(sid, token):
            return

        # 取消组内所有的下一首歌曲的定时器
        await self.cancel_group_next_timer()
        if self._is_barrier_stale(sid, token):
            return

        # 强制停止组内所有设备
        try:
            results = await self.group_force_stop_xiaoai()
            if not self._group_stop_result_succeeded(results):
                raise RuntimeError("group stop returned a non-success result")
        except Exception:
            self._report_executor_failure(
                reason="stop_not_confirmed", sid=sid, token=token, barrier=True
            )
            raise
        if self._is_barrier_stale(sid, token):
            return

        # Only current: complete stop → STOPPED; the runtime boundary emits it.
        self._complete_runtime_stop(updated_at=time.time())
        self.log.info("stop now")

    async def _execute_play_intent(self, payload: dict) -> None:
        """Physical play work. Only called by arbiter executor.

        Routes by mode:
        - "external"  → _execute_external_play_intent
        - "playlocal" → _play_internal(name, allow_download=False,
                                        command_already_accepted=True)
        - "play"      → _play(name, search, command_already_accepted=True)

        command_generation was already bumped by the public accept API;
        command_already_accepted=True prevents a second bump.
        Exceptions propagate to arbiter.last_error — no fake completion.
        """
        mode = payload.get("mode", "play")
        if mode == "external":
            await self._execute_external_play_intent(payload)
            return

        name = payload.get("name", "")
        search = payload.get("search", "")

        if mode == "playlocal":
            await self._play_internal(
                name=name,
                search_key=search,
                allow_download=False,
                command_already_accepted=True,
            )
        else:
            await self._play(
                name=name,
                search_key=search,
                command_already_accepted=True,
            )

    async def _wait_manual_navigation_settle(self) -> None:
        """0.2 s settle window for fast-click burst merging.

        Replacing the old ``_manual_navigation_worker`` sleep-before-dispatch
        pattern.  Used exclusively by the arbiter executor.

        Overridable in tests (patch to instant Event or zero-sleep) so that
        burst-merge behaviour can be verified deterministically.
        """
        await asyncio.sleep(0.2)

    async def _execute_auto_next_intent(self, intent) -> None:
        """Execute AUTO_NEXT: guard accepted_token strict, then _play_next.

        Only physical work happens here — single concurrency per arbiter.
        """
        payload = intent.payload or {}
        sid = payload.get("sid")
        accepted_token: LifecycleToken | None = payload.get("accepted_token")

        # Strict guard: sid must match.
        if sid != self._play_session_id:
            self.log.info(
                "auto_next_executor_sid_mismatch seq=%d intent_sid=%s cur_sid=%s",
                intent.sequence,
                sid,
                self._play_session_id,
            )
            return

        # Strict guard: accepted token must exactly match current state.
        if accepted_token is None:
            self.log.info(
                "auto_next_executor_no_accepted_token seq=%d",
                intent.sequence,
            )
            return

        current = self.get_runtime_state()
        if (
            current.queue_session_id != accepted_token.queue_session_id
            or current.command_generation != accepted_token.command_generation
            or current.track_attempt_id != accepted_token.track_attempt_id
        ):
            self.log.info(
                "auto_next_executor_token_mismatch seq=%d "
                "expected(q=%d,c=%d,a=%d) actual(q=%d,c=%d,a=%d)",
                intent.sequence,
                accepted_token.queue_session_id,
                accepted_token.command_generation,
                accepted_token.track_attempt_id,
                current.queue_session_id,
                current.command_generation,
                current.track_attempt_id,
            )
            return

        self.log.info(
            "auto_next_executor_dispatch seq=%d reason=%s",
            intent.sequence,
            payload.get("reason", ""),
        )
        target = str(payload.get("target_name") or "")
        if target:
            await self._play_next(command_already_accepted=True, target_name=target)
        else:
            await self._play_next(command_already_accepted=True)

    async def _execute_retry_intent(self, intent) -> None:
        """Execute RETRY: guard accepted_token strict, route same-song vs next.

        Only physical work happens here — single concurrency per arbiter.
        """
        payload = intent.payload or {}
        sid = payload.get("sid")
        accepted_token: LifecycleToken | None = payload.get("accepted_token")
        name: str = payload.get("name", "")
        retry_same_song: bool = payload.get("retry_same_song", False)

        # Strict guard: sid must match.
        if sid != self._play_session_id:
            self.log.info(
                "retry_executor_sid_mismatch seq=%d intent_sid=%s cur_sid=%s",
                intent.sequence,
                sid,
                self._play_session_id,
            )
            return

        # Strict guard: accepted token must exactly match current state.
        if accepted_token is None:
            self.log.info(
                "retry_executor_no_accepted_token seq=%d",
                intent.sequence,
            )
            return

        current = self.get_runtime_state()
        if (
            current.queue_session_id != accepted_token.queue_session_id
            or current.command_generation != accepted_token.command_generation
            or current.track_attempt_id != accepted_token.track_attempt_id
        ):
            self.log.info(
                "retry_executor_token_mismatch seq=%d "
                "expected(q=%d,c=%d,a=%d) actual(q=%d,c=%d,a=%d)",
                intent.sequence,
                accepted_token.queue_session_id,
                accepted_token.command_generation,
                accepted_token.track_attempt_id,
                current.queue_session_id,
                current.command_generation,
                current.track_attempt_id,
            )
            return

        if retry_same_song and name:
            self.log.info(
                "retry_executor_same_song seq=%d name=%s",
                intent.sequence,
                name,
            )
            await self._play(
                name,
                preserve_playlist=True,
                confirm_start_in_background=False,
                fast_stop=False,
                command_already_accepted=True,
            )
        else:
            self.log.info(
                "retry_executor_next seq=%d reason=%s",
                intent.sequence,
                payload.get("reason", ""),
            )
            await self._play_next(command_already_accepted=True)

    async def close_command_arbiter(self) -> None:
        """Close all Device playback tasks, then the independent arbiter worker."""
        await self._ensure_playback_tasks().close()
        arb = self._command_arbiter
        if arb is not None:
            await arb.close()
            self._command_arbiter = None

    async def _queue_manual_navigation(self, *, direction: str) -> bool:
        self._ensure_manual_navigation_state()
        async with self._manual_nav_lock:
            if direction == "next":
                name = self.get_next_music(skip_one_repeat=True)
            else:
                name = self.get_prev_music()
            if not name:
                return False
            # Accept the control intent — bumps command_generation.
            self._accept_command(updated_at=time.time())
            self._supersede_queue(reason=f"manual_{direction}")
            self._stage_playlist_navigation_transition(
                name,
                reason="play_next" if direction == "next" else "play_prev",
            )
            self._manual_nav_generation += 1
            generation = self._manual_nav_generation
            self._manual_nav_target = (generation, name)
            self.log.info(
                "manual_nav_intent generation=%d direction=%s index=%d target=%s",
                generation,
                direction,
                self._current_index,
                name,
            )
            from xiaomusic.playback.command_arbiter import IntentKind

            arbiter = self._get_or_create_arbiter()
            arbiter.submit(
                IntentKind.NEXT if direction == "next" else IntentKind.PREVIOUS,
                payload={
                    "generation": generation,
                    "target": name,
                    "direction": direction,
                },
            )
        return True

    async def play_next(self):
        """Queue a manual next intent and return after it is accepted."""
        return await self._queue_manual_navigation(direction="next")

    def _stage_playlist_navigation_transition(self, name: str, *, reason: str) -> None:
        target = str(name or "").strip()
        if not target:
            return
        self.is_playing = False
        self._start_time = 0
        self._duration = 0
        self._last_cmd = reason
        self._set_runtime_track_reference(
            playlist_name=str(getattr(self.device, "cur_playlist", "") or ""),
            display_name=target,
        )

    async def _play_next(
        self,
        manual: bool = False,
        command_already_accepted: bool = False,
        target_name: str = "",
    ):
        """播放下一首（内部实现）

        manual=True（用户主动点击）：单曲循环模式下前进到下一首
        manual=False（自动切歌）：单曲循环模式下重复当前歌曲

        始终 preserve_playlist=True：当前 _play_list_items 是会话权威快照，
        自动/手动切歌都不应触发 find_cur_playlist/update_playlist 重新打乱。

        command_already_accepted: True when command was already accepted
            upstream.  False (default) preserves legacy behavior.
        target_name: play this track instead of computing the next one
            (queue dispatch: the next window starts at a known track).
        """
        if not command_already_accepted:
            self._accept_command(updated_at=time.time())
        self.log.info("开始播放下一首")
        name = target_name or self.get_next_music(skip_one_repeat=manual)
        self.log.info(f"get_next_music {name}")
        self.log.info(f"_play_next. name:{name}, cur_music:{self.get_cur_music()}")
        if name == "":
            self.log.info("本地没有歌曲")
            if not manual and self.device.play_type == PLAY_TYPE_SEQ:
                # Sequential play reached the end of the list. Stop the speaker
                # explicitly, as single-play mode does; otherwise the speaker
                # keeps looping its one-item queue while the server believes
                # playback is still running.
                self.log.info("顺序播放结束，停止播放")
                await self.stop(arg1="notts")
            return False
        self._stage_playlist_navigation_transition(name, reason="play_next")
        return await self._play(
            name,
            preserve_playlist=True,
            confirm_start_in_background=not manual,
            fast_stop=not manual,
            command_already_accepted=True,
            auto_advance=not manual,
        )

    async def play_prev(self):
        """Queue a manual previous intent and return after it is accepted."""
        return await self._queue_manual_navigation(direction="previous")

    async def _play_prev(self, manual: bool = False):
        """播放上一首（内部实现）"""
        self.log.info("开始播放上一首")
        name = self.get_cur_music()
        # manual=True（用户主动点击）: 切到上一首
        # manual=False（自动触发）: 仅当当前歌曲无效时才切到上一首，否则重启当前歌曲
        if manual or name == "" or (self._find_playlist_index(display_name=name) < 0):
            name = self.get_prev_music()
        self.log.info(f"_play_prev. name:{name}, cur_music:{self.get_cur_music()}")
        if name == "":
            self.log.info("本地没有歌曲")
            return False
        self._stage_playlist_navigation_transition(name, reason="play_prev")
        return await self._play(
            name,
            preserve_playlist=True,
            confirm_start_in_background=not manual,
            fast_stop=not manual,
        )
