"""T05-B: failure retry-owner lifecycle and policy tests (A-M).

Uses real ``XiaoMusicDevice.__new__`` fixture with real runtime reducer,
real ``_handle_play_failure``, real ``_submit_auto_retry`` → arbiter.
Only stubs: cloud status, physical play, backoff (Event waiter), TTS.
"""

from __future__ import annotations

import asyncio
import logging
import time
import types

import pytest

from xiaomusic.const import PLAY_TYPE_ALL, PLAY_TYPE_SIN
from xiaomusic.device_player import XiaoMusicDevice
from xiaomusic.playback.runtime_state import (
    LifecycleToken,
    PlaybackPhase,
    PlaybackRuntimeState,
    TrackReference,
)

# ── helpers ──────────────────────────────────────────────────────────


def _make_t05b_device(*, did: str = "t05b", play_type: int = PLAY_TYPE_ALL) -> XiaoMusicDevice:
    """Minimal XiaoMusicDevice via __new__. All fields explicitly initialised."""
    d = XiaoMusicDevice.__new__(XiaoMusicDevice)
    d.log = logging.getLogger("test-t05b")
    d._runtime_state = PlaybackRuntimeState()
    d._play_list_items = []
    d._current_index = -1
    d._play_session_id = 1
    d._last_cmd = "play"
    d.is_playing = True
    d._start_time = 0
    d._paused_time = 0
    d._duration = 0
    d._play_failed_cnt = 0
    d._play_fail_first_ts = 0.0
    d._play_fail_last_reason = ""
    d._degraded = False
    d._degraded_notified = False
    d._last_volume = 0
    d._next_timer = None
    d._autonext_guard_task = None
    d._playback_confirm_task = None
    d._playback_status_probe_task = None
    # T05-B owner fields
    d._failure_retry_task = None
    d._failure_retry_meta: dict = {}
    d._failure_retry_last_status = "idle"
    d._failure_retry_last_error = ""
    d._failure_retry_done_event = None
    d._timer_expiry_false_count = 0
    d._bg_confirm_false_count = 0
    d._timer_expiry_playing_grace_count = 0
    d._timer_expiry_unknown_grace_count = 0
    d._playlist_session_shuffled = False
    d._inflight_fast_stop_tasks = set()
    d._duration_probe_task = None
    d._manual_nav_lock = asyncio.Lock()
    d._manual_nav_generation = 0
    d._manual_nav_target = None
    d._command_arbiter = None
    d._external_context_registry: dict = {}
    d._external_context_registry_order: list = []
    d._external_context_next_id = 0
    d.device = types.SimpleNamespace(
        did=did,
        play_type=play_type,
        hardware="OH2P",
        cur_playlist="全部",
        cur_music="",
        current_display_name="",
        current_entity_id="",
        current_playlist_item_id="",
        playlist2music={},
    )
    d.config = types.SimpleNamespace(
        delay_sec=0,
        verbose=False,
        ffmpeg_location="",
        jellyfin_proxy_mode="off",
    )
    d.event_bus = None
    d.group_name = "test"
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(
            get_music_duration=lambda name: _async_none(),
        ),
        analytics=types.SimpleNamespace(
            send_play_event=lambda name, sec, hw: _async_none(),
        ),
    )
    return d


async def _async_none():
    return None


async def _async_return(val):
    return val


def _stage_dispatching(d: XiaoMusicDevice) -> None:
    """Transition device to DISPATCHING (legal failure target)."""
    d._runtime_state = PlaybackRuntimeState(phase=PlaybackPhase.DISPATCHING)


def _install_backoff_gate(d: XiaoMusicDevice) -> asyncio.Event:
    """Replace ``_wait_failure_retry_backoff`` with Event waiter."""
    gate = asyncio.Event()

    async def _waiter(delay):
        await asyncio.wait_for(gate.wait(), timeout=5.0)

    d._wait_failure_retry_backoff = _waiter
    return gate


def _install_backoff_raiser(d: XiaoMusicDevice, exc: Exception) -> None:
    """Replace ``_wait_failure_retry_backoff`` with immediate raise."""

    async def _raiser(delay):
        raise exc

    d._wait_failure_retry_backoff = _raiser


def _install_cloud_waiter(
    d: XiaoMusicDevice,
    *,
    result: bool | None = None,
    exc: Exception | None = None,
) -> asyncio.Event:
    """Replace ``get_if_xiaoai_is_playing`` with gate + controlled result."""
    gate = asyncio.Event()

    async def _cloud(device_id=None):
        await asyncio.wait_for(gate.wait(), timeout=5.0)
        if exc is not None:
            raise exc
        return result

    d.get_if_xiaoai_is_playing = _cloud
    return gate


