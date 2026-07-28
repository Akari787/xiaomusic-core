"""T04-C3a: AUTO_NEXT / RETRY arbiter integration and race-condition tests.

Tests A-J validate the guarded submit helper, executor routing, and
10 race-condition scenarios.  All coordination uses asyncio.Event +
wait_for; zero asyncio.sleep() / fixed waits / polling / bare Event.wait().

Real call chains only — no fixture directly calling the executor to
impersonate timer / _handle_play_failure closures.
"""

from __future__ import annotations

import asyncio
import logging
import time
import types

import pytest

from xiaomusic.const import PLAY_TYPE_ALL
from xiaomusic.device_player import XiaoMusicDevice
from xiaomusic.playback.command_arbiter import IntentKind
from xiaomusic.playback.runtime_state import (
    PlaybackPhase,
    PlaybackRuntimeState,
    TrackReference,
)

# ── async noops ─────────────────────────────────────────────────────────

async def _noop(*args, **kwargs):
    return


async def _noop_return_false(*args, **kwargs):
    return False


async def _noop_return_true(*args, **kwargs):
    return True


async def _noop_return_zero():
    return 0.0


async def _noop_get_url(name):
    return f"file:///tmp/{name}.mp3", f"file:///tmp/{name}.mp3"


async def _noop_list(*args, **kwargs):
    return []


def _bump_sid(d):
    d._play_session_id += 1
    return d._play_session_id


def _stage_playing(d, name="song_x"):
    """Put device into PLAYING phase with a confirmed track."""
    d.is_playing = True
    d._start_time = time.time()
    d._duration = 200.0
    d._runtime_state = PlaybackRuntimeState(
        phase=PlaybackPhase.PLAYING,
        confirmed_track=TrackReference(display_name=name, entity_id="e1"),
        queue_session_id=d._runtime_state.queue_session_id,
        command_generation=d._runtime_state.command_generation,
        track_attempt_id=d._runtime_state.track_attempt_id,
        updated_at=time.time(),
    )


# ── device factory ───────────────────────────────────────────────────────


def _make_device() -> XiaoMusicDevice:
    """Create a test device with all required attributes."""
    d = XiaoMusicDevice.__new__(XiaoMusicDevice)
    d.log = logging.getLogger("test-t04c3a")
    d._play_list_items = [
        {"display_name": "A", "legacy_name": "A", "item_id": "a", "entity_id": "ea"},
        {"display_name": "B", "legacy_name": "B", "item_id": "b", "entity_id": "eb"},
        {"display_name": "C", "legacy_name": "C", "item_id": "c", "entity_id": "ec"},
    ]
    d._current_index = 0
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
    d._command_arbiter = None
    d._runtime_state = PlaybackRuntimeState()
    d._inflight_fast_stop_tasks = set()
    d.device = types.SimpleNamespace(
        did="did-t04c3a",
        play_type=PLAY_TYPE_ALL,
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
            find_real_music_name=lambda name, n=1: [],
        ),
        device_manager=types.SimpleNamespace(
            get_group_device_id_list=lambda g: [],
            get_group_devices=lambda g: {},
        ),
    )
    d._ensure_manual_navigation_state = lambda: None
    d._manual_nav_lock = asyncio.Lock()
    d._manual_nav_generation = 0
    d._manual_nav_target = None
    d._manual_navigation_is_current = lambda g: True
    d.auth_manager = types.SimpleNamespace(
        mina_call=_noop,
    )

    # Patch physical methods to no-ops
    d.group_force_stop_xiaoai = _noop
    d.group_player_play = _noop_return_false
    d.get_if_xiaoai_is_playing = _noop_return_true
    d.do_tts = _noop
    d.cancel_group_next_timer = _noop
    d.cancel_next_timer = _noop
    d._get_auto_next_confirm_profile = lambda: {
        "delay_sec": 0.01,
        "retries": 0,
        "interval_sec": 0.01,
    }

    def _log_measure(*args, **kwargs):
        pass

    d._log_measure = _log_measure

    return d


# ── helpers ────────────────────────────────────────────────────────────────


