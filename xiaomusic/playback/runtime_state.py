"""Pure PlaybackRuntimeState model (T02-A).

No I/O. No tasks. No device_player references. Immutable reducer-style transitions.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any


class PlaybackPhase(str, Enum):
    IDLE = "idle"
    RESOLVING = "resolving"
    SWITCHING = "switching"
    DISPATCHING = "dispatching"
    CONFIRMING = "confirming"
    PLAYING = "playing"
    PAUSED = "paused"
    STOPPED = "stopped"
    STOPPING = "stopping"
    FAILED = "failed"


class TransitionError(Exception):
    """Raised when an illegal state transition is attempted."""


@dataclass(frozen=True)
class TrackReference:
    """Immutable track identity snapshot."""

    entity_id: str = ""
    playlist_item_id: str = ""
    display_name: str = ""
    source: str = ""


@dataclass(frozen=True)
class DeviceObservation:
    """Snapshot of device status at a point in time. All fields optional."""

    status: int | None = None
    volume: int | None = None
    observed_at: float | None = None
    source: str | None = None


@dataclass(frozen=True)
class FailureState:
    """Failure tracking — purely informational, no retry logic."""

    count: int = 0
    reason: str = ""
    degraded: bool = False
    last_failed_at: float | None = None


@dataclass(frozen=True)
class LifecycleToken:
    """Immutable snapshot of lifecycle counters for stale-guard comparison."""

    queue_session_id: int = 0
    command_generation: int = 0
    track_attempt_id: int = 0


@dataclass(frozen=True)
class PlaybackRuntimeState:
    """Immutable playback runtime state snapshot.

    All transitions return a new instance. The original is never mutated.
    """

    phase: PlaybackPhase = PlaybackPhase.IDLE
    desired_track: TrackReference | None = None
    confirmed_track: TrackReference | None = None
    expected_end_at: float | None = None
    failure: FailureState | None = None
    last_device_observation: DeviceObservation | None = None
    updated_at: float | None = None
    transition_reason: str | None = None
    queue_session_id: int = 0
    command_generation: int = 0
    track_attempt_id: int = 0


# ── active attempt phases (where stop/failure are meaningful) ──────────

_ACTIVE_ATTEMPT = {
    PlaybackPhase.RESOLVING,
    PlaybackPhase.SWITCHING,
    PlaybackPhase.DISPATCHING,
    PlaybackPhase.CONFIRMING,
    PlaybackPhase.PLAYING,
    PlaybackPhase.PAUSED,
}

# ── legal transition table ─────────────────────────────────────────────

_LEGAL = {
    PlaybackPhase.IDLE: {PlaybackPhase.RESOLVING},
    PlaybackPhase.RESOLVING: {PlaybackPhase.DISPATCHING, PlaybackPhase.STOPPING, PlaybackPhase.FAILED},
    PlaybackPhase.SWITCHING: {PlaybackPhase.RESOLVING, PlaybackPhase.STOPPING, PlaybackPhase.FAILED},
    PlaybackPhase.DISPATCHING: {PlaybackPhase.CONFIRMING, PlaybackPhase.STOPPING, PlaybackPhase.FAILED},
    PlaybackPhase.CONFIRMING: {PlaybackPhase.PLAYING, PlaybackPhase.SWITCHING, PlaybackPhase.STOPPING, PlaybackPhase.FAILED},
    PlaybackPhase.PLAYING: {PlaybackPhase.PAUSED, PlaybackPhase.SWITCHING, PlaybackPhase.STOPPING, PlaybackPhase.FAILED},
    PlaybackPhase.PAUSED: {PlaybackPhase.PLAYING, PlaybackPhase.SWITCHING, PlaybackPhase.STOPPING, PlaybackPhase.FAILED},
    PlaybackPhase.STOPPING: {PlaybackPhase.STOPPED, PlaybackPhase.STOPPING},
    PlaybackPhase.STOPPED: {PlaybackPhase.IDLE, PlaybackPhase.RESOLVING},
    PlaybackPhase.FAILED: {PlaybackPhase.IDLE, PlaybackPhase.FAILED, PlaybackPhase.STOPPING},
}


def _transition(
    state: PlaybackRuntimeState,
    target: PlaybackPhase,
    reason: str | None = None,
    **overrides: Any,
) -> PlaybackRuntimeState:
    legal = _LEGAL.get(state.phase, set())
    if target not in legal:
        raise TransitionError(
            f"Illegal transition: {state.phase.value} -> {target.value}"
        )
    kwargs: dict[str, Any] = {
        "phase": target,
        "updated_at": overrides.get("updated_at", state.updated_at),
        "transition_reason": reason or "",
    }
    kwargs.update({k: v for k, v in overrides.items() if k not in kwargs})
    return replace(state, **kwargs)


# ── public transition functions ────────────────────────────────────────


def begin_resolve(
    state: PlaybackRuntimeState,
    *,
    desired_track: TrackReference | None = None,
    updated_at: float,
) -> PlaybackRuntimeState:
    return _transition(
        state,
        PlaybackPhase.RESOLVING,
        reason="begin_resolve",
        desired_track=desired_track or state.desired_track,
        updated_at=updated_at,
    )


def begin_switch(
    state: PlaybackRuntimeState,
    *,
    desired_track: TrackReference,
    updated_at: float,
) -> PlaybackRuntimeState:
    """Begin switching from current track to a new desired track.

    Replaces desired_track, preserves confirmed_track for UI until new track
    is confirmed. Clears expected_end_at. Only from PLAYING/PAUSED/CONFIRMING.
    """
    if state.phase not in {PlaybackPhase.PLAYING, PlaybackPhase.PAUSED, PlaybackPhase.CONFIRMING}:
        raise TransitionError(
            f"begin_switch requires PLAYING/PAUSED/CONFIRMING, got {state.phase.value}"
        )
    return replace(
        state,
        phase=PlaybackPhase.SWITCHING,
        transition_reason="begin_switch",
        desired_track=desired_track,
        expected_end_at=None,
        updated_at=updated_at,
    )


def begin_play_request(
    state: PlaybackRuntimeState,
    *,
    desired_track: TrackReference,
    updated_at: float,
) -> PlaybackRuntimeState:
    """Begin a play request from a higher-level caller (e.g. facade).

    Phase mapping:
      IDLE / STOPPED / FAILED / RESOLVING / DISPATCHING → RESOLVING
      PLAYING / PAUSED / CONFIRMING / SWITCHING → SWITCHING
      STOPPING → TransitionError

    Common rules:
      - desired_track → replaced with parameter
      - confirmed_track → preserved
      - expected_end_at → cleared
      - failure → preserved (only confirm_playing clears it)
      - updated_at → parameter
      - transition_reason → "begin_play_request"
      - queue_session_id / command_generation / track_attempt_id → unchanged
    """
    _resolving_phases = {
        PlaybackPhase.IDLE,
        PlaybackPhase.STOPPED,
        PlaybackPhase.FAILED,
        PlaybackPhase.RESOLVING,
        PlaybackPhase.DISPATCHING,
    }
    _switching_phases = {
        PlaybackPhase.PLAYING,
        PlaybackPhase.PAUSED,
        PlaybackPhase.CONFIRMING,
        PlaybackPhase.SWITCHING,
    }
    if state.phase == PlaybackPhase.STOPPING:
        raise TransitionError(
            f"begin_play_request illegal from {state.phase.value}"
        )
    if state.phase in _switching_phases:
        target = PlaybackPhase.SWITCHING
    else:
        target = PlaybackPhase.RESOLVING
    return replace(
        state,
        phase=target,
        transition_reason="begin_play_request",
        desired_track=desired_track,
        expected_end_at=None,
        updated_at=updated_at,
    )


def begin_dispatch(
    state: PlaybackRuntimeState,
    *,
    updated_at: float,
) -> PlaybackRuntimeState:
    return _transition(
        state,
        PlaybackPhase.DISPATCHING,
        reason="begin_dispatch",
        updated_at=updated_at,
    )


def begin_play_dispatch(
    state: PlaybackRuntimeState,
    *,
    updated_at: float,
) -> PlaybackRuntimeState:
    """Transition to DISPATCHING from RESOLVING or SWITCHING only.

    Preserves desired_track, confirmed_track, expected_end_at, failure,
    last_device_observation, and all three lifecycle IDs.
    All other phases raise TransitionError.
    """
    if state.phase not in {PlaybackPhase.RESOLVING, PlaybackPhase.SWITCHING}:
        raise TransitionError(
            f"begin_play_dispatch requires RESOLVING or SWITCHING, got {state.phase.value}"
        )
    return replace(
        state,
        phase=PlaybackPhase.DISPATCHING,
        transition_reason="begin_play_dispatch",
        updated_at=updated_at,
    )


def begin_confirm(
    state: PlaybackRuntimeState,
    *,
    updated_at: float,
) -> PlaybackRuntimeState:
    return _transition(
        state,
        PlaybackPhase.CONFIRMING,
        reason="begin_confirm",
        updated_at=updated_at,
    )


def confirm_playing(
    state: PlaybackRuntimeState,
    *,
    confirmed_track: TrackReference | None = None,
    expected_end_at: float | None = None,
    updated_at: float,
) -> PlaybackRuntimeState:
    """Transition to playing and clear attempt-level failure."""
    return _transition(
        state,
        PlaybackPhase.PLAYING,
        reason="confirm_playing",
        confirmed_track=confirmed_track or state.desired_track,
        expected_end_at=expected_end_at,
        updated_at=updated_at,
        failure=None,
    )


def confirm_failed(
    state: PlaybackRuntimeState,
    *,
    reason: str = "",
    degraded: bool | None = None,
    updated_at: float,
) -> PlaybackRuntimeState:
    """Transition to failed from confirming phase only."""
    if state.phase != PlaybackPhase.CONFIRMING:
        raise TransitionError(
            f"confirm_failed requires CONFIRMING phase, got {state.phase.value}"
        )
    prev = state.failure
    new_failure = FailureState(
        count=(prev.count + 1) if prev else 1,
        reason=reason,
        degraded=degraded if degraded is not None else (prev.degraded if prev else False),
        last_failed_at=updated_at,
    )
    return replace(
        state,
        phase=PlaybackPhase.FAILED,
        transition_reason=f"confirm_failed: {reason}" if reason else "confirm_failed",
        failure=new_failure,
        updated_at=updated_at,
    )


def begin_stop(
    state: PlaybackRuntimeState,
    *,
    updated_at: float,
) -> PlaybackRuntimeState:
    """Begin stop from any active attempt, FAILED, or STOPPING phase."""
    if state.phase not in _ACTIVE_ATTEMPT and state.phase not in {PlaybackPhase.STOPPING, PlaybackPhase.FAILED}:
        raise TransitionError(
            f"begin_stop requires active or stopping phase, got {state.phase.value}"
        )
    return replace(
        state,
        phase=PlaybackPhase.STOPPING,
        transition_reason="begin_stop",
        updated_at=updated_at,
    )


def complete_stop(
    state: PlaybackRuntimeState,
    *,
    updated_at: float,
) -> PlaybackRuntimeState:
    """Complete stop: STOPPING → STOPPED. Preserves confirmed_track for UI, clears expected_end_at."""
    if state.phase != PlaybackPhase.STOPPING:
        raise TransitionError(
            f"complete_stop requires STOPPING phase, got {state.phase.value}"
        )
    return replace(
        state,
        phase=PlaybackPhase.STOPPED,
        transition_reason="complete_stop",
        expected_end_at=None,
        updated_at=updated_at,
    )


def pause(
    state: PlaybackRuntimeState,
    *,
    updated_at: float,
) -> PlaybackRuntimeState:
    return _transition(
        state,
        PlaybackPhase.PAUSED,
        reason="pause",
        updated_at=updated_at,
    )


def resume(
    state: PlaybackRuntimeState,
    *,
    updated_at: float,
) -> PlaybackRuntimeState:
    return _transition(
        state,
        PlaybackPhase.PLAYING,
        reason="resume",
        updated_at=updated_at,
    )


def report_failure(
    state: PlaybackRuntimeState,
    *,
    reason: str = "",
    degraded: bool | None = None,
    updated_at: float,
) -> PlaybackRuntimeState:
    """Report failure from active attempt phases only. IDLE/STOPPED raise.

    FAILED → FAILED accumulates (count increments, repeated report).
    """
    if state.phase not in _ACTIVE_ATTEMPT and state.phase != PlaybackPhase.FAILED:
        raise TransitionError(
            f"report_failure requires active or failed phase, got {state.phase.value}"
        )
    prev = state.failure
    new_count = (prev.count + 1) if prev else 1
    new_failure = FailureState(
        count=new_count,
        reason=reason,
        degraded=degraded if degraded is not None else (prev.degraded if prev else False),
        last_failed_at=updated_at,
    )
    return replace(
        state,
        phase=PlaybackPhase.FAILED,
        transition_reason=f"report_failure: {reason}" if reason else "report_failure",
        failure=new_failure,
        updated_at=updated_at,
    )


def clear_state(
    state: PlaybackRuntimeState,
    *,
    updated_at: float,
) -> PlaybackRuntimeState:
    """Clear state back to IDLE. Only from STOPPED or FAILED."""
    if state.phase not in {PlaybackPhase.STOPPED, PlaybackPhase.FAILED}:
        raise TransitionError(
            f"clear_state requires STOPPED or FAILED, got {state.phase.value}"
        )
    return replace(
        state,
        phase=PlaybackPhase.IDLE,
        transition_reason="clear_state",
        desired_track=None,
        confirmed_track=None,
        expected_end_at=None,
        failure=None,
        updated_at=updated_at,
    )


def update_observation(
    state: PlaybackRuntimeState,
    observation: DeviceObservation,
    *,
    updated_at: float,
) -> PlaybackRuntimeState:
    """Update device observation without changing phase."""
    return replace(
        state,
        last_device_observation=observation,
        updated_at=updated_at,
    )


# ── lifecycle counters ────────────────────────────────────────────────


def new_queue_session(
    state: PlaybackRuntimeState,
    *,
    updated_at: float,
) -> PlaybackRuntimeState:
    """Start a new queue session (e.g. new POST /play). Increments queue_session_id only."""
    return replace(
        state,
        queue_session_id=state.queue_session_id + 1,
        updated_at=updated_at,
    )


def next_command(
    state: PlaybackRuntimeState,
    *,
    updated_at: float,
) -> PlaybackRuntimeState:
    """Accept a control intent (next/prev/stop/pause). Increments command_generation only."""
    return replace(
        state,
        command_generation=state.command_generation + 1,
        updated_at=updated_at,
    )


def begin_track_attempt(
    state: PlaybackRuntimeState,
    *,
    updated_at: float,
) -> PlaybackRuntimeState:
    """Begin a physical play attempt. Increments track_attempt_id only."""
    return replace(
        state,
        track_attempt_id=state.track_attempt_id + 1,
        updated_at=updated_at,
    )


def capture_token(state: PlaybackRuntimeState) -> LifecycleToken:
    """Capture a snapshot of lifecycle counters for later guard checks."""
    return LifecycleToken(
        queue_session_id=state.queue_session_id,
        command_generation=state.command_generation,
        track_attempt_id=state.track_attempt_id,
    )


def check_stale_queue(state: PlaybackRuntimeState, token: LifecycleToken) -> bool:
    """True if queue_session_id has changed since token was captured."""
    return state.queue_session_id != token.queue_session_id


def check_stale_command(state: PlaybackRuntimeState, token: LifecycleToken) -> bool:
    """True if command_generation has changed since token was captured."""
    return state.command_generation != token.command_generation


def check_stale_attempt(state: PlaybackRuntimeState, token: LifecycleToken) -> bool:
    """True if track_attempt_id has changed since token was captured."""
    return state.track_attempt_id != token.track_attempt_id


def check_stale_strict(state: PlaybackRuntimeState, token: LifecycleToken) -> bool:
    """True if any of queue_session_id, command_generation, or track_attempt_id
    has changed since token was captured. Strictest guard for track attempts."""
    return (
        state.queue_session_id != token.queue_session_id
        or state.command_generation != token.command_generation
        or state.track_attempt_id != token.track_attempt_id
    )