def _install_cloud_fast(d: XiaoMusicDevice, *, result: bool | None = None, exc: Exception | None = None):
    """Non-blocking cloud stub."""

    async def _cloud(device_id=None):
        if exc is not None:
            raise exc
        return result

    d.get_if_xiaoai_is_playing = _cloud


def _track_submits(d: XiaoMusicDevice) -> list[dict]:
    """Wrap device._submit_auto_retry to record calls."""
    calls: list[dict] = []
    _orig = d._submit_auto_retry

    def _tracked(kind, *, source_token, sid, payload=None, reason=""):
        calls.append({
            "kind": kind,
            "payload": dict(payload or {}),
            "reason": reason,
            "sid": sid,
        })
        return _orig(kind, source_token=source_token, sid=sid, payload=payload, reason=reason)

    d._submit_auto_retry = _tracked
    return calls


def _install_physical_stubs(d: XiaoMusicDevice):
    """Stub physical _play / _play_next / _execute_stop_intent for arbiter."""
    d._play = lambda name="", **kw: _async_none()
    d._play_next = lambda command_already_accepted=False: _async_none()
    d._execute_stop_intent = lambda payload: _async_none()


async def _wait_done(d: XiaoMusicDevice, *, timeout: float = 10.0) -> None:
    """Wait for the retry-done callback via its Event."""
    evt = d._failure_retry_done_event
    if evt is not None:
        await asyncio.wait_for(evt.wait(), timeout=timeout)


# ── A: policy boundaries ─────────────────────────────────────────────


class TestA_PolicyBoundaries:
    def test_a1_count1_retry_same(self):
        from xiaomusic.playback.completion_policy import (
            FailureAction,
            decide_failure_action,
        )

        d = decide_failure_action(1, 0)
        assert d.action is FailureAction.RETRY_SAME
        assert d.delay == 1.0

    def test_a2_count2_retry_same(self):
        from xiaomusic.playback.completion_policy import (
            FailureAction,
            decide_failure_action,
        )

        d = decide_failure_action(2, 0)
        assert d.action is FailureAction.RETRY_SAME
        assert d.delay == 2.0

    def test_a3_count3_retry_next(self):
        from xiaomusic.playback.completion_policy import (
            FailureAction,
            decide_failure_action,
        )

        d = decide_failure_action(3, 0)
        assert d.action is FailureAction.RETRY_NEXT
        assert d.delay == 4.0

    def test_a4_count4_retry_next(self):
        from xiaomusic.playback.completion_policy import (
            FailureAction,
            decide_failure_action,
        )

        d = decide_failure_action(4, 0)
        assert d.action is FailureAction.RETRY_NEXT
        assert d.delay == 8.0

    def test_a5_count5_degraded(self):
        from xiaomusic.playback.completion_policy import (
            FailureAction,
            decide_failure_action,
        )

        d = decide_failure_action(5, 0)
        assert d.action is FailureAction.DEGRADED
        assert d.delay == 0.0

    def test_a6_elapsed60_degraded(self):
        from xiaomusic.playback.completion_policy import (
            FailureAction,
            decide_failure_action,
        )

        d = decide_failure_action(1, 60)
        assert d.action is FailureAction.DEGRADED

    def test_a7_single_play_stop(self):
        from xiaomusic.playback.completion_policy import (
            FailureAction,
            decide_failure_action,
        )

        d = decide_failure_action(1, 0, True)
        assert d.action is FailureAction.STOP
        assert d.delay == 0.0

    def test_a8_delay_cap(self):
        from xiaomusic.playback.completion_policy import decide_failure_action

        d = decide_failure_action(4, 0)
        assert d.delay == 8.0


# ── B: count1 pending → RETRY_SAME ────────────────────────────────────