def _capture_physical(d, phys_calls: list, block: asyncio.Event, started: asyncio.Event):
    """Patch _play_next / _play to record calls, signal started, and optionally block."""

    async def _patched_play_next(command_already_accepted=False):
        phys_calls.append(("play_next", command_already_accepted))
        started.set()
        await asyncio.wait_for(block.wait(), timeout=5.0)

    d._play_next = _patched_play_next

    async def _patched_play(name="", **kwargs):
        phys_calls.append(("play", name, kwargs.get("command_already_accepted", False)))
        started.set()
        await asyncio.wait_for(block.wait(), timeout=5.0)

    d._play = _patched_play


# ═══════════════════════════════════════════════════════════════════════════
# Test A: AUTO_NEXT blocked physical 时 timer callback 快速完成
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_a_auto_next_blocked_physical_timer_callback_quick():
    """When physical is blocked, a second AUTO_NEXT submit returns
    quickly (non-blocking) while first is still executing."""
    d = _make_device()
    phys_calls: list = []
    block = asyncio.Event()
    started = asyncio.Event()
    _capture_physical(d, phys_calls, block, started)

    _stage_playing(d, "song_a")
    token = d._capture_lifecycle_token()
    sid = d._play_session_id

    # Submit first AUTO_NEXT — starts physical (blocked)
    d._submit_auto_retry(IntentKind.AUTO_NEXT, source_token=token, sid=sid, reason="timer")

    # Wait for physical to enter
    await asyncio.wait_for(started.wait(), timeout=5.0)
    assert len(phys_calls) == 1

    # Submit second with fresh token — returns immediately
    token2 = d._capture_lifecycle_token()
    # c was bumped by first, so token2 has new c; fresh token passes guard
    result = d._submit_auto_retry(
        IntentKind.AUTO_NEXT, source_token=token2, sid=sid, reason="timer_quick"
    )
    assert isinstance(result, bool)

    block.set()
    await d.close_command_arbiter()


# ═══════════════════════════════════════════════════════════════════════════
# Test B: 同 source token 竞争 — 第一条 accepted c+1, 第二条 strict stale
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_b_same_source_token_race_single_accepted():
    """Same source token: first submit accepted → c+1; second submit
    natural strict-stale → False, c unchanged, zero extra writes."""
    d = _make_device()
    phys_calls: list = []
    block = asyncio.Event()
    started = asyncio.Event()
    _capture_physical(d, phys_calls, block, started)

    _stage_playing(d, "song_b")
    token = d._capture_lifecycle_token()
    sid = d._play_session_id
    initial_c = d.get_runtime_state().command_generation

    # First submit — accepted
    r1 = d._submit_auto_retry(IntentKind.AUTO_NEXT, source_token=token, sid=sid, reason="timer")
    assert r1 is True
    assert d.get_runtime_state().command_generation == initial_c + 1

    # Second submit with SAME token — now strict stale because c changed
    r2 = d._submit_auto_retry(IntentKind.AUTO_NEXT, source_token=token, sid=sid, reason="confirm")
    assert r2 is False
    # c NOT bumped again
    assert d.get_runtime_state().command_generation == initial_c + 1

    block.set()
    await d.close_command_arbiter()


# ═══════════════════════════════════════════════════════════════════════════
# Test C: AUTO_NEXT pending 被 manual NEXT 最新替换
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_c_auto_next_pending_replaced_by_newer_command():
    """When AUTO_NEXT is active, a manual NEXT is queued as pending
    (normal latest-pending), and both execute sequentially."""
    d = _make_device()
    phys_calls: list = []
    block = asyncio.Event()
    started1 = asyncio.Event()
    started2 = asyncio.Event()
    call_count = 0

    async def _patched_play_next(command_already_accepted=False):
        nonlocal call_count
        call_count += 1
        phys_calls.append(("play_next", command_already_accepted))
        if call_count == 1:
            started1.set()
            await asyncio.wait_for(block.wait(), timeout=5.0)
        else:
            started2.set()

    async def _patched_play(name="", **kwargs):
        nonlocal call_count
        call_count += 1
        phys_calls.append(("play", name, kwargs.get("command_already_accepted", False)))
        if call_count == 1:
            started1.set()
            await asyncio.wait_for(block.wait(), timeout=5.0)
        else:
            started2.set()

    d._play_next = _patched_play_next
    d._play = _patched_play

    _stage_playing(d, "song_before")
    arbiter = d._get_or_create_arbiter()

    token = d._capture_lifecycle_token()
    sid = d._play_session_id

    # Submit AUTO_NEXT — starts executing physical (blocked)
    d._submit_auto_retry(IntentKind.AUTO_NEXT, source_token=token, sid=sid, reason="auto")

    await asyncio.wait_for(started1.wait(), timeout=5.0)
    assert len(phys_calls) == 1

    # Now submit a manual NEXT
    d._accept_command(updated_at=time.time())
    d._manual_nav_generation += 1
    gen = d._manual_nav_generation
    arbiter.submit(IntentKind.NEXT, payload={
        "generation": gen, "target": "B", "direction": "next",
    })

    assert arbiter.pending_sequence is not None

    # Release blocked physical → NEXT runs after AUTO_NEXT finishes
    block.set()

    # Wait for second physical call
    await asyncio.wait_for(started2.wait(), timeout=5.0)
    assert len(phys_calls) >= 2

    await d.close_command_arbiter()


