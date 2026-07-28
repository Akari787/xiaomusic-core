"""T03-G: runtime FAILED/degraded and successful recovery boundary tests.

Tests A-H cover the full failure lifecycle: report, degraded threshold,
stale guard, premature clear removal, confirmed recovery, FAILED stop,
and invalid-phase defense.
"""

from __future__ import annotations

import asyncio
import logging
import time
import types

import pytest

from xiaomusic.const import PLAY_TYPE_ALL
from xiaomusic.device_player import XiaoMusicDevice
from xiaomusic.playback.runtime_state import (
    LifecycleToken,
    PlaybackPhase,
    PlaybackRuntimeState,
    TrackReference,
)

# ── async noops (zero asyncio.sleep) ───────────────────────────────────

async def _noop():
    pass


async def _noop_return_none():
    return None


async def _noop_return_false(*args, **kwargs):
    return False


async def _noop_return_zero():
    return 0.0


async def _wait_arbiter_idle(d, *, timeout=5.0):
    """Wait until arbiter has no active or pending work."""
    arb = d._command_arbiter
    if arb is None:
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if arb.active_sequence is None and arb.pending_sequence is None:
            return
        await asyncio.sleep(0.01)
    raise TimeoutError("arbiter did not idle within timeout")


# ── fixtures ──────────────────────────────────────────────────────────

def _make_device() -> XiaoMusicDevice:
    """Create a XiaoMusicDevice via __new__ for isolated testing."""
    d = XiaoMusicDevice.__new__(XiaoMusicDevice)
    d.log = logging.getLogger("test-t03g")
    d._play_list_items = []
    d._current_index = -1
    d._play_session_id = 0
    d._last_cmd = ""
    d.is_playing = False
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
    d._timer_expiry_false_count = 0
    d._bg_confirm_false_count = 0
    d._timer_expiry_playing_grace_count = 0
    d._timer_expiry_unknown_grace_count = 0
    d._playlist_session_shuffled = False
    d._runtime_state = PlaybackRuntimeState()
    d.device = types.SimpleNamespace(
        did="did-t03g", play_type=PLAY_TYPE_ALL, hardware="OH2P",
        cur_playlist="全部", cur_music="", current_display_name="",
        current_entity_id="", current_playlist_item_id="",
        playlist2music={},
    )
    d.config = types.SimpleNamespace(
        delay_sec=0, verbose=False, ffmpeg_location="",
        jellyfin_proxy_mode="off",
    )
    d.event_bus = None
    d.group_name = "test"
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(
            music_list={"全部": []},
            is_music_exist=lambda n: True,
            get_music_duration=lambda n: _noop_return_zero(),
        ),
        analytics=types.SimpleNamespace(send_play_event=lambda *a, **k: _noop()),
        device_manager=types.SimpleNamespace(
            get_group_device_id_list=lambda g: [],
            get_group_devices=lambda g: {},
        ),
    )
    d._inflight_fast_stop_tasks = set()
    d._manual_nav_lock = asyncio.Lock()
    d._manual_nav_generation = 0
    d._manual_nav_target = None
    d._command_arbiter = None  # T04-B
    d.do_tts = _noop
    d.group_force_stop_xiaoai = _noop
    d.cancel_group_next_timer = _noop
    d._invalidate_manual_navigation = lambda reason: None
    d.auto_add_song = lambda cur, sec: _noop()
    d._refresh_runtime_volume = lambda context="": _noop_return_none()
    d._start_duration_probe = lambda name, sid, **kw: None
    d.set_next_music_timeout = lambda sec, token=None: _noop()
    d._schedule_playing_status_probe = lambda sid, name: None
    d._find_playlist_index = lambda *a, **kw: -1
    d.get_if_xiaoai_is_playing = _noop_return_false
    d._play = _noop_return_false
    d._play_next = _noop
    return d


def _enter_dispatching(d: XiaoMusicDevice) -> LifecycleToken:
    """Transition from IDLE to DISPATCHING using real wrappers, return token."""
    d._start_queue_session(updated_at=time.time())
    d._accept_command(updated_at=time.time())
    d._begin_runtime_play_request(
        desired_track=TrackReference(
            entity_id="e1", display_name="song1", source="test"
        ),
        updated_at=time.time(),
    )
    d._begin_runtime_play_dispatch(updated_at=time.time())
    return d._capture_lifecycle_token()