@pytest.mark.asyncio
async def test_B_count1_pending_release_retry_same():
    """count=1 → pending snapshot, release gate, RETRY_SAME submit, c+1."""
    d = _make_t05b_device()
    _stage_dispatching(d)
    _install_physical_stubs(d)
    _install_cloud_fast(d, result=False)
    gate = _install_backoff_gate(d)
    _submits = _track_submits(d)
    token = d._capture_lifecycle_token()
    initial_c = d.get_runtime_state().command_generation

    await d._handle_play_failure(name="b", sid=1, reason="r", token=token)

    st = d.get_failure_retry_status()
    assert st["status"] == "pending"
    assert st["active"] is True
    assert st["action"] == "retry_same"
    assert st["count"] == 1
    assert st["reason"] == "r"

    gate.set()
    await _wait_done(d)

    assert len(_submits) == 1
    assert _submits[0]["kind"].value == "retry"
    assert _submits[0]["payload"].get("retry_same_song") is True
    assert _submits[0]["payload"].get("name") == "b"
    assert d.get_runtime_state().command_generation == initial_c + 1
    assert d.get_failure_retry_status()["status"] == "done"


# ── C: count3 → RETRY_NEXT ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_C_count3_retry_next():
    """count=3 → RETRY_NEXT submit."""
    d = _make_t05b_device()
    _stage_dispatching(d)
    _install_physical_stubs(d)
    _install_cloud_fast(d, result=False)
    d._play_failed_cnt = 2
    d._play_fail_first_ts = time.time() - 5
    gate = _install_backoff_gate(d)
    _submits = _track_submits(d)
    token = d._capture_lifecycle_token()

    await d._handle_play_failure(name="c", sid=1, reason="r", token=token)

    st = d.get_failure_retry_status()
    assert st["count"] == 3
    assert st["action"] == "retry_next"

    gate.set()
    await _wait_done(d)

    assert len(_submits) == 1
    assert _submits[0]["payload"].get("retry_same_song") is False


# ── D: second failure cancels first ───────────────────────────────────


@pytest.mark.asyncio
async def test_D_second_failure_cancels_first():
    """Second failure cancels+awaits first retry; only second can submit."""
    d = _make_t05b_device()
    _stage_dispatching(d)
    _install_physical_stubs(d)
    _install_cloud_fast(d, result=False)
    gate = _install_backoff_gate(d)
    _submits = _track_submits(d)
    token = d._capture_lifecycle_token()

    # First failure
    await d._handle_play_failure(name="d1", sid=1, reason="r1", token=token)
    first_task = d._failure_retry_task
    assert first_task is not None
    assert not first_task.done()

    # Second failure (same token) — must cancel first
    await d._handle_play_failure(name="d2", sid=1, reason="r2", token=token)
    assert first_task.cancelled() or first_task.done()
    second_task = d._failure_retry_task
    assert second_task is not None
    assert second_task is not first_task

    gate.set()
    await _wait_done(d)

    # Only the second task should have submitted
    assert len(_submits) == 1
    assert _submits[0]["payload"].get("name") == "d2"


# ── E: stop / _bump_session during backoff cancels ────────────────────


@pytest.mark.asyncio
async def test_E_stop_during_backoff_cancels_zero_submit():
    """Public stop during backoff → cancel, zero submit."""
    d = _make_t05b_device()
    _stage_dispatching(d)
    _install_physical_stubs(d)
    _install_cloud_fast(d, result=False)
    gate = _install_backoff_gate(d)
    _submits = _track_submits(d)
    token = d._capture_lifecycle_token()

    # Create pending retry
    await d._handle_play_failure(name="e", sid=1, reason="r", token=token)
    assert d.get_failure_retry_status()["active"] is True

    # Public stop bumps sid, cancels task
    # Patch stop internals to avoid real physical work
    d._begin_runtime_stop = lambda updated_at: d._set_runtime_state(
        __import__("xiaomusic.playback.runtime_state", fromlist=["begin_stop"]).begin_stop(
            d.get_runtime_state(), updated_at=updated_at
        )
    )
    d._invalidate_manual_navigation = lambda reason: None

    await d.stop(arg1="notts")
    await _wait_done(d)  # done callback has fired

    gate.set()  # release backoff (task already cancelled, no submit)
    assert len(_submits) == 0
    assert d.get_failure_retry_status()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_E2_bump_session_during_backoff_cancels():
    """_bump_play_session during backoff cancels retry."""
    d = _make_t05b_device()
    _stage_dispatching(d)
    _install_physical_stubs(d)
    _install_cloud_fast(d, result=False)
    gate = _install_backoff_gate(d)
    _submits = _track_submits(d)
    token = d._capture_lifecycle_token()

    await d._handle_play_failure(name="e2", sid=1, reason="r", token=token)
    assert d.get_failure_retry_status()["active"] is True

    d._bump_play_session(reason="test_bump")
    await _wait_done(d)

    assert d.get_failure_retry_status()["status"] == "cancelled"
    gate.set()
    assert len(_submits) == 0


