"""Failed play attempts: retry, advance, degrade or stop.

Mixin of :class:`xiaomusic.device_player.XiaoMusicDevice`; relies on the
attributes it sets up in ``__init__``.
"""

import asyncio
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass
from xiaomusic.const import (
    PLAY_TYPE_SIN,
)
from xiaomusic.playback.completion_policy import (
    FailureAction,
    decide_failure_action,
)
from xiaomusic.playback.runtime_state import (
    LifecycleToken,
    PlaybackPhase,
    TransitionError,
)
from xiaomusic.playback.task_registry import (
    TaskKind,
)


class FailurePolicyMixin:
    @staticmethod
    def _physical_result_succeeded(result) -> bool:
        if isinstance(result, bool):
            return result
        if result is None:
            return False
        if isinstance(result, list):
            return bool(result) and all(
                FailurePolicyMixin._physical_result_succeeded(item) for item in result
            )
        if isinstance(result, dict):
            accepted = result.get("accepted")
            if isinstance(accepted, bool):
                return accepted
            if "ret" in result:
                ret = result["ret"]
                if isinstance(ret, bool):
                    return ret
                if isinstance(ret, (int, float)):
                    return ret == 0
                return False
            if "code" in result:
                if result["code"] != 0:
                    return False
                data = result.get("data")
                if isinstance(data, dict) and "code" in data:
                    return data["code"] == 0
                return True
        return False

    def _report_executor_failure(
        self, *, reason: str, sid: int, token: LifecycleToken, barrier: bool = False
    ) -> None:
        if sid != self._play_session_id:
            return
        if self._is_barrier_stale(sid, token) if barrier else self._is_lifecycle_token_stale(token):
            return
        try:
            self._report_runtime_failure(reason=reason, updated_at=time.time())
        except TransitionError:
            self.log.info("executor_failure_ignored phase=%s reason=%s", self.get_runtime_state().phase.value, reason)

    def _cancel_failure_retry_task(self, *, wait: bool = False):
        task = self._ensure_playback_tasks().get_task(TaskKind.FAILURE_RETRY)
        if task is None or task.done() or task is asyncio.current_task():
            return None
        self._ensure_playback_tasks().cancel(TaskKind.FAILURE_RETRY)
        self._failure_retry_last_status = "cancelled"
        if wait:
            return task
        return None

    async def _await_cancelled_failure_retry(self):
        task = self._cancel_failure_retry_task(wait=True)
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                self._failure_retry_last_error = str(exc)[:200]
                self.log.error("failure_retry_cancelled_task_error=%s", exc)

    async def _wait_failure_retry_backoff(self, delay: float) -> None:
        await asyncio.sleep(delay)

    def _token_to_safe_dict(self, token) -> dict:
        if token is None:
            return {}
        try:
            return {
                "queue_session_id": getattr(token, "queue_session_id", None),
                "command_generation": getattr(token, "command_generation", None),
                "track_attempt_id": getattr(token, "track_attempt_id", None),
            }
        except Exception:
            return {}

    def get_failure_retry_status(self) -> dict:
        task = getattr(self, "_failure_retry_task", None)
        last = getattr(self, "_failure_retry_last_status", "idle")
        if task is None:
            status = last if last in {"done", "cancelled"} else "idle"
        elif task.cancelled():
            status = "cancelled"
        elif task.done():
            status = "done"
        elif last == "running":
            status = "running"
        else:
            status = "pending"
        active = task is not None and not task.done()
        meta = getattr(self, "_failure_retry_meta", {}) or {}
        raw_token = meta.get("token")
        return {
            "status": status,
            "active": active,
            "action": meta.get("action", ""),
            "count": meta.get("count", 0),
            "reason": meta.get("reason", ""),
            "sid": meta.get("sid"),
            "token": self._token_to_safe_dict(raw_token),
            "last_error": getattr(self, "_failure_retry_last_error", ""),
        }

    def failure_retry_snapshot(self) -> dict:
        return self.get_failure_retry_status()

    def _clear_degraded_state(self):
        self._cancel_failure_retry_task()
        self._failure_retry_meta = {}
        self._failure_retry_last_status = "idle"
        self._degraded = False
        self._degraded_notified = False
        self._play_failed_cnt = 0
        self._play_fail_first_ts = 0.0
        self._play_fail_last_reason = ""

    async def _handle_play_failure(
        self,
        *,
        name: str,
        sid: int,
        reason: str,
        token: LifecycleToken,
    ):
        if self._is_lifecycle_token_stale(token) or sid != self._play_session_id:
            return
        state = self.get_runtime_state()
        if state.phase not in {
            PlaybackPhase.RESOLVING, PlaybackPhase.SWITCHING,
            PlaybackPhase.DISPATCHING, PlaybackPhase.CONFIRMING,
            PlaybackPhase.PLAYING, PlaybackPhase.FAILED,
        }:
            return

        now = time.time()
        if self._play_fail_first_ts <= 0:
            self._play_fail_first_ts = now
        new_count = self._play_failed_cnt + 1
        total_elapsed = now - self._play_fail_first_ts
        decision = decide_failure_action(
            new_count,
            total_elapsed,
            getattr(getattr(self, "device", None), "play_type", None) == PLAY_TYPE_SIN,
            reason=reason,
        )
        try:
            self._report_runtime_failure(
                reason=reason,
                degraded=decision.action == FailureAction.DEGRADED,
                updated_at=now,
            )
        except TransitionError:
            return
        self._play_failed_cnt = new_count
        self._play_fail_last_reason = reason
        self._failure_retry_meta = {
            "action": decision.action.value, "count": new_count,
            "reason": reason, "sid": sid, "token": token,
        }
        logger = getattr(self, "log", None)
        if logger is not None:
            logger.info("播放 %s 失败. reason=%s cnt=%d action=%s", name, reason, new_count, decision.action.value)

        if decision.action == FailureAction.DEGRADED:
            self._degraded = True
            await self._await_cancelled_failure_retry()
            if not self._degraded_notified:
                self._degraded_notified = True
                self.log.warning("播放失败过多，已停止自动切歌")
                if sid != self._play_session_id or self._is_lifecycle_token_stale(token):
                    return
            return
        if decision.action == FailureAction.STOP:
            await self._await_cancelled_failure_retry()
            if sid == self._play_session_id and not self._is_lifecycle_token_stale(token):
                await self.stop(arg1="notts")
            return

        await self._await_cancelled_failure_retry()
        from xiaomusic.playback.command_arbiter import IntentKind

        evt = asyncio.Event()
        self._failure_retry_done_event = evt

        async def _retry_runner():
            self._failure_retry_last_status = "running"
            await self._wait_failure_retry_backoff(decision.delay)
            if sid != self._play_session_id or self._is_lifecycle_token_stale(token):
                return
            runtime_phase = self.get_runtime_state().phase
            if runtime_phase in {
                PlaybackPhase.IDLE,
                PlaybackPhase.STOPPING,
                PlaybackPhase.STOPPED,
            } or self._degraded:
                return
            if reason in self._FAILURE_REASONS_WITHOUT_DISPATCH:
                # The new track was never sent, so "speaker is playing" can
                # only mean the previous track (or the speaker looping it).
                # Skipping the retry here would strand playback.
                is_playing = False
            else:
                try:
                    is_playing = await self.get_if_xiaoai_is_playing()
                except Exception:
                    is_playing = False
            if sid != self._play_session_id or self._is_lifecycle_token_stale(token):
                return
            if is_playing:
                self.log.info("retry_skip_speaker_already_playing(session_id=%s, name=%s)", sid, name)
                return
            self._submit_auto_retry(
                IntentKind.RETRY,
                source_token=token,
                sid=sid,
                payload={
                    "name": name,
                    "retry_same_song": decision.action == FailureAction.RETRY_SAME,
                },
                reason=reason,
            )

        task = self._ensure_playback_tasks().start(
            TaskKind.FAILURE_RETRY,
            self._task_generation(token, sid=sid),
            _retry_runner(),
            metadata={
                "action": decision.action.value,
                "count": new_count,
                "reason": reason,
                "sid": sid,
            },
        )
        self._failure_retry_last_status = "pending"

        def _consume_retry_done(done_task):
            if done_task.cancelled():
                self._failure_retry_last_status = "cancelled"
            else:
                exc_info = None
                try:
                    exc_info = done_task.exception()
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
                if exc_info is not None:
                    self._failure_retry_last_error = str(exc_info)[:200]
                    self._failure_retry_last_status = "done"
                    logger = getattr(self, "log", None)
                    if logger is not None:
                        logger.error("failure_retry_task_error=%s", exc_info)
                else:
                    self._failure_retry_last_status = "done"
            if getattr(self, "_failure_retry_task", None) is done_task:
                self._failure_retry_task = None
            evt.set()

        if task is not None:
            task.add_done_callback(_consume_retry_done)