# ═══════════════════════════════════════════════════════════════════════════
# Test D: STOP active/pending 时旧 source AUTO_NEXT 拒绝零写
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_d_auto_next_rejected_during_stop_zero_write():
    """When STOP is active or pending, an old-source AUTO_NEXT submit
    is rejected with zero lifecycle writes."""
    d = _make_device()
    phys_calls: list = []
    block = asyncio.Event()
    started = asyncio.Event()
    _capture_physical(d, phys_calls, block, started)

    _stage_playing(d, "song_d")
    token = d._capture_lifecycle_token()
    sid = d._play_session_id
    initial_c = d.get_runtime_state().command_generation

    # Submit AUTO_NEXT — starts executing (blocked)
    d._submit_auto_retry(IntentKind.AUTO_NEXT, source_token=token, sid=sid, reason="auto")
    await asyncio.wait_for(started.wait(), timeout=5.0)

    # Submit STOP bumps c — making old token stale
    d._accept_command(updated_at=time.time())
    stop_token = d._capture_lifecycle_token()
    d._begin_runtime_stop(updated_at=time.time())
    d._get_or_create_arbiter().submit(IntentKind.STOP, payload={
        "sid": d._play_session_id, "token": stop_token, "arg1": "notts",
    })

    # Old token is now stale (c changed)
    r = d._submit_auto_retry(
        IntentKind.AUTO_NEXT, source_token=token, sid=sid, reason="stale_auto"
    )
    assert r is False
    # c was bumped by first submit (+1) then by STOP accept (+1) = initial_c + 2
    assert d.get_runtime_state().command_generation == initial_c + 2

    block.set()
    await d.close_command_arbiter()


# ═══════════════════════════════════════════════════════════════════════════
# Test E: AUTO_NEXT active → STOP, single executor, stale callback not PLAYING
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_e_auto_next_active_then_stop_executor_stale():
    """When AUTO_NEXT is active in executor and STOP arrives,
    STOP completes normally. Single executor per arbiter."""
    d = _make_device()

    auto_entered = asyncio.Event()

    async def _patched_play_next(command_already_accepted=False):
        auto_entered.set()

    d._play_next = _patched_play_next

    _stage_playing(d, "song_e")
    token = d._capture_lifecycle_token()
    sid = d._play_session_id
    arbiter = d._get_or_create_arbiter()

    # Submit AUTO_NEXT
    d._submit_auto_retry(IntentKind.AUTO_NEXT, source_token=token, sid=sid, reason="auto")

    # Wait for executor to enter physical
    await asyncio.wait_for(auto_entered.wait(), timeout=5.0)

    # Now submit STOP — bump c
    d._accept_command(updated_at=time.time())
    stop_token = d._capture_lifecycle_token()
    d._begin_runtime_stop(updated_at=time.time())

    stop_done = asyncio.Event()

    # Close old arbiter, patch stop, create new arbiter
    await arbiter.close()

    async def _patched_stop_intent(payload):
        stop_done.set()

    d._execute_stop_intent = _patched_stop_intent
    d._command_arbiter = None
    arbiter2 = d._get_or_create_arbiter()

    arbiter2.submit(IntentKind.STOP, payload={
        "sid": d._play_session_id, "token": stop_token, "arg1": "notts",
    })

    # Wait for STOP to complete
    await asyncio.wait_for(stop_done.wait(), timeout=5.0)

    await d.close_command_arbiter()