# ── F: token stale during cloud → zero submit ────────────────────────


@pytest.mark.asyncio
async def test_F_token_stale_during_cloud_zero_submit():
    """Token goes stale during blocked cloud check → zero submit."""
    d = _make_t05b_device()
    _stage_dispatching(d)
    _install_physical_stubs(d)
    backoff_gate = _install_backoff_gate(d)
    cloud_gate = _install_cloud_waiter(d, result=False)
    _submits = _track_submits(d)
    token = d._capture_lifecycle_token()

    await d._handle_play_failure(name="f", sid=1, reason="r", token=token)

    # Release backoff → runner enters cloud check and blocks
    backoff_gate.set()

    # Make token stale
    d._accept_command(updated_at=time.time())

    # Release cloud gate → runner finds stale token → return
    cloud_gate.set()
    await _wait_done(d)

    assert len(_submits) == 0
    assert d.get_failure_retry_status()["status"] == "done"


# ── G: cloud True → zero submit ──────────────────────────────────────


@pytest.mark.asyncio
async def test_G_cloud_playing_true_zero_submit():
    """Cloud reports playing=True → retry skips, zero submit."""
    d = _make_t05b_device()
    _stage_dispatching(d)
    _install_physical_stubs(d)
    gate = _install_backoff_gate(d)
    _install_cloud_fast(d, result=True)
    _submits = _track_submits(d)
    token = d._capture_lifecycle_token()

    await d._handle_play_failure(name="g", sid=1, reason="r", token=token)

    gate.set()
    await _wait_done(d)

    assert len(_submits) == 0
    st = d.get_failure_retry_status()
    assert st["status"] == "done"
    assert st["active"] is False


# ── H: cloud exception → still submit ────────────────────────────────


@pytest.mark.asyncio
async def test_H_cloud_exception_still_submits():
    """Cloud raises exception → still current → submit proceeds."""
    d = _make_t05b_device()
    _stage_dispatching(d)
    _install_physical_stubs(d)
    gate = _install_backoff_gate(d)
    _install_cloud_fast(d, exc=RuntimeError("cloud_err"))
    _submits = _track_submits(d)
    token = d._capture_lifecycle_token()

    await d._handle_play_failure(name="h", sid=1, reason="r", token=token)

    gate.set()
    await _wait_done(d)

    assert len(_submits) == 1
    assert _submits[0]["payload"].get("name") == "h"


# ── I: confirmed PLAYING cancels pending ──────────────────────────────


@pytest.mark.asyncio
async def test_I_confirmed_playing_cancels_pending_clears_failure():
    """Real confirmed PLAYING chain cancels pending retry + clears all failure.

    Uses real state machine: FAILED → RESOLVING (begin_play_request, failure preserved)
    → DISPATCHING → CONFIRMING → PLAYING (confirm_playing, failure cleared).
    Then _mark_play_started triggers _clear_degraded_state → legacy + retry cleared.
    Only I/O leaves stubbed."""
    d = _make_t05b_device()
    _install_physical_stubs(d)
    _install_cloud_fast(d, result=False)
    gate = _install_backoff_gate(d)
    _submits = _track_submits(d)

    # ── stage: FAILED with runtime FailureState + legacy failure ──
    d._runtime_state = PlaybackRuntimeState(phase=PlaybackPhase.DISPATCHING)
    d._report_runtime_failure(reason="prev", degraded=False, updated_at=time.time())
    assert d.get_runtime_state().phase == PlaybackPhase.FAILED
    assert d.get_runtime_state().failure is not None
    assert d.get_runtime_state().failure.count == 1

    d._play_failed_cnt = 2
    d._play_fail_first_ts = time.time() - 10
    d._play_fail_last_reason = "prev"
    d._degraded = False

    # ── create pending retry from FAILED phase ──
    old_token = d._capture_lifecycle_token()
    await d._handle_play_failure(name="i", sid=1, reason="r", token=old_token)
    assert d.get_failure_retry_status()["active"] is True  # pending retry
    assert d._play_failed_cnt == 3

    # ── real new-play chain: FAILED → RESOLVING (begin_play_request preserves failure) ──
    d._begin_runtime_play_request(
        desired_track=TrackReference(display_name="i"),
        updated_at=time.time(),
    )
    assert d.get_runtime_state().phase == PlaybackPhase.RESOLVING
    assert d.get_runtime_state().failure is not None  # preserved

    # ── RESOLVING → DISPATCHING ──
    d._begin_runtime_play_dispatch(updated_at=time.time())
    assert d.get_runtime_state().phase == PlaybackPhase.DISPATCHING

    # ── DISPATCHING → CONFIRMING ──
    d._begin_runtime_confirmation(updated_at=time.time())
    assert d.get_runtime_state().phase == PlaybackPhase.CONFIRMING
    assert d.get_runtime_state().failure is not None  # still preserved

    # capture token for the confirm/playback-started chain
    token = d._capture_lifecycle_token()

    # ── CONFIRMING → PLAYING (failure cleared) ──
    ok = d._confirm_runtime_playing_for_attempt(token=token, updated_at=time.time())
    assert ok is True
    assert d.get_runtime_state().phase == PlaybackPhase.PLAYING
    assert d.get_runtime_state().failure is None

    # ── stub I/O leaves for _mark_play_started ──
    d._refresh_runtime_volume = lambda context: _async_none()
    d.event_bus = types.SimpleNamespace(publish=lambda event, device_id: None)
    d.xiaomusic.music_library.get_music_duration = lambda name: _async_return(0.0)
    d.xiaomusic.analytics.send_play_event = lambda name, sec, hw: _async_none()

    # ── real _mark_play_started → _clear_degraded_state → cancel retry ──
    await d._mark_play_started(name="i", sid=1, cur_playlist="全部", token=token)
    await _wait_done(d)

    # ── assert complete recovery boundary ──
    assert d.get_runtime_state().phase == PlaybackPhase.PLAYING
    assert d.get_runtime_state().failure is None
    assert d._play_failed_cnt == 0
    assert d._play_fail_first_ts == 0.0
    assert d._play_fail_last_reason == ""
    assert d._degraded is False
    assert d._degraded_notified is False
    st = d.get_failure_retry_status()
    assert st["status"] == "cancelled"
    assert st["active"] is False

    gate.set()
    assert len(_submits) == 0


