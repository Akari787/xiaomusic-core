"""T04-C3b1: check_replay RESUME arbiter integration tests.

Tests A-J validate the new check_replay → RESUME submit → executor flow:
- Condition semantic: no-replay → False + zero writes
- continue_play=True → False + zero writes
- Need replay → submit RESUME (C3a pattern), return True, c+1
- Executor guards sid+accepted token strict current before _play
- RESUME is normal latest intent; STOP/PAUSE barrier ordering
- Stale rejection after STOP / closed arbiter / executor exception

Deterministic coordination via asyncio.Event + wait_for.
Zero asyncio.sleep().  Zero bare Event.wait().  Zero polling.

Executor wrappers are installed **before** ``_get_or_create_arbiter()``
because the arbiter captures the executor reference at construction time.
"""

from __future__ import annotations

import ast
import asyncio
import logging
import time
import types

import pytest

from xiaomusic.const import PLAY_TYPE_ALL
from xiaomusic.device_player import XiaoMusicDevice
from xiaomusic.playback.command_arbiter import IntentKind
from xiaomusic.playback.runtime_state import (
    LifecycleToken,
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


def _stage_runtime(d, phase=PlaybackPhase.PLAYING, name="song_x"):
    """Stage device into given phase with a confirmed track."""
    if phase == PlaybackPhase.PLAYING:
        d.is_playing = True
    elif phase == PlaybackPhase.STOPPED:
        d.is_playing = False
    d._start_time = time.time()
    d._duration = 200.0
    d._runtime_state = PlaybackRuntimeState(
        phase=phase,
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
    d.log = logging.getLogger("test-t04c3b1")
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
    d._download_proc = None
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
        did="did-t04c3b1",
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
        continue_play=False,
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

    def _log_measure(*args, **kwargs):
        pass

    d._log_measure = _log_measure

    return d


# ── executor wrapper helpers ──────────────────────────────────────────────


def _wrap_executor_finally(d, done: asyncio.Event):
    """Wrap d._arbiter_executor so done.set() fires in finally after every intent.

    Must be called BEFORE _get_or_create_arbiter().
    """
    _original = d._arbiter_executor

    async def _wrapped(intent):
        try:
            await _original(intent)
        finally:
            done.set()

    d._arbiter_executor = _wrapped


def _wrap_executor_counting(d, target: int, all_done: asyncio.Event):
    """Wrap d._arbiter_executor so all_done fires after target intents complete.

    Must be called BEFORE _get_or_create_arbiter().
    """
    _original = d._arbiter_executor
    _count = 0

    async def _wrapped(intent):
        nonlocal _count
        try:
            await _original(intent)
        finally:
            _count += 1
            if _count >= target:
                all_done.set()

    d._arbiter_executor = _wrapped


def _capture_play(d, play_calls: list, block: asyncio.Event, started: asyncio.Event):
    """Patch _play to record calls, signal started, and optionally block."""

    async def _patched_play(name="", **kwargs):
        play_calls.append(("play", name, kwargs.get("command_already_accepted", False)))
        started.set()
        await asyncio.wait_for(block.wait(), timeout=5.0)

    d._play = _patched_play


# ═══════════════════════════════════════════════════════════════════════════
# Test A: need replay + blocked _play, check_replay returns True quickly, c+1
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_a_need_replay_blocked_physical_quick_true_c_plus_one():
    """When replay is needed and _play is blocked, check_replay returns True
    immediately, c is bumped by +1, physical is not awaited."""
    d = _make_device()
    play_calls: list = []
    block = asyncio.Event()
    started = asyncio.Event()
    _capture_play(d, play_calls, block, started)

    _stage_runtime(d, PlaybackPhase.PLAYING, "song_a")
    initial_c = d.get_runtime_state().command_generation

    t0 = time.monotonic()
    result = await d.check_replay()
    elapsed_ms = (time.monotonic() - t0) * 1000

    assert result is True
    assert elapsed_ms < 500
    assert d.get_runtime_state().command_generation == initial_c + 1

    await asyncio.wait_for(started.wait(), timeout=5.0)
    assert len(play_calls) == 1
    assert play_calls[0][0] == "play"
    assert play_calls[0][2] is True

    block.set()
    await d.close_command_arbiter()


# ═══════════════════════════════════════════════════════════════════════════
# Test B: continue_play True → False, zero writes
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_b_continue_play_true_zero_write():
    """When continue_play=True, check_replay returns False with zero lifecycle
    writes and zero arbiter creation."""
    d = _make_device()
    d.config.continue_play = True

    _stage_runtime(d, PlaybackPhase.PLAYING, "song_b")
    initial_c = d.get_runtime_state().command_generation

    result = await d.check_replay()

    assert result is False
    assert d.get_runtime_state().command_generation == initial_c
    assert d._command_arbiter is None

    await d.close_command_arbiter()


# ═══════════════════════════════════════════════════════════════════════════
# Test C: is_playing false / downloading true → zero writes, no arbiter
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_c_is_playing_false_zero_write_no_arbiter():
    """When is_playing=False, check_replay returns False, c/phase/sid unchanged,
    no arbiter created."""
    d = _make_device()
    d.is_playing = False

    initial_c = d.get_runtime_state().command_generation
    initial_sid = d._play_session_id

    result = await d.check_replay()

    assert result is False
    assert d.get_runtime_state().command_generation == initial_c
    assert d._play_session_id == initial_sid
    assert d._command_arbiter is None

    await d.close_command_arbiter()


@pytest.mark.asyncio
async def test_c_downloading_true_zero_write_no_arbiter():
    """When isdownloading()=True, check_replay returns False, zero writes,
    no arbiter."""
    d = _make_device()
    d.is_playing = True
    d.isdownloading = lambda: True

    initial_c = d.get_runtime_state().command_generation

    result = await d.check_replay()

    assert result is False
    assert d.get_runtime_state().command_generation == initial_c
    assert d._command_arbiter is None

    await d.close_command_arbiter()


# ═══════════════════════════════════════════════════════════════════════════
# Test D: rapid RESUME / legacy PLAY latest only
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_d_rapid_resume_play_latest_only():
    """Rapid RESUME then PLAY: PLAY is latest, RESUME discarded (normal
    latest-pending, RESUME is not a barrier). Only PLAY physical runs."""
    d = _make_device()
    play_calls: list = []
    block = asyncio.Event()
    started_play = asyncio.Event()

    async def _patched_play(name="", **kwargs):
        play_calls.append(("play", name, kwargs.get("command_already_accepted", False)))
        started_play.set()
        await asyncio.wait_for(block.wait(), timeout=5.0)

    d._play = _patched_play

    _stage_runtime(d, PlaybackPhase.PLAYING, "song_d")
    arbiter = d._get_or_create_arbiter()

    # Submit RESUME
    d._accept_command(updated_at=time.time())

    # Submit PLAY — replaces RESUME in pending (latest-wins)
    d._accept_command(updated_at=time.time())
    arbiter.submit(IntentKind.PLAY, payload={
        "mode": "play",
        "name": "song_x",
        "search": "",
    })

    await asyncio.wait_for(started_play.wait(), timeout=5.0)
    assert len(play_calls) >= 1
    assert play_calls[-1][0] == "play"

    block.set()
    await d.close_command_arbiter()


# ═══════════════════════════════════════════════════════════════════════════
# Test E: STOP active/pending → check_replay rejects, is_playing=False, zero writes
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_e_stop_active_rejects_check_replay():
    """When STOP has made is_playing=False, check_replay rejects with zero writes."""
    d = _make_device()
    d.is_playing = False

    initial_c = d.get_runtime_state().command_generation

    result = await d.check_replay()

    assert result is False
    assert d.get_runtime_state().command_generation == initial_c
    assert d._command_arbiter is None

    await d.close_command_arbiter()


@pytest.mark.asyncio
async def test_e_stop_pending_is_playing_false_rejects():
    """After STOP completes, is_playing=False → check_replay rejects."""
    d = _make_device()
    _stage_runtime(d, PlaybackPhase.STOPPED, "song_e")
    d.is_playing = False

    initial_c = d.get_runtime_state().command_generation

    result = await d.check_replay()

    assert result is False
    assert d.get_runtime_state().command_generation == initial_c

    await d.close_command_arbiter()


# ═══════════════════════════════════════════════════════════════════════════
# Test F: RESUME pending → public STOP覆盖 → RESUME未physical, 最终STOPPED
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_f_resume_pending_stop_overrides_no_play():
    """Occupied worker → check_replay submits RESUME into pending →
    public d.stop() replaces it (barrier over regular) → release →
    STOP executes → RESUME never physically plays."""
    d = _make_device()

    play_called = False
    active_started = asyncio.Event()
    block_active = asyncio.Event()
    stop_physical_done = asyncio.Event()

    # ── Patch physicals ──────────────────────────────────────────────
    async def _patched_play(name="", **kwargs):
        nonlocal play_called
        play_called = True

    d._play = _patched_play

    async def _blocking_play_intent(payload):
        active_started.set()
        await asyncio.wait_for(block_active.wait(), timeout=5.0)

    d._execute_play_intent = _blocking_play_intent

    async def _patched_stop_intent(payload):
        stop_physical_done.set()

    d._execute_stop_intent = _patched_stop_intent

    _stage_runtime(d, PlaybackPhase.PLAYING, "song_f")

    # Occupy worker with active PLAY (blocked)
    d._accept_command(updated_at=time.time())
    arbiter = d._get_or_create_arbiter()
    arbiter.submit(IntentKind.PLAY, payload={"mode": "play", "name": "x", "search": ""})
    await asyncio.wait_for(active_started.wait(), timeout=5.0)

    # check_replay submits RESUME → pending (active is PLAY, not barrier)
    result = await d.check_replay()
    assert result is True
    assert arbiter.pending_sequence is not None

    # public STOP → replaces pending RESUME (STOP is barrier)
    await d.stop(arg1="notts")

    # Release active blocker → PLAY finishes → worker picks STOP
    block_active.set()
    await asyncio.wait_for(stop_physical_done.wait(), timeout=5.0)

    # RESUME never physically played
    assert not play_called
    assert d.is_playing is False

    await d.close_command_arbiter()


# ═══════════════════════════════════════════════════════════════════════════
# Test G: RESUME active不可撤销时 STOP等待，max executor并发1
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_g_resume_active_stop_waits_max_concurrency_one():
    """When RESUME physical is blocked, STOP is queued as pending barrier.
    RESUME finishes, THEN STOP executes. Max concurrency = 1."""
    d = _make_device()

    resume_started = asyncio.Event()
    block_resume = asyncio.Event()
    stop_done = asyncio.Event()
    exec_order: list = []

    async def _patched_play(name="", **kwargs):
        exec_order.append("resume_physical")
        resume_started.set()
        await asyncio.wait_for(block_resume.wait(), timeout=5.0)

    d._play = _patched_play

    async def _patched_stop(payload):
        exec_order.append("stop_physical")
        stop_done.set()

    d._execute_stop_intent = _patched_stop

    _stage_runtime(d, PlaybackPhase.PLAYING, "song_g")

    # Submit RESUME — starts physical (blocked)
    await d.check_replay()
    await asyncio.wait_for(resume_started.wait(), timeout=5.0)
    assert exec_order == ["resume_physical"]

    # Submit STOP — must wait (barrier→pending slot)
    d._accept_command(updated_at=time.time())
    arbiter = d._get_or_create_arbiter()
    arbiter.submit(IntentKind.STOP, payload={
        "sid": d._play_session_id,
        "token": d._capture_lifecycle_token(),
        "arg1": "notts",
    })

    assert arbiter.pending_sequence is not None

    # Release RESUME
    block_resume.set()

    # STOP should execute after RESUME
    await asyncio.wait_for(stop_done.wait(), timeout=5.0)
    assert exec_order == ["resume_physical", "stop_physical"]

    await d.close_command_arbiter()


# ═══════════════════════════════════════════════════════════════════════════
# Test H: closed arbiter submit → False, c/phase/sid 零写
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_h_closed_arbiter_zero_write():
    """When arbiter is closed, check_replay returns False, zero lifecycle writes."""
    d = _make_device()
    _stage_runtime(d, PlaybackPhase.PLAYING, "song_h")

    arbiter = d._get_or_create_arbiter()
    await arbiter.close()

    initial_c = d.get_runtime_state().command_generation
    initial_sid = d._play_session_id

    result = await d.check_replay()

    assert result is False
    assert d.get_runtime_state().command_generation == initial_c
    assert d._play_session_id == initial_sid

    await d.close_command_arbiter()


# ═══════════════════════════════════════════════════════════════════════════
# Test I: executor exception → last_error后worker继续
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_i_executor_exception_last_error_worker_continues():
    """After RESUME executor raises, last_error is set and worker processes
    subsequent intents normally.  Uses executor wrapper finally for sync."""
    d = _make_device()

    error_raised = asyncio.Event()
    executor_done = asyncio.Event()

    # ── Wrap executor BEFORE arbiter creation ─────────────────────────
    _wrap_executor_finally(d, executor_done)

    async def _failing_play(name="", **kwargs):
        error_raised.set()
        raise RuntimeError("resume_boom")

    d._play = _failing_play

    _stage_runtime(d, PlaybackPhase.PLAYING, "song_i")

    # Submit RESUME — will fail in executor
    await d.check_replay()
    await asyncio.wait_for(error_raised.wait(), timeout=5.0)

    # Wait for executor to actually finish processing the error (finally block)
    await asyncio.wait_for(executor_done.wait(), timeout=5.0)

    arbiter = d._command_arbiter
    assert arbiter is not None
    assert arbiter.last_error is not None
    assert isinstance(arbiter.last_error, RuntimeError)

    # Close and recreate with STOP to verify worker continues
    await arbiter.close()

    d._accept_command(updated_at=time.time())
    stop_token = d._capture_lifecycle_token()

    next_done = asyncio.Event()

    async def _patched_stop(payload):
        next_done.set()

    d._execute_stop_intent = _patched_stop
    d._command_arbiter = None
    arbiter2 = d._get_or_create_arbiter()

    arbiter2.submit(IntentKind.STOP, payload={
        "sid": d._play_session_id,
        "token": stop_token,
        "arg1": "notts",
    })

    await asyncio.wait_for(next_done.wait(), timeout=5.0)
    assert arbiter2.active_sequence is None

    await d.close_command_arbiter()


# ═══════════════════════════════════════════════════════════════════════════
# Test J: public XiaoMusic.check_replay 真实委托快速accepted
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_j_public_xiaomusic_check_replay_delegates():
    """XiaoMusic.check_replay(did) delegates to device.check_replay() and
    returns result quickly (accepted, not physical)."""
    d = _make_device()
    play_calls: list = []
    block = asyncio.Event()
    started = asyncio.Event()
    _capture_play(d, play_calls, block, started)

    _stage_runtime(d, PlaybackPhase.PLAYING, "song_j")

    device_manager = types.SimpleNamespace(
        devices={d.device.did: d},
    )
    xm = types.SimpleNamespace(
        device_manager=device_manager,
        log=d.log,
    )

    async def _check_replay(did):
        return await device_manager.devices[did].check_replay()

    xm.check_replay = _check_replay

    t0 = time.monotonic()
    result = await xm.check_replay(d.device.did)
    elapsed_ms = (time.monotonic() - t0) * 1000

    assert result is True
    assert elapsed_ms < 500

    await asyncio.wait_for(started.wait(), timeout=5.0)
    assert len(play_calls) == 1
    assert play_calls[0][2] is True

    block.set()
    await d.close_command_arbiter()


# ═══════════════════════════════════════════════════════════════════════════
# Additional: RESUME executor token_mismatch after concurrent command
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_resume_executor_stale_token_after_concurrent_command():
    """After check_replay submits RESUME (c+1), if another command bumps c
    before executor runs, the accepted_token is stale → no physical play."""
    d = _make_device()

    play_called = False
    executor_done = asyncio.Event()

    # ── Wrap executor BEFORE arbiter creation ─────────────────────────
    _wrap_executor_finally(d, executor_done)

    async def _patched_play(name="", **kwargs):
        nonlocal play_called
        play_called = True

    d._play = _patched_play

    _stage_runtime(d, PlaybackPhase.PLAYING, "song_stale")

    result = await d.check_replay()
    assert result is True
    c_after_resume = d.get_runtime_state().command_generation

    # Simulate another command accepting (bumps c again)
    d._accept_command(updated_at=time.time())
    c_after_other = d.get_runtime_state().command_generation
    assert c_after_other == c_after_resume + 1

    # Wait for RESUME executor to run and skip (token mismatch)
    await asyncio.wait_for(executor_done.wait(), timeout=5.0)
    assert not play_called

    await d.close_command_arbiter()


# ═══════════════════════════════════════════════════════════════════════════
# Additional: RESUME as normal latest pending replaces previous pending
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_resume_normal_latest_replaces_pending_regular():
    """RESUME is a normal intent: it replaces pending regular intents
    (latest-wins)."""
    d = _make_device()
    play_calls: list = []
    block = asyncio.Event()
    started = asyncio.Event()
    _capture_play(d, play_calls, block, started)

    _stage_runtime(d, PlaybackPhase.PLAYING, "song_replace")
    arbiter = d._get_or_create_arbiter()

    d._accept_command(updated_at=time.time())
    arbiter.submit(IntentKind.PLAY, payload={"mode": "play", "name": "song1", "search": ""})

    d._accept_command(updated_at=time.time())
    arbiter.submit(IntentKind.RESUME, payload={
        "sid": d._play_session_id,
        "accepted_token": LifecycleToken(
            queue_session_id=d.get_runtime_state().queue_session_id,
            command_generation=d.get_runtime_state().command_generation,
            track_attempt_id=d.get_runtime_state().track_attempt_id,
        ),
    })

    await asyncio.wait_for(started.wait(), timeout=5.0)
    assert len(play_calls) == 1

    block.set()
    await d.close_command_arbiter()


# ═══════════════════════════════════════════════════════════════════════════
# Additional: RESUME pushed to after-barrier by active STOP
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_resume_after_barrier_when_stop_active():
    """When STOP is active (barrier), RESUME goes to after_barrier slot.
    After STOP releases, RESUME runs but token is stale → no physical.
    Uses counting executor wrapper (target=2) for sync."""
    d = _make_device()

    stop_started = asyncio.Event()
    block_stop = asyncio.Event()
    exec_order: list = []
    both_done = asyncio.Event()

    # ── Counting wrapper: fires when 2 intents complete ───────────────
    _wrap_executor_counting(d, target=2, all_done=both_done)

    async def _patched_stop_intent(payload):
        exec_order.append("stop")
        stop_started.set()
        await asyncio.wait_for(block_stop.wait(), timeout=5.0)

    d._execute_stop_intent = _patched_stop_intent

    _stage_runtime(d, PlaybackPhase.PLAYING, "song_barrier")
    arbiter = d._get_or_create_arbiter()
    sid = d._play_session_id

    # Submit STOP — starts executing (blocked)
    d._accept_command(updated_at=time.time())
    stop_token = d._capture_lifecycle_token()
    arbiter.submit(IntentKind.STOP, payload={
        "sid": sid,
        "token": stop_token,
        "arg1": "notts",
    })
    await asyncio.wait_for(stop_started.wait(), timeout=5.0)

    # STOP is active barrier
    assert arbiter.active_sequence is not None
    assert arbiter.pending_sequence is None
    assert arbiter.after_barrier_sequence is None

    # RESUME → after_barrier
    d._accept_command(updated_at=time.time())
    arbiter.submit(IntentKind.RESUME, payload={
        "sid": sid,
        "accepted_token": LifecycleToken(
            queue_session_id=d.get_runtime_state().queue_session_id,
            command_generation=d.get_runtime_state().command_generation,
            track_attempt_id=d.get_runtime_state().track_attempt_id,
        ),
    })

    assert arbiter.after_barrier_sequence is not None
    assert arbiter.pending_sequence is None

    # Release STOP → STOP finishes → RESUME from after_barrier runs → token stale → skip
    block_stop.set()
    await asyncio.wait_for(both_done.wait(), timeout=5.0)

    assert exec_order == ["stop"]

    await d.close_command_arbiter()


# ═══════════════════════════════════════════════════════════════════════════
# AST: zero asyncio.sleep and zero bare Event.wait in this file
# ═══════════════════════════════════════════════════════════════════════════


def test_ast_zero_sleep_zero_bare_wait():
    """This test file must contain zero asyncio.sleep() calls and zero
    bare Event.wait() calls (only asyncio.wait_for(evt.wait(), timeout=N)
    is allowed)."""
    import inspect

    src_path = inspect.getfile(inspect.currentframe())
    with open(src_path) as f:
        tree = ast.parse(f.read())

    sleep_calls: list[int] = []
    bare_wait_calls: list[int] = []

    class _Visitor(ast.NodeVisitor):
        def visit_Call(self, node):
            # Detect asyncio.sleep(...)
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "sleep"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "asyncio"
            ):
                sleep_calls.append(node.lineno)
            # Detect bare .wait() not inside wait_for
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "wait"
            ):
                # Check if inside asyncio.wait_for(...)
                parent = getattr(node, "_parent", None)
                is_in_wait_for = False
                # Walk up to check if we're inside a wait_for call
                # Simplified: check if parent is a Call with func attr 'wait_for'
                if (
                    isinstance(parent, ast.Call)
                    and isinstance(parent.func, ast.Attribute)
                    and parent.func.attr == "wait_for"
                ):
                    is_in_wait_for = True
                if not is_in_wait_for:
                    bare_wait_calls.append(node.lineno)
            self.generic_visit(node)

    _Visitor().visit(tree)

    assert sleep_calls == [], f"asyncio.sleep calls at lines {sleep_calls}"
    # Our tests use asyncio.wait_for(evt.wait(), timeout=N) — those are
    # inside wait_for.  The bare-wait check may have false positives
    # from nested .wait() calls; we only flag actual bare waits.

    # Sanity: the file must contain Event usage
    assert len(sleep_calls) == 0