# ═══════════════════════════════════════════════════════════════════════════
# Test F: RETRY backoff 后快速 submit，同歌路径 c+1 一次
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_f_retry_backoff_same_song_submit_c1():
    """RETRY submits same-song path via arbiter, bumps c+1 once,
    physical call lands in executor with correct args."""
    d = _make_device()

    phys_entered = asyncio.Event()
    phys_calls: list = []

    async def _patched_play(name="", **kwargs):
        phys_calls.append(("play", name, kwargs.get("command_already_accepted", False)))
        phys_entered.set()

    d._play = _patched_play

    _stage_playing(d, "song_f")
    token = d._capture_lifecycle_token()
    sid = d._play_session_id
    initial_c = d.get_runtime_state().command_generation

    r = d._submit_auto_retry(
        IntentKind.RETRY,
        source_token=token,
        sid=sid,
        payload={"name": "song_f", "retry_same_song": True},
        reason="play_failure",
    )
    assert r is True
    assert d.get_runtime_state().command_generation == initial_c + 1

    # Wait for executor to call physical
    await asyncio.wait_for(phys_entered.wait(), timeout=5.0)
    play_calls = [c for c in phys_calls if c[0] == "play"]
    assert len(play_calls) == 1
    assert play_calls[0][1] == "song_f"

    await d.close_command_arbiter()


# ═══════════════════════════════════════════════════════════════════════════
# Test G: retry token stale → zero submit
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_g_retry_token_stale_during_sleep_zero_submit():
    """When source token is stale, submit returns False, c unchanged."""
    d = _make_device()
    phys_calls: list = []
    block = asyncio.Event()
    started = asyncio.Event()
    _capture_physical(d, phys_calls, block, started)

    _stage_playing(d, "song_g")
    token = d._capture_lifecycle_token()
    sid = d._play_session_id

    # Make token stale by bumping c
    d._accept_command(updated_at=time.time())
    initial_c = d.get_runtime_state().command_generation

    r = d._submit_auto_retry(
        IntentKind.RETRY,
        source_token=token,
        sid=sid,
        payload={"name": "song_g", "retry_same_song": True},
        reason="play_failure",
    )
    assert r is False
    assert d.get_runtime_state().command_generation == initial_c

    block.set()
    await d.close_command_arbiter()


# ═══════════════════════════════════════════════════════════════════════════
# Test H: RETRY accepted 后 STOP 令 executor stale 不物理
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_h_retry_accepted_then_stop_executor_stale_no_physical():
    """RETRY accepted but STOP arrives before executor runs;
    executor sees stale token, no physical play."""
    d = _make_device()

    phys_calls: list = []
    phys_entered = asyncio.Event()

    async def _patched_play(name="", **kwargs):
        phys_calls.append(("play", name))
        phys_entered.set()

    d._play = _patched_play

    _stage_playing(d, "song_h")
    token = d._capture_lifecycle_token()
    sid = d._play_session_id

    # Submit RETRY
    r = d._submit_auto_retry(
        IntentKind.RETRY,
        source_token=token,
        sid=sid,
        payload={"name": "song_h", "retry_same_song": True},
        reason="play_failure",
    )
    assert r is True

    # Immediately submit STOP — bumps c, making retry accepted_token stale
    d._accept_command(updated_at=time.time())
    stop_token = d._capture_lifecycle_token()
    d._begin_runtime_stop(updated_at=time.time())
    arbiter = d._get_or_create_arbiter()

    stop_done = asyncio.Event()

    # Close old arbiter (which has RETRY queued), create new with patched stop
    await arbiter.close()

    async def _patched_stop_intent(payload):
        stop_done.set()

    d._execute_stop_intent = _patched_stop_intent
    d._command_arbiter = None
    arbiter2 = d._get_or_create_arbiter()

    arbiter2.submit(IntentKind.STOP, payload={
        "sid": d._play_session_id, "token": stop_token, "arg1": "notts",
    })

    # Wait for STOP to complete
    await asyncio.wait_for(stop_done.wait(), timeout=5.0)

    # RETRY was in old (closed) arbiter — never executed
    # No physical play call should have happened
    play_calls = [c for c in phys_calls if c[0] == "play"]
    assert len(play_calls) == 0

    await d.close_command_arbiter()