# ── J: degraded → one TTS, no retry ──────────────────────────────────


@pytest.mark.asyncio
async def test_J_degraded_one_tts_no_retry():
    """count >= 5 → degraded, one TTS, no retry task."""
    d = _make_t05b_device()
    _stage_dispatching(d)
    _install_physical_stubs(d)
    _install_cloud_fast(d, result=False)
    gate = _install_backoff_gate(d)
    _submits = _track_submits(d)
    token = d._capture_lifecycle_token()

    d._play_failed_cnt = 4
    d._play_fail_first_ts = time.time() - 5
    tts_calls: list[str] = []
    d.do_tts = lambda msg: tts_calls.append(msg) or _async_none()

    # 5th failure → degraded
    await d._handle_play_failure(name="j", sid=1, reason="r", token=token)

    assert d._degraded is True
    assert len(tts_calls) == 1
    assert d._failure_retry_task is None
    assert d.get_failure_retry_status()["action"] == "degraded"
    assert d.get_failure_retry_status()["active"] is False

    # 6th failure → no second TTS (already notified)
    await d._handle_play_failure(name="j2", sid=1, reason="r2", token=token)
    assert len(tts_calls) == 1

    gate.set()
    assert len(_submits) == 0


# ── K: SIN → public STOP → arbiter → STOPPED ─────────────────────────


@pytest.mark.asyncio
async def test_K_sin_stop_via_arbiter():
    """SIN mode failure → public stop → arbiter STOP → STOPPED."""
    d = _make_t05b_device(play_type=PLAY_TYPE_SIN)
    _stage_dispatching(d)
    _install_physical_stubs(d)

    stop_exec_payloads: list[dict] = []

    # Stub stop internals
    d._begin_runtime_stop = lambda updated_at: d._set_runtime_state(
        __import__("xiaomusic.playback.runtime_state", fromlist=["begin_stop"]).begin_stop(
            d.get_runtime_state(), updated_at=updated_at
        )
    )
    d._invalidate_manual_navigation = lambda reason: None
    d.do_tts = lambda msg: _async_none()
    stop_done_evt = asyncio.Event()

    async def _tracked_stop_with_event(payload):
        stop_exec_payloads.append(dict(payload or {}))
        d._complete_runtime_stop(updated_at=time.time())
        stop_done_evt.set()

    d._execute_stop_intent = _tracked_stop_with_event

    token = d._capture_lifecycle_token()
    await d._handle_play_failure(name="k", sid=1, reason="r", token=token)

    # Wait for STOP executor via Event
    await asyncio.wait_for(stop_done_evt.wait(), timeout=5.0)

    assert len(stop_exec_payloads) >= 1
    assert d.get_runtime_state().phase == PlaybackPhase.STOPPED