def _enter_failed(
    d: XiaoMusicDevice,
    *,
    count: int = 1,
    reason: str = "test_fail",
    degraded: bool = False,
) -> LifecycleToken:
    """Set device to FAILED phase via real DevicePlayer wrapper chain.

    Uses _report_runtime_failure (the real wrapper), not direct
    report_failure + _set_runtime_state.  Increments runtime runtime_state
    and returns a fresh token.
    """
    _enter_dispatching(d)
    for _i in range(count):
        d._report_runtime_failure(reason=reason, degraded=degraded, updated_at=time.time())
    return d._capture_lifecycle_token()


def _snapshot_all_legacy(d: XiaoMusicDevice) -> dict:
    """Snapshot all legacy failure-related fields."""
    return {
        "cnt": d._play_failed_cnt,
        "first_ts": d._play_fail_first_ts,
        "reason": d._play_fail_last_reason,
        "degraded": d._degraded,
        "degraded_notified": d._degraded_notified,
    }


def _assert_legacy_unchanged(d: XiaoMusicDevice, snap: dict, label: str = ""):
    """Assert all legacy failure fields match snapshot."""
    prefix = f"[{label}] " if label else ""
    assert d._play_failed_cnt == snap["cnt"], f"{prefix}cnt changed"
    assert d._play_fail_first_ts == snap["first_ts"], f"{prefix}first_ts changed"
    assert d._play_fail_last_reason == snap["reason"], f"{prefix}reason changed"
    assert d._degraded == snap["degraded"], f"{prefix}degraded changed"
    assert d._degraded_notified == snap["degraded_notified"], f"{prefix}degraded_notified changed"


# ── Test A: DISPATCHING failure → FAILED, runtime/legacy count=1, reason一致 ──

@pytest.mark.asyncio
async def test_A_dispatching_failure_count1_reason_match():
    """A single _handle_play_failure from DISPATCHING produces count=1 in both
    runtime and legacy, with matching reason."""
    d = _make_device()
    token = _enter_dispatching(d)

    await d._handle_play_failure(
        name="song1", sid=d._play_session_id, reason="player_play_failed", token=token
    )

    s = d.get_runtime_state()
    assert s.phase == PlaybackPhase.FAILED
    assert s.failure is not None
    assert s.failure.count == 1
    assert s.failure.reason == "player_play_failed"
    assert s.failure.degraded is False
    assert s.failure.last_failed_at is not None
    assert d._play_failed_cnt == 1
    assert d._play_fail_last_reason == "player_play_failed"
    assert d._play_fail_first_ts > 0


# ── Test B: 连续5个合法失败 → count=5, degraded=true, 不双计 ──

@pytest.mark.asyncio
async def test_B_five_consecutive_failures_degrades_without_double_count():
    """Five consecutive failures: count=5, degraded=True, no double count."""
    d = _make_device()

    for i in range(5):
        d._start_queue_session(updated_at=time.time())
        d._accept_command(updated_at=time.time())
        d._begin_runtime_play_request(
            desired_track=TrackReference(
                entity_id="e1", display_name="song1", source="test"
            ),
            updated_at=time.time(),
        )
        d._begin_runtime_play_dispatch(updated_at=time.time())
        token = d._capture_lifecycle_token()

        await d._handle_play_failure(
            name="song1", sid=d._play_session_id, reason=f"fail_{i + 1}", token=token,
        )

    s = d.get_runtime_state()
    assert s.phase == PlaybackPhase.FAILED
    assert s.failure is not None
    assert s.failure.count == 5
    assert s.failure.degraded is True
    assert d._play_failed_cnt == 5
    assert d._degraded is True


# ── Test C: elapsed>=60 单次新失败标degraded且count正确 ──

