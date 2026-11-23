"""Runtime state, lifecycle tokens, task registry and manual-navigation generation.

Mixin of :class:`xiaomusic.device_player.XiaoMusicDevice`; relies on the
attributes it sets up in ``__init__``.
"""

import asyncio
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass
from xiaomusic.events import PLAYER_STATE_CHANGED
from xiaomusic.playback.runtime_state import (
    LifecycleToken,
    PlaybackPhase,
    PlaybackRuntimeState,
    TrackReference,
    TransitionError,
    begin_confirm,
    begin_play_dispatch,
    begin_play_request,
    begin_stop,
    begin_track_attempt,
    capture_token,
    check_stale_attempt,
    check_stale_queue,
    check_stale_strict,
    complete_stop,
    confirm_playing,
    new_queue_session,
    next_command,
    report_failure,
)
from xiaomusic.playback.task_registry import (
    ATTEMPT_SCOPED_KINDS,
    SESSION_SCOPED_KINDS,
    PlaybackTaskRegistry,
    TaskGeneration,
    TaskKind,
)


class PlaybackLifecycleMixin:
    def _ensure_playback_tasks(self) -> PlaybackTaskRegistry:
        registry = getattr(self, "_playback_tasks", None)
        if registry is None:
            registry = PlaybackTaskRegistry()
            self._playback_tasks = registry
        return registry

    def _task_generation(self, token=None, *, sid: int | None = None) -> TaskGeneration:
        if token is None:
            token = self._capture_lifecycle_token()
        return TaskGeneration.from_token(token, session_id=self._play_session_id if sid is None else sid)

    def _task_ref(self, kind: TaskKind):
        legacy = getattr(self, "_legacy_task_values", {})
        if kind in legacy:
            return legacy[kind]
        return self._ensure_playback_tasks().get_task(kind)

    def _set_task_compat(self, kind: TaskKind, task) -> None:
        legacy = getattr(self, "_legacy_task_values", None)
        if legacy is None:
            legacy = {}
            self._legacy_task_values = legacy
        if task is None:
            legacy.pop(kind, None)
            # Cancel the active registry task for this kind (respects self-cancel guard).
            registry = getattr(self, "_playback_tasks", None)
            if registry is not None:
                registry.cancel(kind)
        elif isinstance(task, asyncio.Task):
            legacy.pop(kind, None)
            self._ensure_playback_tasks().adopt(kind, TaskGeneration(), task)
        else:
            # Historical tests use opaque sentinels to model an old reference;
            # keep that non-task marker without making it a task owner.
            legacy[kind] = task

    @property
    def _duration_probe_task(self):
        return self._task_ref(TaskKind.DURATION_PROBE)

    @_duration_probe_task.setter
    def _duration_probe_task(self, task):
        self._set_task_compat(TaskKind.DURATION_PROBE, task)

    @property
    def _playback_confirm_task(self):
        return self._task_ref(TaskKind.PLAYBACK_CONFIRMATION)

    @_playback_confirm_task.setter
    def _playback_confirm_task(self, task):
        self._set_task_compat(TaskKind.PLAYBACK_CONFIRMATION, task)

    @property
    def _playback_status_probe_task(self):
        return self._task_ref(TaskKind.STATUS_PROBE)

    @_playback_status_probe_task.setter
    def _playback_status_probe_task(self, task):
        self._set_task_compat(TaskKind.STATUS_PROBE, task)

    @property
    def _next_timer(self):
        return self._task_ref(TaskKind.COMPLETION_NEXT_TIMER)

    @_next_timer.setter
    def _next_timer(self, task):
        self._set_task_compat(TaskKind.COMPLETION_NEXT_TIMER, task)

    @property
    def _failure_retry_task(self):
        return self._task_ref(TaskKind.FAILURE_RETRY)

    @_failure_retry_task.setter
    def _failure_retry_task(self, task):
        self._set_task_compat(TaskKind.FAILURE_RETRY, task)

    @property
    def _add_song_timer(self):
        return self._task_ref(TaskKind.ADD_SONG_TIMER)

    @_add_song_timer.setter
    def _add_song_timer(self, task):
        self._set_task_compat(TaskKind.ADD_SONG_TIMER, task)

    @property
    def _stop_timer(self):
        return self._task_ref(TaskKind.STOP_TIMER)

    @_stop_timer.setter
    def _stop_timer(self, task):
        self._set_task_compat(TaskKind.STOP_TIMER, task)

    def get_runtime_state(self) -> PlaybackRuntimeState:
        """Return the current immutable runtime state snapshot."""
        return self._runtime_state

    @staticmethod
    def _public_runtime_key(state: PlaybackRuntimeState) -> tuple:
        """Return the externally visible runtime identity used for events.

        DISPATCHING and CONFIRMING both project to ``starting``. Lifecycle
        counters and timestamps are deliberately excluded.
        """
        starting = {
            PlaybackPhase.RESOLVING,
            PlaybackPhase.DISPATCHING,
            PlaybackPhase.CONFIRMING,
        }
        phase = "starting" if state.phase in starting else state.phase.value
        return (phase, state.desired_track, state.confirmed_track, state.failure)

    def _publish_state_changed(self) -> None:
        event_bus = getattr(self, "event_bus", None)
        if event_bus:
            event_bus.publish(PLAYER_STATE_CHANGED, device_id=self.did)

    def _set_runtime_state(self, new_state: PlaybackRuntimeState) -> PlaybackRuntimeState:
        """Replace runtime state and publish one semantic public transition."""
        if not isinstance(new_state, PlaybackRuntimeState):
            raise TypeError(
                f"_set_runtime_state requires PlaybackRuntimeState, got {type(new_state).__name__}"
            )
        old_state = getattr(self, "_runtime_state", None)
        self._runtime_state = new_state
        if (
            isinstance(old_state, PlaybackRuntimeState)
            and self._public_runtime_key(old_state) != self._public_runtime_key(new_state)
        ):
            self._publish_state_changed()
        return new_state

    def _start_queue_session(self, updated_at: float) -> PlaybackRuntimeState:
        """Begin a new queue session. Increments queue_session_id only."""
        return self._set_runtime_state(
            new_queue_session(self.get_runtime_state(), updated_at=updated_at)
        )

    def _accept_command(self, updated_at: float) -> PlaybackRuntimeState:
        """Accept a control intent. Increments command_generation only."""
        return self._set_runtime_state(
            next_command(self.get_runtime_state(), updated_at=updated_at)
        )

    def _start_track_attempt(self, updated_at: float) -> LifecycleToken:
        """Begin a physical play attempt. Increments track_attempt_id,
        then returns a strict LifecycleToken captured AFTER the increment.
        """
        state = self._set_runtime_state(
            begin_track_attempt(self.get_runtime_state(), updated_at=updated_at)
        )
        return capture_token(state)

    def _begin_runtime_play_request(
        self, *, desired_track: TrackReference, updated_at: float
    ) -> PlaybackRuntimeState:
        """Begin a higher-level play request via the pure runtime state model.

        Read → call pure model → write back. Pure wrapper, no I/O, no task.
        Does not mutate lifecycle counters (queue_session_id / command_generation /
        track_attempt_id).
        """
        return self._set_runtime_state(
            begin_play_request(
                self.get_runtime_state(),
                desired_track=desired_track,
                updated_at=updated_at,
            )
        )

    def _begin_runtime_play_dispatch(
        self, *, updated_at: float
    ) -> PlaybackRuntimeState:
        """Wrapper for begin_play_dispatch: get → dispatch → set.

        Pure wrapper, no I/O, no task, no legacy ID mutation.
        """
        return self._set_runtime_state(
            begin_play_dispatch(self.get_runtime_state(), updated_at=updated_at)
        )

    def _begin_runtime_external_dispatch_for_token(
        self, token: LifecycleToken
    ) -> bool:
        """Sync helper for external-request dispatch phase entry.

        - strict token stale → False
        - phase RESOLVING/SWITCHING → calls _begin_runtime_play_dispatch → True
        - phase DISPATCHING → idempotent True (fallback, same command)
        - other phase → False

        No I/O. No task. No lifecycle ID change.
        """
        if self._is_lifecycle_token_stale(token):
            return False
        state = self.get_runtime_state()
        if state.phase in {PlaybackPhase.RESOLVING, PlaybackPhase.SWITCHING}:
            try:
                self._begin_runtime_play_dispatch(updated_at=time.time())
            except TransitionError:
                return False
            return True
        if state.phase == PlaybackPhase.DISPATCHING:
            return True
        return False

    def _begin_runtime_confirmation(
        self, *, updated_at: float
    ) -> PlaybackRuntimeState:
        """Wrapper for begin_confirm: get → confirm → set.

        Pure wrapper, no I/O, no task, no legacy ID mutation.
        Only valid from DISPATCHING.
        """
        return self._set_runtime_state(
            begin_confirm(self.get_runtime_state(), updated_at=updated_at)
        )

    def _confirm_runtime_playing(
        self,
        *,
        confirmed_track: TrackReference | None = None,
        expected_end_at: float | None = None,
        updated_at: float,
    ) -> PlaybackRuntimeState:
        """Wrapper for confirm_playing: get → confirm_playing → set.

        Pure wrapper, no I/O, no task, no legacy ID mutation.
        Only valid from CONFIRMING.
        """
        return self._set_runtime_state(
            confirm_playing(
                self.get_runtime_state(),
                confirmed_track=confirmed_track,
                expected_end_at=expected_end_at,
                updated_at=updated_at,
            )
        )

    def _confirm_runtime_playing_for_attempt(
        self, *, token: LifecycleToken, updated_at: float
    ) -> bool:
        """Private sync helper: establish PLAYING fact for the current attempt.

        - token stale → log + False
        - current phase CONFIRMING → calls _confirm_runtime_playing + True
        - current phase PLAYING → idempotent True (late proxy recovery)
        - other phase → log + False

        No I/O. No task. No lifecycle ID changes.
        """
        if self._is_lifecycle_token_stale(token):
            self.log.info(
                "runtime_playing_guard_token_stale did=%s", self.did
            )
            return False
        state = self.get_runtime_state()
        if state.phase == PlaybackPhase.CONFIRMING:
            self._confirm_runtime_playing(updated_at=updated_at, expected_end_at=None)
            return True
        if state.phase == PlaybackPhase.PLAYING:
            self.log.info(
                "runtime_playing_guard_already_playing did=%s", self.did
            )
            return True
        self.log.info(
            "runtime_playing_guard_unexpected_phase did=%s phase=%s",
            self.did,
            state.phase.value,
        )
        return False

    def _begin_runtime_stop(self, updated_at: float) -> PlaybackRuntimeState:
        """Wrapper for runtime_state.begin_stop: active phase → STOPPING.

        Pure wrapper, no I/O, no task, no lifecycle ID mutation.
        """
        return self._set_runtime_state(
            begin_stop(self.get_runtime_state(), updated_at=updated_at)
        )

    def _complete_runtime_stop(self, updated_at: float) -> PlaybackRuntimeState:
        """Wrapper for runtime_state.complete_stop: STOPPING → STOPPED.

        Pure wrapper, no I/O, no task, no lifecycle ID mutation.
        """
        return self._set_runtime_state(
            complete_stop(self.get_runtime_state(), updated_at=updated_at)
        )

    def _report_runtime_failure(
        self,
        *,
        reason: str,
        degraded: bool | None = None,
        updated_at: float,
    ) -> PlaybackRuntimeState:
        """Report failure to runtime state model.

        Pure wrapper: calls report_failure. No I/O, no task, no legacy mutation.
        Only this wrapper may call report_failure in device_player (AST-guarded).
        """
        return self._set_runtime_state(
            report_failure(
                self.get_runtime_state(),
                reason=reason,
                degraded=degraded,
                updated_at=updated_at,
            )
        )

    def _capture_lifecycle_token(self) -> LifecycleToken:
        """Capture current lifecycle counters as a token (read-only)."""
        return capture_token(self.get_runtime_state())

    def _is_lifecycle_token_stale(self, token: LifecycleToken) -> bool:
        """True if any lifecycle counter has changed since token was captured."""
        return check_stale_strict(self.get_runtime_state(), token)

    def _is_barrier_stale(self, sid: int, token: LifecycleToken) -> bool:
        """True if the barrier context is stale.

        Checks sid, queue_session_id, and track_attempt_id — but NOT
        command_generation.  This allows pending after-barrier regular
        intents (which bump command_generation) without killing the
        active barrier executor.
        """
        state = self.get_runtime_state()
        return (
            sid != self._play_session_id
            or check_stale_queue(state, token)
            or check_stale_attempt(state, token)
        )

    def _bump_play_session(self, reason: str = "") -> int:
        self._play_session_id += 1
        registry = self._ensure_playback_tasks()
        # Attempt-scoped: tied to individual track lifecycle.
        # Session-scoped: add-song work is invalidated by a new session.
        # STOP_TIMER (global) intentionally excluded — must survive bumps.
        registry.cancel_by_kinds(*ATTEMPT_SCOPED_KINDS, *SESSION_SCOPED_KINDS)
        self.log.info(
            "play_session_bump(session_id=%s, reason=%s)",
            self._play_session_id,
            reason or "",
        )
        return self._play_session_id

    @staticmethod
    def _task_cancel_requested(task) -> bool:
        cancelling = getattr(task, "cancelling", None)
        return bool(cancelling()) if callable(cancelling) else False

    def _ensure_manual_navigation_state(self) -> None:
        if not hasattr(self, "_manual_nav_lock"):
            self._manual_nav_lock = asyncio.Lock()
            self._manual_nav_generation = 0
            self._manual_nav_target = None

    def _manual_navigation_is_current(self, generation: int | None) -> bool:
        if generation is None:
            return True
        return generation == getattr(self, "_manual_nav_generation", 0)

    def _invalidate_manual_navigation(self, *, reason: str) -> None:
        supersede = getattr(self, "_supersede_queue", None)
        if supersede is not None:
            supersede(reason=reason)
        self._ensure_manual_navigation_state()
        self._manual_nav_generation += 1
        self._manual_nav_target = None
        self.log.info(
            "manual_nav_invalidated generation=%d reason=%s",
            self._manual_nav_generation,
            reason,
        )