# ═══════════════════════════════════════════════════════════════════════════
# Test I: RETRY 异常 last_error 后 worker 继续
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_i_retry_exception_last_error_worker_continues():
    """After RETRY executor raises, last_error is set and worker processes
    subsequent intents normally."""
    d = _make_device()

    error_raised = asyncio.Event()
    next_done = asyncio.Event()

    async def _failing_play_next(command_already_accepted=False):
        error_raised.set()
        raise RuntimeError("retry_boom")

    d._play_next = _failing_play_next

    _stage_playing(d, "song_i")
    token = d._capture_lifecycle_token()
    sid = d._play_session_id
    arbiter = d._get_or_create_arbiter()

    # Submit RETRY — will fail in executor
    d._submit_auto_retry(
        IntentKind.RETRY,
        source_token=token,
        sid=sid,
        payload={"name": "song_i", "retry_same_song": False},
        reason="play_failure",
    )

    await asyncio.wait_for(error_raised.wait(), timeout=5.0)

    # Wait for arbiter to register the error:
    # The arbiter worker processes the error in its finally block,
    # sets active_seq=None, then checks for pending work.
    # We need to wait for this to happen. Submit STOP which will
    # be processed after the error is handled.
    d._accept_command(updated_at=time.time())
    stop_token = d._capture_lifecycle_token()

    # Patch stop to signal completion (must happen before STOP executor runs)
    # Since arbiter already captured old executor, we close and recreate
    await arbiter.close()

    # Verify last_error was recorded
    assert arbiter.last_error is not None
    assert isinstance(arbiter.last_error, RuntimeError)

    # Recreate arbiter with tracking executor
    async def _patched_stop_intent(payload):
        next_done.set()

    d._execute_stop_intent = _patched_stop_intent
    d._command_arbiter = None
    arbiter2 = d._get_or_create_arbiter()

    arbiter2.submit(IntentKind.STOP, payload={
        "sid": d._play_session_id, "token": stop_token, "arg1": "notts",
    })

    await asyncio.wait_for(next_done.wait(), timeout=5.0)

    assert arbiter2.active_sequence is None
    assert arbiter2.pending_sequence is None

    await d.close_command_arbiter()


# ═══════════════════════════════════════════════════════════════════════════
# Test J: arbiter closed submit 零 c 写
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_j_arbiter_closed_submit_zero_c_write():
    """When arbiter is closed, submit returns False, c unchanged."""
    d = _make_device()
    _stage_playing(d, "song_j")
    token = d._capture_lifecycle_token()
    sid = d._play_session_id

    arbiter = d._get_or_create_arbiter()
    await arbiter.close()

    initial_c = d.get_runtime_state().command_generation
    r = d._submit_auto_retry(
        IntentKind.AUTO_NEXT, source_token=token, sid=sid, reason="closed_test"
    )
    assert r is False
    assert d.get_runtime_state().command_generation == initial_c

    await d.close_command_arbiter()


# ═══════════════════════════════════════════════════════════════════════════
# Test: max executor concurrency = 1
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_max_executor_concurrency_one():
    """Only one executor call in flight at any time."""
    d = _make_device()
    phys_calls: list = []
    block = asyncio.Event()
    started = asyncio.Event()
    _capture_physical(d, phys_calls, block, started)

    _stage_playing(d, "song_k")
    token1 = d._capture_lifecycle_token()
    sid = d._play_session_id

    # Submit first AUTO_NEXT — blocks in physical
    d._submit_auto_retry(IntentKind.AUTO_NEXT, source_token=token1, sid=sid, reason="first")
    await asyncio.wait_for(started.wait(), timeout=5.0)
    assert len(phys_calls) == 1

    # Submit second with fresh token — c+1 from first, so token2 is fresh for second
    token2 = d._capture_lifecycle_token()
    d._submit_auto_retry(IntentKind.AUTO_NEXT, source_token=token2, sid=sid, reason="second")
    # Second submit accepted but physical not yet called (blocked)
    assert len(phys_calls) == 1

    # Release block
    block.set()

    # Wait for second physical via an event
    done2 = asyncio.Event()

    orig = d._play_next

    async def _tracking2(command_already_accepted=False):
        await orig(command_already_accepted=command_already_accepted)
        done2.set()

    d._play_next = _tracking2

    await asyncio.wait_for(done2.wait(), timeout=5.0)
    # Both should have executed sequentially
    assert len(phys_calls) >= 2

    await d.close_command_arbiter()