@pytest.mark.asyncio
async def test_C_elapsed_60s_marks_degraded_on_single_failure():
    """When first_fail_ts >= 60s ago, a single new failure degrades with count=1."""
    d = _make_device()
    token = _enter_dispatching(d)
    d._play_fail_first_ts = time.time() - 65
    d._play_failed_cnt = 0

    await d._handle_play_failure(
        name="song1", sid=d._play_session_id, reason="slow_fail", token=token
    )

    s = d.get_runtime_state()
    assert s.phase == PlaybackPhase.FAILED
    assert s.failure is not None
    assert s.failure.count == 1
    assert s.failure.degraded is True
    assert s.failure.reason == "slow_fail"
    assert d._play_failed_cnt == 1
    assert d._degraded is True


# ── Test D: stale (command/queue/attempt/sid) 四维参数化零写 ──────────

@pytest.mark.parametrize("mutation", ["command", "queue", "attempt", "sid"])
@pytest.mark.asyncio
async def test_D_stale_zero_writes(mutation: str):
    """Enter DISPATCHING, mutate one dimension, call _handle_play_failure.
    Assert zero writes to runtime failure AND all legacy fields."""
    d = _make_device()
    token = _enter_dispatching(d)

    # Snapshot
    s_before = d.get_runtime_state()
    failure_before = s_before.failure
    leg_before = _snapshot_all_legacy(d)
    old_sid = d._play_session_id

    # Single-dimension mutation
    if mutation == "command":
        d._accept_command(updated_at=time.time())
        use_sid = d._play_session_id
    elif mutation == "queue":
        d._start_queue_session(updated_at=time.time())
        use_sid = d._play_session_id
    elif mutation == "attempt":
        d._start_track_attempt(updated_at=time.time())
        use_sid = d._play_session_id
    else:  # sid — _bump_play_session does NOT change q/c/a
        d._bump_play_session(reason="sid_stale")
        use_sid = old_sid  # pass OLD sid to trigger sid mismatch

    await d._handle_play_failure(
        name="x", sid=use_sid, reason="fail", token=token,
    )

    # Assert zero writes
    s_after = d.get_runtime_state()
    assert s_after.phase == s_before.phase, f"{mutation}: phase changed"
    assert s_after.failure is failure_before, f"{mutation}: failure changed"
    _assert_legacy_unchanged(d, leg_before, mutation)


# ── D5: IDLE phase zero writes ────────────────────────────────────────

@pytest.mark.asyncio
async def test_D5_idle_phase_zero_writes():
    """IDLE → _handle_play_failure → zero writes."""
    d = _make_device()
    token = d._capture_lifecycle_token()
    leg = _snapshot_all_legacy(d)

    await d._handle_play_failure(
        name="x", sid=d._play_session_id, reason="err", token=token,
    )

    assert d.get_runtime_state().phase == PlaybackPhase.IDLE
    assert d.get_runtime_state().failure is None
    _assert_legacy_unchanged(d, leg, "IDLE")


# ── D6: STOPPED phase zero writes (via real chain) ────────────────────

@pytest.mark.asyncio
async def test_D6_stopped_phase_zero_writes():
    """STOPPED via real chain → _handle_play_failure → zero writes."""
    d = _make_device()
    # Real chain: IDLE → ... → PLAYING → STOPPING → STOPPED
    d._start_queue_session(updated_at=time.time())
    d._accept_command(updated_at=time.time())
    d._begin_runtime_play_request(
        desired_track=TrackReference(
            entity_id="e1", display_name="x", source="test"
        ),
        updated_at=time.time(),
    )
    d._begin_runtime_play_dispatch(updated_at=time.time())
    d._begin_runtime_confirmation(updated_at=time.time())
    d._confirm_runtime_playing(updated_at=time.time())
    d._begin_runtime_stop(updated_at=time.time())
    d._complete_runtime_stop(updated_at=time.time())
    assert d.get_runtime_state().phase == PlaybackPhase.STOPPED

    token = d._capture_lifecycle_token()
    leg = _snapshot_all_legacy(d)

    await d._handle_play_failure(
        name="x", sid=d._play_session_id, reason="err", token=token,
    )

    assert d.get_runtime_state().phase == PlaybackPhase.STOPPED
    assert d.get_runtime_state().failure is None
    _assert_legacy_unchanged(d, leg, "STOPPED")


# ── Test E: play/playlocal 不提前清 legacy/runtime failure ────────────

