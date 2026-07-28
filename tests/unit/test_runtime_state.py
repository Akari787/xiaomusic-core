"""Tests for PlaybackRuntimeState pure state model (T02-A)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from xiaomusic.playback.runtime_state import (
    DeviceObservation,
    FailureState,
    PlaybackPhase,
    PlaybackRuntimeState,
    TrackReference,
    TransitionError,
    begin_confirm,
    begin_dispatch,
    begin_play_dispatch,
    begin_play_request,
    begin_resolve,
    begin_stop,
    begin_switch,
    begin_track_attempt,
    capture_token,
    check_stale_attempt,
    check_stale_command,
    check_stale_queue,
    check_stale_strict,
    clear_state,
    complete_stop,
    confirm_failed,
    confirm_playing,
    new_queue_session,
    next_command,
    pause,
    report_failure,
    resume,
    update_observation,
)


def _tr(entity_id: str = "", display_name: str = "", source: str = "") -> TrackReference:
    return TrackReference(entity_id=entity_id, display_name=display_name, source=source)


def _idle_state() -> PlaybackRuntimeState:
    return PlaybackRuntimeState(phase=PlaybackPhase.IDLE)


def _playing_state() -> PlaybackRuntimeState:
    return PlaybackRuntimeState(
        phase=PlaybackPhase.PLAYING,
        desired_track=_tr("e1", "track-A", "jellyfin"),
        confirmed_track=_tr("e1", "track-A", "jellyfin"),
    )


# ── phase enum ─────────────────────────────────────────────────────────

def test_phase_enum_has_all_values():
    expected = {"idle", "resolving", "switching", "dispatching", "confirming",
                "playing", "paused", "stopped", "stopping", "failed"}
    actual = {e.value for e in PlaybackPhase}
    assert actual == expected


# ── legal transitions ──────────────────────────────────────────────────

def test_idle_to_resolving():
    s = begin_resolve(_idle_state(), desired_track=_tr("e1", "song"), updated_at=1.0)
    assert s.phase == PlaybackPhase.RESOLVING
    assert s.desired_track.display_name == "song"
    assert s.updated_at == 1.0


def test_resolving_to_dispatching():
    s = begin_dispatch(begin_resolve(_idle_state(), updated_at=1.0), updated_at=2.0)
    assert s.phase == PlaybackPhase.DISPATCHING


def test_dispatching_to_confirming():
    s = begin_confirm(begin_dispatch(begin_resolve(_idle_state(), updated_at=1.0), updated_at=2.0), updated_at=3.0)
    assert s.phase == PlaybackPhase.CONFIRMING


def test_confirming_to_playing():
    s = confirm_playing(
        begin_confirm(begin_dispatch(begin_resolve(_idle_state(), updated_at=1.0), updated_at=2.0), updated_at=3.0),
        confirmed_track=_tr("e1", "song"),
        expected_end_at=100.0,
        updated_at=4.0,
    )
    assert s.phase == PlaybackPhase.PLAYING
    assert s.confirmed_track.display_name == "song"
    assert s.expected_end_at == 100.0


def test_report_failure_does_not_auto_degrade():
    """连续报告 10 次也不自动 degraded，除非调用者显式传入。"""
    s = PlaybackRuntimeState(phase=PlaybackPhase.CONFIRMING)
    for i in range(10):
        s = report_failure(s, reason=f"err{i}", updated_at=float(i))
    assert s.failure.count == 10
    assert s.failure.degraded is False


def test_report_failure_explicit_degraded():
    """调用者显式 degraded=True 时记录，后续 None 保持 True。"""
    s = PlaybackRuntimeState(phase=PlaybackPhase.CONFIRMING)
    s = report_failure(s, reason="e1", degraded=True, updated_at=1.0)
    assert s.failure.degraded is True
    s = report_failure(s, reason="e2", updated_at=2.0)
    assert s.failure.degraded is True  # preserved


def test_confirm_playing_clears_failure():
    s = PlaybackRuntimeState(
        phase=PlaybackPhase.CONFIRMING,
        failure=FailureState(count=3, reason="prev", degraded=True),
    )
    s2 = confirm_playing(s, confirmed_track=_tr("e1", "x"), expected_end_at=50.0, updated_at=1.0)
    assert s2.failure is None


@pytest.mark.asyncio
async def test_runtime_state_module_has_no_device_imports():
    """模型文件不引用 XiaoMusicDevice 或任何设备模块。"""
    import ast
    with open("xiaomusic/playback/runtime_state.py") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                name = alias.name
                if isinstance(node, ast.ImportFrom):
                    name = f"{node.module}.{alias.name}" if node.module else alias.name
                assert "device" not in name.lower(), f"imports device: {name}"
                assert "xiaomusic" not in name or "runtime_state" in name or "playback" in name, f"unexpected import: {name}"


def test_confirming_to_failed():
    s = confirm_failed(
        begin_confirm(begin_dispatch(begin_resolve(_idle_state(), updated_at=1.0), updated_at=2.0), updated_at=3.0),
        reason="no response",
        updated_at=4.0,
    )
    assert s.phase == PlaybackPhase.FAILED
    assert s.failure.reason == "no response"
    assert s.failure.count == 1


def test_playing_to_paused():
    s = pause(_playing_state(), updated_at=1.0)
    assert s.phase == PlaybackPhase.PAUSED


def test_paused_to_playing():
    s = resume(pause(_playing_state(), updated_at=1.0), updated_at=2.0)
    assert s.phase == PlaybackPhase.PLAYING


# ── switching ──────────────────────────────────────────────────────────

def test_playing_to_switching():
    s = PlaybackRuntimeState(
        phase=PlaybackPhase.PLAYING,
        desired_track=_tr("e1", "old"),
        confirmed_track=_tr("e1", "old"),
        expected_end_at=100.0,
    )
    s2 = begin_switch(s, desired_track=_tr("e2", "new"), updated_at=1.0)
    assert s2.phase == PlaybackPhase.SWITCHING
    assert s2.desired_track.display_name == "new"
    assert s2.confirmed_track.display_name == "old"  # preserved for UI
    assert s2.expected_end_at is None


def test_switching_to_resolving_preserves_desired():
    s = begin_switch(_playing_state(), desired_track=_tr("e2", "new"), updated_at=1.0)
    s2 = begin_resolve(s, updated_at=2.0)
    assert s2.phase == PlaybackPhase.RESOLVING
    assert s2.desired_track.display_name == "new"


def test_switching_full_cycle_to_playing():
    s = _playing_state()
    s = begin_switch(s, desired_track=_tr("e2", "new"), updated_at=1.0)
    assert s.phase == PlaybackPhase.SWITCHING and s.confirmed_track.display_name == "track-A"
    s = begin_resolve(s, updated_at=2.0)
    assert s.phase == PlaybackPhase.RESOLVING
    s = begin_dispatch(s, updated_at=3.0)
    s = begin_confirm(s, updated_at=4.0)
    s = confirm_playing(s, confirmed_track=_tr("e2", "new"), expected_end_at=50.0, updated_at=5.0)
    assert s.phase == PlaybackPhase.PLAYING and s.confirmed_track.display_name == "new"


def test_switching_can_stop():
    s = begin_switch(_playing_state(), desired_track=_tr("e2", "new"), updated_at=1.0)
    s = begin_stop(s, updated_at=2.0)
    assert s.phase == PlaybackPhase.STOPPING


def test_switching_can_fail():
    s = begin_switch(_playing_state(), desired_track=_tr("e2", "new"), updated_at=1.0)
    s = report_failure(s, reason="err", updated_at=2.0)
    assert s.phase == PlaybackPhase.FAILED


def test_idle_cannot_begin_switch():
    with pytest.raises(TransitionError):
        begin_switch(_idle_state(), desired_track=_tr("e1", "x"), updated_at=1.0)


def test_stopped_cannot_begin_switch():
    with pytest.raises(TransitionError):
        begin_switch(PlaybackRuntimeState(phase=PlaybackPhase.STOPPED), desired_track=_tr("e1", "x"), updated_at=1.0)


def test_paused_can_begin_switch():
    s = begin_switch(pause(_playing_state(), updated_at=1.0), desired_track=_tr("e2", "new"), updated_at=2.0)
    assert s.phase == PlaybackPhase.SWITCHING


def test_confirming_can_begin_switch():
    s = PlaybackRuntimeState(phase=PlaybackPhase.CONFIRMING, desired_track=_tr("e1", "old"), confirmed_track=_tr("e1", "old"))
    s2 = begin_switch(s, desired_track=_tr("e2", "new"), updated_at=1.0)
    assert s2.phase == PlaybackPhase.SWITCHING


# ── stop semantics ─────────────────────────────────────────────────────

def test_begin_stop_from_active():
    for p in (PlaybackPhase.RESOLVING, PlaybackPhase.SWITCHING, PlaybackPhase.DISPATCHING, PlaybackPhase.CONFIRMING,
              PlaybackPhase.PLAYING, PlaybackPhase.PAUSED, PlaybackPhase.FAILED):
        s = begin_stop(PlaybackRuntimeState(phase=p), updated_at=1.0)
        assert s.phase == PlaybackPhase.STOPPING


def test_complete_stop_terminates_in_stopped():
    s = begin_stop(_playing_state(), updated_at=1.0)
    s2 = complete_stop(s, updated_at=2.0)
    assert s2.phase == PlaybackPhase.STOPPED
    assert s2.confirmed_track is not None  # preserved for UI
    assert s2.expected_end_at is None  # cleared


def test_begin_stop_from_stopping_is_idempotent():
    s = PlaybackRuntimeState(phase=PlaybackPhase.STOPPING)
    s2 = begin_stop(s, updated_at=1.0)
    assert s2.phase == PlaybackPhase.STOPPING


def test_begin_stop_from_idle_raises():
    with pytest.raises(TransitionError):
        begin_stop(_idle_state(), updated_at=1.0)


def test_begin_stop_from_stopped_raises():
    with pytest.raises(TransitionError):
        begin_stop(PlaybackRuntimeState(phase=PlaybackPhase.STOPPED), updated_at=1.0)


def test_stopped_can_clear_to_idle():
    s = complete_stop(begin_stop(_playing_state(), updated_at=1.0), updated_at=2.0)
    s2 = clear_state(s, updated_at=3.0)
    assert s2.phase == PlaybackPhase.IDLE
    assert s2.confirmed_track is None


def test_failed_begin_stop_preserves_failure():
    """FAILED→STOPPING preserves failure info for diagnosis."""
    s = PlaybackRuntimeState(
        phase=PlaybackPhase.FAILED,
        failure=FailureState(count=3, reason="err", degraded=False, last_failed_at=1.0),
        desired_track=_tr("e1", "song"),
        expected_end_at=100.0,
    )
    s2 = begin_stop(s, updated_at=2.0)
    assert s2.phase == PlaybackPhase.STOPPING
    assert s2.failure is not None
    assert s2.failure.count == 3
    assert s2.failure.reason == "err"


def test_failed_stop_complete_preserves_failure_clears_expected_end():
    """FAILED→STOPPING→STOPPED: failure preserved, expected_end cleared."""
    s = PlaybackRuntimeState(
        phase=PlaybackPhase.FAILED,
        failure=FailureState(count=2, reason="boom", degraded=True, last_failed_at=5.0),
        desired_track=_tr("e1", "song"),
        expected_end_at=50.0,
    )
    s = begin_stop(s, updated_at=1.0)
    assert s.phase == PlaybackPhase.STOPPING
    s = complete_stop(s, updated_at=2.0)
    assert s.phase == PlaybackPhase.STOPPED
    assert s.failure is not None
    assert s.failure.count == 2
    assert s.failure.reason == "boom"
    assert s.failure.degraded is True
    assert s.expected_end_at is None


# ── failure semantics ──────────────────────────────────────────────────

def test_report_failure_from_active():
    for p in (PlaybackPhase.RESOLVING, PlaybackPhase.DISPATCHING, PlaybackPhase.CONFIRMING,
              PlaybackPhase.PLAYING, PlaybackPhase.PAUSED):
        s = report_failure(PlaybackRuntimeState(phase=p), reason="err", updated_at=1.0)
        assert s.phase == PlaybackPhase.FAILED


def test_report_failure_from_idle_raises():
    with pytest.raises(TransitionError):
        report_failure(_idle_state(), reason="err", updated_at=1.0)


def test_report_failure_from_stopped_raises():
    with pytest.raises(TransitionError):
        report_failure(PlaybackRuntimeState(phase=PlaybackPhase.STOPPED), reason="err", updated_at=1.0)


def test_report_failure_from_failed_idempotent():
    s = report_failure(
        PlaybackRuntimeState(phase=PlaybackPhase.CONFIRMING), reason="one", updated_at=1.0
    )
    s2 = report_failure(s, reason="two", updated_at=2.0)
    assert s2.phase == PlaybackPhase.FAILED
    assert s2.failure.count == 2
    assert s2.failure.reason == "two"


# ── clear_state ────────────────────────────────────────────────────────

def test_clear_state_from_stopped():
    s = clear_state(PlaybackRuntimeState(phase=PlaybackPhase.STOPPED), updated_at=1.0)
    assert s.phase == PlaybackPhase.IDLE


def test_clear_state_from_failed():
    s = clear_state(PlaybackRuntimeState(phase=PlaybackPhase.FAILED), updated_at=1.0)
    assert s.phase == PlaybackPhase.IDLE


def test_clear_state_from_idle_raises():
    with pytest.raises(TransitionError):
        clear_state(_idle_state(), updated_at=1.0)


def test_clear_state_from_playing_raises():
    with pytest.raises(TransitionError):
        clear_state(_playing_state(), updated_at=1.0)


# ── illegal transitions ────────────────────────────────────────────────

@pytest.mark.parametrize("from_phase,fn,kwargs", [
    (PlaybackPhase.RESOLVING, begin_resolve, {"updated_at": 1.0}),
    (PlaybackPhase.PLAYING, begin_resolve, {"updated_at": 1.0}),
    (PlaybackPhase.IDLE, begin_dispatch, {"updated_at": 1.0}),
    (PlaybackPhase.IDLE, begin_confirm, {"updated_at": 1.0}),
    (PlaybackPhase.IDLE, pause, {"updated_at": 1.0}),
    (PlaybackPhase.RESOLVING, pause, {"updated_at": 1.0}),
    (PlaybackPhase.PAUSED, pause, {"updated_at": 1.0}),
    (PlaybackPhase.IDLE, resume, {"updated_at": 1.0}),
    (PlaybackPhase.RESOLVING, resume, {"updated_at": 1.0}),
    (PlaybackPhase.PLAYING, resume, {"updated_at": 1.0}),
    (PlaybackPhase.IDLE, complete_stop, {"updated_at": 1.0}),
    (PlaybackPhase.STOPPED, begin_stop, {"updated_at": 1.0}),
])
def test_illegal_transition_raises(from_phase, fn, kwargs):
    s = PlaybackRuntimeState(phase=from_phase)
    with pytest.raises(TransitionError):
        fn(s, **kwargs)


# ── immutability ───────────────────────────────────────────────────────

def test_transition_does_not_mutate_input():
    original = _idle_state()
    result = begin_resolve(original, updated_at=1.0)
    assert original.phase == PlaybackPhase.IDLE
    assert result.phase == PlaybackPhase.RESOLVING
    assert original is not result


def test_track_reference_preserved_through_stop():
    s = _playing_state()
    s2 = complete_stop(begin_stop(s, updated_at=1.0), updated_at=2.0)
    assert s2.confirmed_track.entity_id == "e1"
    assert s2.confirmed_track.display_name == "track-A"
    assert s2.confirmed_track.source == "jellyfin"


# ── observation ────────────────────────────────────────────────────────

def test_observation_does_not_change_phase():
    s = _playing_state()
    obs = DeviceObservation(status=1, volume=48, observed_at=100.0)
    s2 = update_observation(s, obs, updated_at=101.0)
    assert s2.phase == PlaybackPhase.PLAYING
    assert s2.last_device_observation.status == 1
    assert s2.last_device_observation.volume == 48
    assert s2.last_device_observation.observed_at == 100.0


# ── failure has no navigation ──────────────────────────────────────────

def test_failure_preserves_track():
    s = _playing_state()
    s2 = report_failure(s, reason="boom", updated_at=1.0)
    assert s2.desired_track.display_name == "track-A"
    assert s2.confirmed_track.display_name == "track-A"
    assert s2.failure.count == 1


# ── time preservation ──────────────────────────────────────────────────

def test_updated_at_not_cleared_by_transition():
    s = PlaybackRuntimeState(phase=PlaybackPhase.IDLE, updated_at=10.0)
    s2 = begin_resolve(s, updated_at=15.0)
    assert s2.updated_at == 15.0


def test_observation_updated_at_not_cleared():
    s = PlaybackRuntimeState(phase=PlaybackPhase.PLAYING, updated_at=10.0)
    obs = DeviceObservation(status=1, observed_at=12.0)
    s2 = update_observation(s, obs, updated_at=13.0)
    assert s2.updated_at == 13.0
    assert s2.last_device_observation.observed_at == 12.0


# ── confirm_playing is recovery boundary ───────────────────────────────

def test_confirm_playing_resets_degraded():
    s = PlaybackRuntimeState(
        phase=PlaybackPhase.CONFIRMING,
        failure=FailureState(count=5, reason="many", degraded=True),
    )
    s2 = confirm_playing(s, confirmed_track=_tr("e1", "x"), expected_end_at=1.0, updated_at=2.0)
    assert s2.failure is None
    assert s2.phase == PlaybackPhase.PLAYING


# ── lifecycle counters ────────────────────────────────────────────────


def _playing_with_ids(q=0, c=0, a=0):
    return PlaybackRuntimeState(
        phase=PlaybackPhase.PLAYING,
        queue_session_id=q,
        command_generation=c,
        track_attempt_id=a,
    )


def test_queue_session_id_is_monotonic():
    s = new_queue_session(_idle_state(), updated_at=1.0)
    assert s.queue_session_id == 1
    s2 = new_queue_session(s, updated_at=2.0)
    assert s2.queue_session_id == 2


def test_queue_different_from_initial():
    s = new_queue_session(_idle_state(), updated_at=1.0)
    # initial idle has queue=0, after new_queue_session it's 1
    assert s.queue_session_id == 1


def test_clear_state_does_not_reset_ids():
    s = PlaybackRuntimeState(phase=PlaybackPhase.STOPPED, queue_session_id=5, command_generation=3, track_attempt_id=7)
    s2 = clear_state(s, updated_at=1.0)
    assert s2.queue_session_id == 5
    assert s2.command_generation == 3
    assert s2.track_attempt_id == 7


def test_new_play_increments_queue_and_command():
    """New /play: queue+1, command+1."""
    s = _idle_state()
    s = new_queue_session(s, updated_at=1.0)
    s = next_command(s, updated_at=1.0)
    assert s.queue_session_id == 1 and s.command_generation == 1 and s.track_attempt_id == 0
    s = begin_track_attempt(s, updated_at=1.0)
    assert s.track_attempt_id == 1


def test_same_queue_next_increments_command_and_attempt():
    """Same queue next: queue stays, command+1, attempt+1."""
    s = _playing_with_ids(q=1, c=1, a=1)
    s = next_command(s, updated_at=1.0)
    s = begin_track_attempt(s, updated_at=1.0)
    assert s.queue_session_id == 1 and s.command_generation == 2 and s.track_attempt_id == 2


def test_stop_increments_command_only():
    s = _playing_with_ids(q=1, c=1, a=1)
    s = next_command(s, updated_at=1.0)
    assert s.queue_session_id == 1 and s.command_generation == 2 and s.track_attempt_id == 1


def test_retry_is_new_attempt():
    """Retry: new attempt, command policy TBD."""
    s = _playing_with_ids(q=1, c=1, a=1)
    s = begin_track_attempt(s, updated_at=1.0)
    assert s.track_attempt_id == 2


def test_strict_stale_guard_true_when_any_differs():
    s1 = _playing_with_ids(q=1, c=1, a=1)
    token = capture_token(s1)
    s2 = replace(s1, queue_session_id=2)
    assert check_stale_strict(s2, token)
    s3 = replace(s1, command_generation=2)
    assert check_stale_strict(s3, token)
    s4 = replace(s1, track_attempt_id=2)
    assert check_stale_strict(s4, token)


def test_strict_stale_guard_false_when_all_match():
    s1 = _playing_with_ids(q=1, c=1, a=1)
    token = capture_token(s1)
    assert not check_stale_strict(s1, token)


def test_single_guards_check_only_own_dimension():
    s1 = _playing_with_ids(q=1, c=1, a=1)
    token = capture_token(s1)
    s2 = replace(s1, queue_session_id=2)
    assert check_stale_queue(s2, token)
    assert not check_stale_command(s2, token)
    assert not check_stale_attempt(s2, token)


def test_stale_is_not_failed_phase():
    """Stale guard returning True does not change phase to FAILED."""
    s1 = _playing_with_ids(q=1, c=1, a=1)
    token = capture_token(s1)
    s2 = replace(s1, command_generation=2)
    assert check_stale_command(s2, token)
    assert s2.phase == PlaybackPhase.PLAYING  # phase unchanged


# ── begin_play_request ────────────────────────────────────────────────


def _rich_state(phase: PlaybackPhase) -> PlaybackRuntimeState:
    """Create a state with all fields populated for preservation tests."""
    return PlaybackRuntimeState(
        phase=phase,
        desired_track=_tr("e-old", "old-track", "jellyfin"),
        confirmed_track=_tr("e-old", "old-track", "jellyfin"),
        expected_end_at=100.0,
        failure=FailureState(count=2, reason="prev-err", degraded=False, last_failed_at=50.0),
        last_device_observation=DeviceObservation(status=1, volume=30, observed_at=90.0),
        updated_at=99.0,
        transition_reason="previous-action",
        queue_session_id=7,
        command_generation=13,
        track_attempt_id=5,
    )


def _assert_common_fields(s: PlaybackRuntimeState, new_track: TrackReference, expected_phase: PlaybackPhase):
    """Assert the common rules for begin_play_request output."""
    assert s.phase == expected_phase
    assert s.desired_track is new_track
    assert s.confirmed_track is not None
    assert s.confirmed_track.entity_id == "e-old"
    assert s.confirmed_track.display_name == "old-track"
    assert s.expected_end_at is None
    assert s.failure is not None
    assert s.failure.count == 2
    assert s.failure.reason == "prev-err"
    assert s.updated_at == 200.0
    assert s.transition_reason == "begin_play_request"
    assert s.queue_session_id == 7
    assert s.command_generation == 13
    assert s.track_attempt_id == 5


_new_track = _tr("e-new", "new-track", "jellyfin")

_RESOLVING_PHASES = [
    PlaybackPhase.IDLE,
    PlaybackPhase.STOPPED,
    PlaybackPhase.FAILED,
    PlaybackPhase.RESOLVING,
    PlaybackPhase.DISPATCHING,
]
_SWITCHING_PHASES = [
    PlaybackPhase.PLAYING,
    PlaybackPhase.PAUSED,
    PlaybackPhase.CONFIRMING,
    PlaybackPhase.SWITCHING,
]


@pytest.mark.parametrize("phase", _RESOLVING_PHASES)
def test_begin_play_request_to_resolving(phase: PlaybackPhase):
    """IDLE/STOPPED/FAILED/RESOLVING/DISPATCHING → RESOLVING."""
    s = _rich_state(phase)
    s2 = begin_play_request(s, desired_track=_new_track, updated_at=200.0)
    _assert_common_fields(s2, _new_track, PlaybackPhase.RESOLVING)


@pytest.mark.parametrize("phase", _SWITCHING_PHASES)
def test_begin_play_request_to_switching(phase: PlaybackPhase):
    """PLAYING/PAUSED/CONFIRMING/SWITCHING → SWITCHING."""
    s = _rich_state(phase)
    s2 = begin_play_request(s, desired_track=_new_track, updated_at=200.0)
    _assert_common_fields(s2, _new_track, PlaybackPhase.SWITCHING)


def test_begin_play_request_from_stopping_raises():
    """STOPPING → TransitionError."""
    s = _rich_state(PlaybackPhase.STOPPING)
    with pytest.raises(TransitionError):
        begin_play_request(s, desired_track=_new_track, updated_at=200.0)


def test_begin_play_request_does_not_mutate_input():
    """Original state is immutable after begin_play_request."""
    s = _rich_state(PlaybackPhase.IDLE)
    original_phase = s.phase
    original_desired = s.desired_track
    original_expected_end = s.expected_end_at
    s2 = begin_play_request(s, desired_track=_new_track, updated_at=200.0)
    assert s.phase == original_phase
    assert s.desired_track is original_desired
    assert s.expected_end_at == original_expected_end
    assert s is not s2


def test_begin_play_request_confirmed_track_preserved_across_switch():
    """When switching, confirmed_track stays on the old track for UI."""
    s = _rich_state(PlaybackPhase.PLAYING)
    s2 = begin_play_request(s, desired_track=_new_track, updated_at=200.0)
    assert s2.phase == PlaybackPhase.SWITCHING
    assert s2.desired_track.display_name == "new-track"
    assert s2.confirmed_track.display_name == "old-track"


def test_begin_play_request_clears_expected_end_at():
    """Every valid phase transition clears expected_end_at."""
    for phase in _RESOLVING_PHASES + _SWITCHING_PHASES:
        s = _rich_state(phase)
        s2 = begin_play_request(s, desired_track=_new_track, updated_at=200.0)
        assert s2.expected_end_at is None, f"expected_end_at not cleared from {phase.value}"


def test_begin_play_request_preserves_failure():
    """Failure is preserved; only confirm_playing clears it."""
    for phase in _RESOLVING_PHASES + _SWITCHING_PHASES:
        s = _rich_state(phase)
        s2 = begin_play_request(s, desired_track=_new_track, updated_at=200.0)
        assert s2.failure is not None, f"failure lost from {phase.value}"
        assert s2.failure.count == 2, f"failure count changed from {phase.value}"


def test_begin_play_request_ids_unchanged():
    """queue_session_id / command_generation / track_attempt_id are unchanged."""
    for phase in _RESOLVING_PHASES + _SWITCHING_PHASES:
        s = _rich_state(phase)
        s2 = begin_play_request(s, desired_track=_new_track, updated_at=200.0)
        assert s2.queue_session_id == 7, f"queue_session_id changed from {phase.value}"
        assert s2.command_generation == 13, f"command_generation changed from {phase.value}"
        assert s2.track_attempt_id == 5, f"track_attempt_id changed from {phase.value}"


# ── begin_play_dispatch ───────────────────────────────────────────────

_OTHER_8_PHASES = [
    PlaybackPhase.IDLE,
    PlaybackPhase.DISPATCHING,
    PlaybackPhase.CONFIRMING,
    PlaybackPhase.PLAYING,
    PlaybackPhase.PAUSED,
    PlaybackPhase.STOPPED,
    PlaybackPhase.STOPPING,
    PlaybackPhase.FAILED,
]


@pytest.mark.parametrize("phase", [PlaybackPhase.RESOLVING, PlaybackPhase.SWITCHING])
def test_begin_play_dispatch_success(phase: PlaybackPhase):
    """RESOLVING or SWITCHING → DISPATCHING with all fields preserved."""
    s = _rich_state(phase)
    s2 = begin_play_dispatch(s, updated_at=200.0)
    assert s2.phase == PlaybackPhase.DISPATCHING
    assert s2.transition_reason == "begin_play_dispatch"
    assert s2.updated_at == 200.0
    # preserved
    assert s2.desired_track is s.desired_track
    assert s2.confirmed_track is s.confirmed_track
    assert s2.expected_end_at == s.expected_end_at
    assert s2.failure is s.failure
    assert s2.last_device_observation is s.last_device_observation
    assert s2.queue_session_id == s.queue_session_id
    assert s2.command_generation == s.command_generation
    assert s2.track_attempt_id == s.track_attempt_id


@pytest.mark.parametrize("phase", _OTHER_8_PHASES)
def test_begin_play_dispatch_reject(phase: PlaybackPhase):
    """All 8 other phases raise TransitionError and do not mutate original."""
    s = _rich_state(phase)
    original_phase = s.phase
    original_desired = s.desired_track
    with pytest.raises(TransitionError):
        begin_play_dispatch(s, updated_at=200.0)
    # original state unchanged
    assert s.phase == original_phase
    assert s.desired_track is original_desired


@pytest.mark.parametrize("phase", [PlaybackPhase.RESOLVING, PlaybackPhase.SWITCHING])
def test_begin_play_dispatch_does_not_mutate_input(phase: PlaybackPhase):
    """Input state is immutable after begin_play_dispatch."""
    s = _rich_state(phase)
    original_phase = s.phase
    original_desired = s.desired_track
    original_updated_at = s.updated_at
    s2 = begin_play_dispatch(s, updated_at=200.0)
    assert s.phase == original_phase
    assert s.desired_track is original_desired
    assert s.updated_at == original_updated_at
    assert s is not s2


def test_begin_play_dispatch_preserves_all_fields():
    """Exhaustive field preservation: no field is silently dropped or altered."""
    s = _rich_state(PlaybackPhase.RESOLVING)
    s2 = begin_play_dispatch(s, updated_at=200.0)
    # phase / updated_at / transition_reason are the only changes
    assert s2.phase == PlaybackPhase.DISPATCHING
    assert s2.updated_at == 200.0
    assert s2.transition_reason == "begin_play_dispatch"
    # everything else is identity-preserved (same object)
    assert s2.desired_track is s.desired_track
    assert s2.confirmed_track is s.confirmed_track
    assert s2.expected_end_at == 100.0
    assert s2.failure is s.failure
    assert s2.failure.count == 2
    assert s2.last_device_observation is s.last_device_observation
    assert s2.last_device_observation.status == 1
    assert s2.queue_session_id == 7
    assert s2.command_generation == 13
    assert s2.track_attempt_id == 5