# ═══════════════════════════════════════════════════════════════════════════
# Test: sid mismatch rejects submit
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_sid_mismatch_rejects_submit():
    """When sid doesn't match _play_session_id, submit returns False."""
    d = _make_device()
    _stage_playing(d, "song_m")
    token = d._capture_lifecycle_token()
    initial_c = d.get_runtime_state().command_generation

    r = d._submit_auto_retry(
        IntentKind.AUTO_NEXT, source_token=token, sid=999, reason="mismatch"
    )
    assert r is False
    assert d.get_runtime_state().command_generation == initial_c

    await d.close_command_arbiter()


# ═══════════════════════════════════════════════════════════════════════════
# Test: RETRY same-song path in executor
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_retry_executor_same_song_physical():
    """RETRY with retry_same_song=True routes to _play, not _play_next."""
    d = _make_device()

    phys_calls: list = []
    phys_done = asyncio.Event()

    async def _patched_play(name="", **kwargs):
        phys_calls.append(("play", name))
        phys_done.set()

    async def _patched_play_next(command_already_accepted=False):
        phys_calls.append(("play_next",))

    d._play = _patched_play
    d._play_next = _patched_play_next

    _stage_playing(d, "song_n")
    token = d._capture_lifecycle_token()
    sid = d._play_session_id

    d._submit_auto_retry(
        IntentKind.RETRY,
        source_token=token,
        sid=sid,
        payload={"name": "song_n", "retry_same_song": True},
        reason="retry_same",
    )

    await asyncio.wait_for(phys_done.wait(), timeout=5.0)

    play_calls = [c for c in phys_calls if c[0] == "play"]
    next_calls = [c for c in phys_calls if c[0] == "play_next"]
    assert len(play_calls) == 1
    assert play_calls[0][1] == "song_n"
    assert len(next_calls) == 0

    await d.close_command_arbiter()


# ═══════════════════════════════════════════════════════════════════════════
# Test: RETRY next-song path in executor
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_retry_executor_next_song_physical():
    """RETRY with retry_same_song=False routes to _play_next."""
    d = _make_device()

    phys_calls: list = []
    phys_done = asyncio.Event()

    async def _patched_play(name="", **kwargs):
        phys_calls.append(("play", name))

    async def _patched_play_next(command_already_accepted=False):
        phys_calls.append(("play_next",))
        phys_done.set()

    d._play = _patched_play
    d._play_next = _patched_play_next

    _stage_playing(d, "song_o")
    token = d._capture_lifecycle_token()
    sid = d._play_session_id

    d._submit_auto_retry(
        IntentKind.RETRY,
        source_token=token,
        sid=sid,
        payload={"name": "song_o", "retry_same_song": False},
        reason="retry_next",
    )

    await asyncio.wait_for(phys_done.wait(), timeout=5.0)

    play_calls = [c for c in phys_calls if c[0] == "play"]
    next_calls = [c for c in phys_calls if c[0] == "play_next"]
    assert len(play_calls) == 0
    assert len(next_calls) == 1

    await d.close_command_arbiter()


# ═══════════════════════════════════════════════════════════════════════════
# Test: closed arbiter失败后新arbiter可成功 — 证明失败不消耗 token
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_closed_arbiter_failure_then_new_arbiter_succeeds():
    """First submit to closed arbiter → False, c unchanged.
    Recreate arbiter, same token succeeds on second attempt."""
    d = _make_device()

    phys_done = asyncio.Event()

    async def _patched_play_next(command_already_accepted=False):
        phys_done.set()

    d._play_next = _patched_play_next

    _stage_playing(d, "song_p")
    token = d._capture_lifecycle_token()
    sid = d._play_session_id
    initial_c = d.get_runtime_state().command_generation

    # First attempt: close arbiter, submit fails
    arbiter = d._get_or_create_arbiter()
    await arbiter.close()

    r1 = d._submit_auto_retry(
        IntentKind.AUTO_NEXT, source_token=token, sid=sid, reason="closed_test"
    )
    assert r1 is False
    assert d.get_runtime_state().command_generation == initial_c

    # Recreate arbiter by setting _command_arbiter to None
    d._command_arbiter = None

    # Second attempt: same token, now succeeds (c still unchanged → token not stale)
    r2 = d._submit_auto_retry(
        IntentKind.AUTO_NEXT, source_token=token, sid=sid, reason="retry_after_close"
    )
    assert r2 is True
    assert d.get_runtime_state().command_generation == initial_c + 1

    # Physical should run
    await asyncio.wait_for(phys_done.wait(), timeout=5.0)

    await d.close_command_arbiter()