@pytest.mark.asyncio
async def test_E1_play_preserves_existing_failure():
    """play() downstream fails → existing runtime + legacy failure preserved.

    Adapted for T04-C2b: play() returns True immediately, physical work
    runs in arbiter background.  We wait for arbiter completion before
    asserting."""
    d = _make_device()
    _enter_failed(d, count=3, reason="prev_err", degraded=False)
    d._play_failed_cnt = 3
    d._play_fail_last_reason = "prev_err"
    d._play_fail_first_ts = time.time() - 30
    d._degraded = False
    d._degraded_notified = False
    leg_before = _snapshot_all_legacy(d)

    # Stub _play to fail immediately (return False)
    d._play = _noop_return_false
    try:
        # Call real play() — returns True immediately (arbiter async)
        await d.play(name="song1", search_key="")

        # Wait for arbiter to complete background work
        await _wait_arbiter_idle(d)

        # Assert nothing was cleared
        s_after = d.get_runtime_state()
        assert s_after.failure is not None, "runtime failure was cleared by play()"
        assert s_after.failure.count == 3
        assert s_after.failure.reason == "prev_err"
        _assert_legacy_unchanged(d, leg_before, "play")
    finally:
        await d.close_command_arbiter()


@pytest.mark.asyncio
async def test_E2_playlocal_preserves_existing_failure():
    """playlocal() downstream fails → existing runtime + legacy failure preserved.

    Adapted for T04-C2b: playlocal() returns True immediately, physical work
    runs in arbiter background."""
    d = _make_device()
    _enter_failed(d, count=2, reason="local_err", degraded=True)
    d._play_failed_cnt = 2
    d._play_fail_last_reason = "local_err"
    d._play_fail_first_ts = time.time() - 50
    d._degraded = True
    d._degraded_notified = True
    leg_before = _snapshot_all_legacy(d)

    # Stub _play_internal to fail immediately
    d._play_internal = lambda **kw: _noop_return_false()
    try:
        await d.playlocal(name="song2")

        # Wait for arbiter to complete background work
        await _wait_arbiter_idle(d)

        s_after = d.get_runtime_state()
        assert s_after.failure is not None, "runtime failure was cleared by playlocal()"
        assert s_after.failure.count == 2
        assert s_after.failure.degraded is True
        _assert_legacy_unchanged(d, leg_before, "playlocal")
    finally:
        await d.close_command_arbiter()


# ── Test F: confirmed PLAYING 清 runtime+legacy failure ───────────────

@pytest.mark.asyncio
async def test_F1_mark_play_started_clears_all():
    """_confirm_runtime_playing_for_attempt + _mark_play_started clears
    runtime failure AND all legacy fields."""
    d = _make_device()
    # Build CONFIRMING with existing failure via real chain
    d._start_queue_session(updated_at=time.time())
    d._accept_command(updated_at=time.time())
    d._begin_runtime_play_request(
        desired_track=TrackReference(
            entity_id="e1", display_name="recover", source="test"
        ),
        updated_at=time.time(),
    )
    d._begin_runtime_play_dispatch(updated_at=time.time())
    d._report_runtime_failure(reason="err1", degraded=False, updated_at=time.time())
    # Re-enter active → CONFIRMING
    d._start_queue_session(updated_at=time.time())
    d._accept_command(updated_at=time.time())
    d._begin_runtime_play_request(
        desired_track=TrackReference(
            entity_id="e1", display_name="recover", source="test"
        ),
        updated_at=time.time(),
    )
    d._begin_runtime_play_dispatch(updated_at=time.time())
    d._begin_runtime_confirmation(updated_at=time.time())
    token = d._capture_lifecycle_token()

    # Set matching legacy failure
    d._play_failed_cnt = 1
    d._play_fail_first_ts = time.time() - 10
    d._play_fail_last_reason = "err1"
    d._degraded = False
    d._degraded_notified = False
    d._play_session_id = 1
    d.is_playing = True

    # confirm_playing_for_attempt: enters PLAYING, clears runtime failure
    d._confirm_runtime_playing_for_attempt(token=token, updated_at=time.time())
    assert d.get_runtime_state().phase == PlaybackPhase.PLAYING
    assert d.get_runtime_state().failure is None

    # _mark_play_started: clears all legacy failure
    await d._mark_play_started(
        name="recover", sid=d._play_session_id, cur_playlist="全部", token=token,
    )
    assert d._play_failed_cnt == 0
    assert d._play_fail_first_ts == 0.0
    assert d._play_fail_last_reason == ""
    assert d._degraded is False
    assert d._degraded_notified is False