# ── L: runner exception consumed by done callback ─────────────────────


@pytest.mark.asyncio
async def test_L_runner_exception_done_callback_consumed():
    """Backoff waiter raises → done callback sets last_error, status=done."""
    d = _make_t05b_device()
    _stage_dispatching(d)
    _install_physical_stubs(d)
    _install_cloud_fast(d, result=False)
    _install_backoff_raiser(d, RuntimeError("backoff_boom"))
    _submits = _track_submits(d)
    token = d._capture_lifecycle_token()

    await d._handle_play_failure(name="l", sid=1, reason="r", token=token)

    await _wait_done(d)

    st = d.get_failure_retry_status()
    assert st["status"] == "done"
    assert st["active"] is False
    assert "backoff_boom" in st["last_error"]
    assert len(_submits) == 0  # never reached submit


# ── L2: runner cloud exception with stale sid ────────────────────────


@pytest.mark.asyncio
async def test_L2_cloud_exception_during_runner_consumed():
    """Cloud exception inside runner with stale sid → zero submit, status done."""
    d = _make_t05b_device()
    _stage_dispatching(d)
    _install_physical_stubs(d)
    gate = _install_backoff_gate(d)
    _install_cloud_fast(d, exc=RuntimeError("cloud_fail"))
    _submits = _track_submits(d)
    token = d._capture_lifecycle_token()

    await d._handle_play_failure(name="l2", sid=1, reason="r", token=token)

    # Make sid stale WITHOUT cancelling the task.
    # Runner will catch cloud exception, find sid stale → return normally.
    d._play_session_id += 1

    gate.set()
    await _wait_done(d)

    st = d.get_failure_retry_status()
    assert st["status"] == "done"
    assert st["active"] is False
    assert len(_submits) == 0  # sid stale prevents submit


# ── M: close cleanup ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_M_close_cleanup_no_pending():
    """close_command_arbiter cancels+awaits retry, no pending task."""
    d = _make_t05b_device()
    _stage_dispatching(d)
    _install_physical_stubs(d)
    _install_cloud_fast(d, result=False)
    gate = _install_backoff_gate(d)
    _submits = _track_submits(d)
    token = d._capture_lifecycle_token()

    await d._handle_play_failure(name="m", sid=1, reason="r", token=token)
    assert d.get_failure_retry_status()["active"] is True

    await d.close_command_arbiter()

    # close_command_arbiter calls _await_cancelled_failure_retry
    # which cancels + awaits → done callback fires
    assert d._failure_retry_task is None
    assert d.get_failure_retry_status()["active"] is False
    assert d.get_failure_retry_status()["status"] in {"cancelled", "done"}

    gate.set()
    assert len(_submits) == 0


# ── status snapshot token safety ──────────────────────────────────────


@pytest.mark.asyncio
async def test_snapshot_token_is_plain_dict():
    """Token in status must be a plain dict, never a LifecycleToken object."""
    d = _make_t05b_device()
    _stage_dispatching(d)
    _install_physical_stubs(d)
    _install_cloud_fast(d, result=False)
    gate = _install_backoff_gate(d)
    _track_submits(d)
    token = d._capture_lifecycle_token()

    await d._handle_play_failure(name="s", sid=1, reason="r", token=token)

    st = d.get_failure_retry_status()
    tk = st["token"]
    assert isinstance(tk, dict)
    assert not isinstance(tk, LifecycleToken)
    assert "queue_session_id" in tk
    assert "command_generation" in tk
    assert "track_attempt_id" in tk

    gate.set()
    await _wait_done(d)


# ── max one task invariant ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_max_one_retry_task_at_any_time():
    """At most one retry task exists concurrently."""
    d = _make_t05b_device()
    _stage_dispatching(d)
    _install_physical_stubs(d)
    _install_cloud_fast(d, result=False)
    gate = _install_backoff_gate(d)
    _track_submits(d)
    token = d._capture_lifecycle_token()

    for i in range(4):
        await d._handle_play_failure(name=f"x{i}", sid=1, reason="r", token=token)

    # Only the last task should exist; previous were cancelled
    t = d._failure_retry_task
    assert t is not None
    assert not t.done()
    assert not t.cancelled()

    gate.set()
    await _wait_done(d)
    assert d._failure_retry_task is None