@pytest.mark.asyncio
async def test_F2_external_url_play_started_clears_all():
    """on_external_url_play_started clears runtime + legacy failure on success."""
    d = _make_device()
    # Build DISPATCHING with existing failure via real wrapper chain
    _enter_failed(d, count=2, reason="ext_err", degraded=False)
    d._play_failed_cnt = 2
    d._play_fail_last_reason = "ext_err"
    d._play_fail_first_ts = time.time() - 20
    d._degraded = False
    d._degraded_notified = False

    # Now re-enter a new attempt in DISPATCHING
    d._start_queue_session(updated_at=time.time())
    d._accept_command(updated_at=time.time())
    d._begin_runtime_play_request(
        desired_track=TrackReference(
            entity_id="e2", display_name="ext-track", source="external"
        ),
        updated_at=time.time(),
    )
    d._begin_runtime_play_dispatch(updated_at=time.time())
    token = d._capture_lifecycle_token()
    d._play_session_id = 3
    d.is_playing = False

    # Call on_external_url_play_started
    result = await d.on_external_url_play_started(
        context={"title": "ext-track"},
        resolved={"title": "ext-track", "entity_id": "e2", "duration_seconds": 30},
        token=token,
    )
    assert result is True
    assert d.get_runtime_state().phase == PlaybackPhase.PLAYING
    assert d.get_runtime_state().failure is None
    assert d._play_failed_cnt == 0
    assert d._play_fail_first_ts == 0.0
    assert d._play_fail_last_reason == ""
    assert d._degraded is False
    assert d._degraded_notified is False


@pytest.mark.asyncio
async def test_F3_stale_external_does_not_clear():
    """Stale external token → _clear_degraded_state NOT called."""
    d = _make_device()
    # Build DISPATCHING with failure
    _enter_failed(d, count=1, reason="ext_stale", degraded=False)
    d._play_failed_cnt = 1
    d._play_fail_last_reason = "ext_stale"
    d._play_fail_first_ts = time.time() - 5
    leg_before = _snapshot_all_legacy(d)

    # Re-enter DISPATCHING
    d._start_queue_session(updated_at=time.time())
    d._accept_command(updated_at=time.time())
    d._begin_runtime_play_request(
        desired_track=TrackReference(
            entity_id="e2", display_name="ext-track", source="external"
        ),
        updated_at=time.time(),
    )
    d._begin_runtime_play_dispatch(updated_at=time.time())
    token = d._capture_lifecycle_token()
    d._play_session_id = 5

    # Make token stale via command bump before calling
    d._accept_command(updated_at=time.time())

    result = await d.on_external_url_play_started(
        context={"title": "ext-track"},
        resolved={"title": "ext-track", "entity_id": "e2"},
        token=token,
    )
    # Stale → not cleared
    assert result is False
    _assert_legacy_unchanged(d, leg_before, "stale_external")


# ── Test G: FAILED stop → STOPPING → STOPPED 且 failure 保留 ──────────

@pytest.mark.asyncio
async def test_G_failed_stop_preserves_failure():
    """FAILED → stop() → STOPPING → STOPPED with failure preserved."""
    d = _make_device()
    _enter_failed(d, count=2, reason="some_err", degraded=False)
    d._play_failed_cnt = 2
    d._play_fail_last_reason = "some_err"
    d._play_fail_first_ts = time.time() - 10
    d._degraded = False
    d._degraded_notified = False

    stopped = asyncio.Event()

    class _Bus:
        def publish(self, event, **kwargs):
            stopped.set()

    d.event_bus = _Bus()
    try:
        assert await d.stop(arg1="notts") is True
        await asyncio.wait_for(stopped.wait(), timeout=5.0)

        s = d.get_runtime_state()
        assert s.phase == PlaybackPhase.STOPPED
        assert s.failure is not None
        assert s.failure.count == 2
        assert s.failure.reason == "some_err"
        assert s.expected_end_at is None
    finally:
        await d.close_command_arbiter()


@pytest.mark.asyncio
async def test_G2_stopped_stop_noop():
    """STOPPED → stop() → no-op (via real chain)."""
    d = _make_device()
    # Real chain to STOPPED
    d._start_queue_session(updated_at=time.time())
    d._accept_command(updated_at=time.time())
    d._begin_runtime_play_request(
        desired_track=TrackReference(entity_id="e1", display_name="x", source="test"),
        updated_at=time.time(),
    )
    d._begin_runtime_play_dispatch(updated_at=time.time())
    d._begin_runtime_confirmation(updated_at=time.time())
    d._confirm_runtime_playing(updated_at=time.time())
    d._begin_runtime_stop(updated_at=time.time())
    d._complete_runtime_stop(updated_at=time.time())
    assert d.get_runtime_state().phase == PlaybackPhase.STOPPED

    result = await d.stop(arg1="notts")
    assert result is False


# ── Test H: IDLE failure 入口零写 ─────────────────────────────────────

@pytest.mark.asyncio
async def test_H_idle_handle_play_failure_zero_writes():
    """_handle_play_failure from IDLE → zero runtime/legacy writes."""
    d = _make_device()
    token = d._capture_lifecycle_token()
    leg = _snapshot_all_legacy(d)

    await d._handle_play_failure(
        name="x", sid=d._play_session_id, reason="err", token=token,
    )

    assert d.get_runtime_state().phase == PlaybackPhase.IDLE
    assert d.get_runtime_state().failure is None
    _assert_legacy_unchanged(d, leg, "IDLE")


# ── degraded TTS: Event + wait_for, stale后无后续副作用 ───────────────

@pytest.mark.asyncio
async def test_degraded_tts_stale_no_retry():
    """5th failure blocks on TTS; new command during await → old task
    returns without creating retry/next.  Count/degraded written before
    await are preserved."""
    d = _make_device()
    # Build up 4 failures in both runtime and legacy
    for i in range(4):
        token_i = _enter_dispatching(d)
        await d._handle_play_failure(
            name="song1", sid=d._play_session_id, reason=f"err{i}", token=token_i,
        )
    # Now runtime + legacy both count=4, not degraded
    assert d._play_failed_cnt == 4
    assert d._degraded is False
    assert d._degraded_notified is False
    assert d.get_runtime_state().failure.count == 4

    tts_entered = asyncio.Event()
    tts_release = asyncio.Event()

    async def _blocking_tts(msg):
        tts_entered.set()
        await asyncio.wait_for(tts_release.wait(), timeout=5.0)

    d.do_tts = _blocking_tts

    # Enter DISPATCHING for 5th failure (begin_play_request preserves failure)
    token5 = _enter_dispatching(d)

    # Call _handle_play_failure — will block on TTS via degraded path
    fail_task = asyncio.create_task(
        d._handle_play_failure(
            name="song1", sid=d._play_session_id, reason="err5", token=token5,
        )
    )

    try:
        await asyncio.wait_for(tts_entered.wait(), timeout=5.0)

        # Runtime + legacy already written before TTS await
        assert d._play_failed_cnt == 5
        assert d._degraded is True
        assert d._degraded_notified is True
        assert d.get_runtime_state().phase == PlaybackPhase.FAILED
        assert d.get_runtime_state().failure.count == 5

        # Stale the token
        d._accept_command(updated_at=time.time())

        tts_release.set()
        await fail_task

        # After stale return: no additional writes
        assert d._play_failed_cnt == 5  # unchanged
        assert d.get_runtime_state().failure.count == 5
        # degraded written BEFORE await stays
        assert d._degraded is True
    finally:
        if not fail_task.done():
            tts_release.set()
            fail_task.cancel()
            try:
                await fail_task
            except asyncio.CancelledError:
                pass


# ── runtime/legacy count always matches ────────────────────────────────

@pytest.mark.asyncio
async def test_runtime_legacy_count_always_match():
    """After any number of real failures, runtime.count == legacy cnt."""
    d = _make_device()

    for i in range(1, 6):
        d._start_queue_session(updated_at=time.time())
        d._accept_command(updated_at=time.time())
        d._begin_runtime_play_request(
            desired_track=TrackReference(
                entity_id="e1", display_name="song1", source="test"
            ),
            updated_at=time.time(),
        )
        d._begin_runtime_play_dispatch(updated_at=time.time())
        token = d._capture_lifecycle_token()

        await d._handle_play_failure(
            name="song1", sid=d._play_session_id, reason=f"fail_{i}", token=token,
        )

        s = d.get_runtime_state()
        assert s.failure is not None
        assert s.failure.count == i, f"iter {i}: runtime count mismatch"
        assert d._play_failed_cnt == i, f"iter {i}: legacy count mismatch"


# ── AST guards (only these use direct AST analysis) ────────────────────

def test_no_fixed_sleep_in_failure_path():
    """_handle_play_failure contains no time.sleep()."""
    import ast

    with open("xiaomusic/device_player.py") as f:
        tree = ast.parse(f.read())

    class SleepChecker(ast.NodeVisitor):
        def __init__(self):
            self.in_handle_fail = False
            self.violations: list[int] = []

        def visit_AsyncFunctionDef(self, node):
            if node.name == "_handle_play_failure":
                self.in_handle_fail = True
                self.generic_visit(node)
                self.in_handle_fail = False
            else:
                self.generic_visit(node)

        def visit_Call(self, node):
            if self.in_handle_fail:
                if isinstance(node.func, ast.Name) and node.func.id == "sleep":
                    self.violations.append(node.lineno)
                if (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "sleep"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "time"
                ):
                    self.violations.append(node.lineno)
            self.generic_visit(node)

    checker = SleepChecker()
    checker.visit(tree)
    assert not checker.violations, f"time.sleep() at lines {checker.violations}"


def test_report_runtime_failure_is_only_report_failure_caller():
    """AST: report_failure only called from _report_runtime_failure."""
    import ast

    with open("xiaomusic/device_player.py") as f:
        tree = ast.parse(f.read())

    class Checker(ast.NodeVisitor):
        def __init__(self):
            self.func_stack: list[str] = []
            self.violations: list[tuple[int, str]] = []

        def visit_FunctionDef(self, node):
            self.func_stack.append(node.name)
            self.generic_visit(node)
            self.func_stack.pop()

        def visit_AsyncFunctionDef(self, node):
            self.func_stack.append(node.name)
            self.generic_visit(node)
            self.func_stack.pop()

        def visit_Call(self, node):
            ctx = self.func_stack[-1] if self.func_stack else "<module>"
            if isinstance(node.func, ast.Name) and node.func.id == "report_failure":
                if ctx != "_report_runtime_failure":
                    self.violations.append((node.lineno, ctx))
            self.generic_visit(node)

    checker = Checker()
    checker.visit(tree)
    assert not checker.violations, f"direct report_failure calls: {checker.violations}"


def test_play_playlocal_no_direct_await_play_or_play_internal():
    """AST: public play() and playlocal() must not directly await
    _play() or _play_internal().  T04-C2b gate: physical play only
    enters via arbiter executor → _execute_play_intent."""
    import ast

    with open("xiaomusic/device_player.py") as f:
        tree = ast.parse(f.read())

    class Checker(ast.NodeVisitor):
        def __init__(self):
            self.current_func = None
            self.violations: list[tuple[int, str]] = []
            self._in_play = False
            self._in_playlocal = False

        def visit_AsyncFunctionDef(self, node):
            prev = self.current_func
            self.current_func = node.name
            self._in_play = (node.name == "play")
            self._in_playlocal = (node.name == "playlocal")
            self.generic_visit(node)
            self.current_func = prev

        def visit_Await(self, node):
            if self._in_play or self._in_playlocal:
                call = node.value
                if isinstance(call, ast.Call):
                    if isinstance(call.func, ast.Attribute):
                        called = call.func.attr
                        if called in ("_play", "_play_internal"):
                            self.violations.append(
                                (node.lineno, self.current_func, called)
                            )
            self.generic_visit(node)

    checker = Checker()
    checker.visit(tree)
    assert not checker.violations, (
        f"play/playlocal directly awaiting _play/_play_internal: {checker.violations}"
    )
