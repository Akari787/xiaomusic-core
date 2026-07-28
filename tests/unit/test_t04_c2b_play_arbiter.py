"""T04-C2b: public play/playlocal arbiter integration sequential tests.

Tests A-J validate the new async accept model: public play()/playlocal()
return True immediately, physical work is deferred to the arbiter executor,
barrier semantics are respected, and command_generation increments exactly
once per accepted PLAY.

Deterministic coordination via asyncio.Event + wait_for.
Zero asyncio.sleep(0).  Zero bare Event.wait().
"""

from __future__ import annotations

import asyncio
import logging
import time
import types

import pytest

from xiaomusic.const import PLAY_TYPE_ALL
from xiaomusic.device_player import XiaoMusicDevice
from xiaomusic.events import PLAYER_STATE_CHANGED
from xiaomusic.playback.runtime_state import (
    PlaybackPhase,
    PlaybackRuntimeState,
    TrackReference,
)

# ── helpers ────────────────────────────────────────────────────────────

async def _noop(*args, **kwargs):
    pass


async def _noop_return_false(*args, **kwargs):
    return False


async def _noop_return_true(*args, **kwargs):
    return True


async def _noop_return_zero():
    return 0.0


async def _noop_list(*args, **kwargs):
    return []


async def _noop_get_url(name):
    return f"file:///tmp/{name}.mp3", f"file:///tmp/{name}.mp3"


def _real_invalidate(d, reason):
    d._ensure_manual_navigation_state()
    d._manual_nav_generation += 1
    d._manual_nav_target = None


def _bump_sid(d):
    d._play_session_id += 1
    return d._play_session_id


# ── device factories ──────────────────────────────────────────────────


def _make_device() -> XiaoMusicDevice:
    """Create a test device with all required attributes."""
    d = XiaoMusicDevice.__new__(XiaoMusicDevice)
    d.log = logging.getLogger("test-t04c2b")
    d._play_list_items = [
        {"display_name": "A", "legacy_name": "A", "item_id": "a", "entity_id": "ea"},
        {"display_name": "B", "legacy_name": "B", "item_id": "b", "entity_id": "eb"},
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
    d._runtime_state = PlaybackRuntimeState()
    d.device = types.SimpleNamespace(
        did="did-t04c2b", play_type=PLAY_TYPE_ALL, hardware="OH2P",
        cur_playlist="全部", cur_music="", current_display_name="",
        current_entity_id="", current_playlist_item_id="",
        playlist2music={},
    )
    d.config = types.SimpleNamespace(
        delay_sec=0, verbose=False, ffmpeg_location="",
        jellyfin_proxy_mode="off", stop_tts_msg="",
        enable_force_stop=True, auto_next_stop_wait_mode="sync",
        auto_next_stop_grace_ms=0,
    )
    d.event_bus = None
    d.group_name = "test"
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(
            all_music={"A": "/tmp/A.mp3", "B": "/tmp/B.mp3"},
            is_music_exist=lambda n: True,
            get_music_url=_noop_get_url,
            get_music_duration=lambda n: _noop_return_zero(),
            find_real_music_name=lambda name, **kw: [name],
            resolve_playlist_item_identity=lambda p, item_name: "",
            resolve_entity_id_by_name=lambda n: "",
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
    d._command_arbiter = None
    d.do_tts = _noop
    d.group_force_stop_xiaoai = _noop_list
    d.cancel_group_next_timer = _noop
    d._invalidate_manual_navigation = lambda reason: None
    d.auto_add_song = lambda cur, sec: _noop()
    d._refresh_runtime_volume = lambda context="": _noop_return_false
    d._start_duration_probe = lambda name, sid, **kw: None
    d.set_next_music_timeout = lambda sec, token=None: _noop()
    d._schedule_playing_status_probe = lambda sid, name: None
    d._find_playlist_index = lambda *a, **kw: -1
    d.get_if_xiaoai_is_playing = _noop_return_false
    d._play = _noop_return_false
    d._play_next = _noop
    d.get_cur_music = lambda: "A"
    d.find_cur_playlist = lambda name: "全部"
    d.update_playlist = lambda: None
    d.check_play_next = lambda: False
    d._check_and_download_music = lambda name, sk, ad: _noop_return_true
    d._playmusic = _noop_return_false
    d._set_runtime_track_reference = lambda **kw: None
    d._track_background_task = lambda task, label: None
    d._bump_play_session = lambda reason="": _bump_sid(d)
    d._execute_group_stop = _noop_list
    d._mark_play_started = _noop_return_false
    d._schedule_playback_confirmation = lambda **kw: None
    d._confirm_playback_started = lambda name, sid: _noop_return_false
    d._try_proxy_fallback = _noop_return_false
    d._handle_play_failure = _noop
    d._log_measure = lambda msg: None
    return d


def _make_playing_device() -> XiaoMusicDevice:
    """Create device in PLAYING phase via real wrapper chain."""
    d = _make_device()
    d._start_queue_session(updated_at=time.time())
    desired = TrackReference(
        entity_id="e1", display_name="test-song", source="test",
    )
    d._begin_runtime_play_request(
        desired_track=desired, updated_at=time.time(),
    )
    d._begin_runtime_play_dispatch(updated_at=time.time())
    d._begin_runtime_confirmation(updated_at=time.time())
    d._confirm_runtime_playing(expected_end_at=180.0, updated_at=time.time())
    return d


# ══════════════════════════════════════════════════════════════════════
# Test A: IDLE play fast True, blocked _play incomplete; release → call
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_A_idle_play_fast_true_blocked_play():
    """IDLE play() → True immediately; _play blocked; release → executes once."""
    d = _make_device()

    play_entered = asyncio.Event()
    play_release = asyncio.Event()
    play_done = asyncio.Event()
    play_calls = 0

    async def _blocked_play(name="", search_key="", **kw):
        nonlocal play_calls
        play_calls += 1
        play_entered.set()
        await asyncio.wait_for(play_release.wait(), timeout=5.0)
        play_done.set()
        return True

    d._play = _blocked_play

    c_before = d.get_runtime_state().command_generation

    try:
        result = await d.play(name="A", search_key="")
        assert result is True
        assert d.get_runtime_state().command_generation == c_before + 1

        await asyncio.wait_for(play_entered.wait(), timeout=5.0)
        assert play_calls == 1

        play_release.set()
        await asyncio.wait_for(play_done.wait(), timeout=5.0)
        assert play_calls == 1
    finally:
        play_release.set()
        await d.close_command_arbiter()


# ══════════════════════════════════════════════════════════════════════
# Test B: play/play/playlocal latest only final exec
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_B_play_play_playlocal_latest_wins():
    """Three rapid submits: only the latest (playlocal) executes."""
    d = _make_device()

    executed: list[tuple[str, str]] = []
    spy_done = asyncio.Event()

    async def _spy_play_internal(**kw):
        executed.append(("playlocal", kw.get("name", "")))
        spy_done.set()

    d._play_internal = _spy_play_internal

    try:
        assert await d.play(name="A") is True
        assert await d.play(name="B") is True
        assert await d.playlocal(name="C") is True

        await asyncio.wait_for(spy_done.wait(), timeout=5.0)
        assert executed == [("playlocal", "C")]
    finally:
        await d.close_command_arbiter()


# ══════════════════════════════════════════════════════════════════════
# Test C: STOP active → PLAY accepted (c++) but STOP barrier completes;
# then PLAY executes after barrier
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_C_stop_active_play_accepted_c_increases():
    """STOP active → PLAY accepted (command_generation increases),
    STOP barrier still completes STOPPED, then PLAY runs after."""
    d = _make_playing_device()

    stop_entered = asyncio.Event()
    stop_release = asyncio.Event()
    stop_done = asyncio.Event()

    async def _blocked_stop(payload):
        stop_entered.set()
        await asyncio.wait_for(stop_release.wait(), timeout=5.0)
        d._complete_runtime_stop(updated_at=time.time())
        if d.event_bus:
            d.event_bus.publish(PLAYER_STATE_CHANGED, device_id=d.did)
        stop_done.set()

    d._execute_stop_intent = _blocked_stop

    play_executed = asyncio.Event()
    async def _spy_play(name="", search_key="", **kw):
        play_executed.set()
        return True
    d._play = _spy_play

    events: list[str] = []
    class _Bus:
        def publish(self, event_type, **kw):
            events.append(event_type)
    d.event_bus = _Bus()

    try:
        assert await d.stop(arg1="notts") is True
        await asyncio.wait_for(stop_entered.wait(), timeout=5.0)

        c_before_play = d.get_runtime_state().command_generation
        assert await d.play(name="after_stop") is True
        assert d.get_runtime_state().command_generation == c_before_play + 1

        arb = d._command_arbiter
        assert arb.after_barrier_sequence is not None

        stop_release.set()
        await asyncio.wait_for(stop_done.wait(), timeout=5.0)
        await asyncio.wait_for(play_executed.wait(), timeout=5.0)

        assert d.get_runtime_state().phase == PlaybackPhase.STOPPED
    finally:
        stop_release.set()
        await d.close_command_arbiter()


# ══════════════════════════════════════════════════════════════════════
# Test D: STOP pending (not yet active) → PLAY cannot replace STOP
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_D_stop_pending_play_cannot_replace():
    """When STOP is pending (not yet active because executor is busy
    with a PLAY), another PLAY goes to after_barrier, does NOT replace STOP."""
    d = _make_playing_device()

    first_started = asyncio.Event()
    first_block = asyncio.Event()

    async def _blocked_play(name="", search_key="", **kw):
        first_started.set()
        await asyncio.wait_for(first_block.wait(), timeout=5.0)
        return True

    d._play = _blocked_play

    try:
        assert await d.play(name="first") is True
        await asyncio.wait_for(first_started.wait(), timeout=5.0)

        arb = d._command_arbiter
        assert arb.active_sequence is not None

        assert await d.stop(arg1="notts") is True
        assert arb.pending_sequence is not None

        assert await d.play(name="X") is True
        assert arb.after_barrier_sequence is not None
        assert arb.pending_sequence is not None
    finally:
        first_block.set()
        await d.close_command_arbiter()


# ══════════════════════════════════════════════════════════════════════
# Test E: PLAY active → STOP accepted; execution order PLAY, STOP
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_E_play_active_stop_accepted_old_play_stale():
    """PLAY active in executor, submit STOP → STOP accepted.
    Execution order: PLAY completes, then STOP."""
    d = _make_playing_device()

    play_entered = asyncio.Event()
    play_block = asyncio.Event()

    async def _blocked_play(name="", search_key="", **kw):
        play_entered.set()
        await asyncio.wait_for(play_block.wait(), timeout=5.0)
        return True

    d._play = _blocked_play

    stop_entered = asyncio.Event()
    stop_block = asyncio.Event()
    stop_done = asyncio.Event()

    async def _blocked_stop(payload):
        stop_entered.set()
        await asyncio.wait_for(stop_block.wait(), timeout=5.0)
        d._complete_runtime_stop(updated_at=time.time())
        if d.event_bus:
            d.event_bus.publish(PLAYER_STATE_CHANGED, device_id=d.did)
        stop_done.set()

    d._execute_stop_intent = _blocked_stop

    events: list[str] = []
    class _Bus:
        def publish(self, event_type, **kw):
            events.append(event_type)
    d.event_bus = _Bus()

    try:
        assert await d.play(name="A") is True
        await asyncio.wait_for(play_entered.wait(), timeout=5.0)

        assert await d.stop(arg1="notts") is True
        arb = d._command_arbiter
        assert arb.pending_sequence is not None

        play_block.set()
        await asyncio.wait_for(stop_entered.wait(), timeout=5.0)

        stop_block.set()
        await asyncio.wait_for(stop_done.wait(), timeout=5.0)

        assert d.get_runtime_state().phase == PlaybackPhase.STOPPED
        assert PLAYER_STATE_CHANGED in events
    finally:
        play_block.set()
        stop_block.set()
        await d.close_command_arbiter()


# ══════════════════════════════════════════════════════════════════════
# Test F: PAUSE → PLAY sequence PAUSE, PLAY
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_F_pause_then_play_sequence():
    """Submit PAUSE then PLAY → PAUSE executes first (barrier),
    then PLAY after.  Execution order: PAUSE, PLAY."""
    d = _make_playing_device()

    executed: list[str] = []
    pause_started = asyncio.Event()
    pause_block = asyncio.Event()
    pause_done = asyncio.Event()

    async def _blocked_pause(payload):
        executed.append("pause")
        pause_started.set()
        await asyncio.wait_for(pause_block.wait(), timeout=5.0)
        if d.event_bus:
            d.event_bus.publish(PLAYER_STATE_CHANGED, device_id=d.did)
        pause_done.set()

    d._execute_pause_intent = _blocked_pause

    play_executed = asyncio.Event()
    async def _spy_play(name="", search_key="", **kw):
        executed.append("play")
        play_executed.set()
        return True
    d._play = _spy_play

    try:
        assert await d.pause() is True
        assert await d.play(name="X") is True

        arb = d._command_arbiter
        assert arb.after_barrier_sequence is not None

        await asyncio.wait_for(pause_started.wait(), timeout=5.0)
        pause_block.set()
        await asyncio.wait_for(pause_done.wait(), timeout=5.0)
        await asyncio.wait_for(play_executed.wait(), timeout=5.0)

        assert executed == ["pause", "play"]
    finally:
        pause_block.set()
        await d.close_command_arbiter()


# ══════════════════════════════════════════════════════════════════════
# Test G: manual settle pending → explicit PLAY latest replaces manual
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_G_manual_pending_explicit_play_replaces():
    """Manual navigation settle pending → explicit play() replaces it,
    manual generation is invalidated."""
    d = _make_device()

    settle_entered = asyncio.Event()
    settle_release = asyncio.Event()

    async def _blocked_settle():
        settle_entered.set()
        await asyncio.wait_for(settle_release.wait(), timeout=5.0)

    d._wait_manual_navigation_settle = _blocked_settle

    executed: list[str] = []
    play_done = asyncio.Event()

    async def _spy_play(name="", search_key="", **kw):
        executed.append(f"play:{name}")
        play_done.set()
        return True

    d._play = _spy_play
    d._ensure_manual_navigation_state()
    d._invalidate_manual_navigation = lambda reason: _real_invalidate(d, reason)

    try:
        assert await d.play_next() is True
        await asyncio.wait_for(settle_entered.wait(), timeout=5.0)

        gen_before = d._manual_nav_generation
        assert await d.play(name="manual_replace") is True
        assert d._manual_nav_generation > gen_before

        settle_release.set()
        await asyncio.wait_for(play_done.wait(), timeout=5.0)

        assert "play:manual_replace" in executed
    finally:
        settle_release.set()
        await d.close_command_arbiter()


# ══════════════════════════════════════════════════════════════════════
# Test H2: direct _play(name='') real chain → c 0→1
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_H2_direct_play_empty_name_real_chain_command_count():
    """Direct _play(name='') with check_play_next=True: real
    _play → _play_internal → _play_next chain.  _play_next receives
    command_already_accepted=False (default), calls _accept_command,
    selects next track via get_next_music, and passes
    command_already_accepted=True down to _play → _playmusic.
    Only _playmusic is stubbed.  Total: command_generation +1."""
    d = _make_device()

    # Restore the real three-layer chain
    d._play = XiaoMusicDevice._play.__get__(d, XiaoMusicDevice)
    d._play_internal = XiaoMusicDevice._play_internal.__get__(d, XiaoMusicDevice)
    d._play_next = XiaoMusicDevice._play_next.__get__(d, XiaoMusicDevice)

    d.check_play_next = lambda: True
    d.get_cur_music = lambda: "A"
    d.get_next_music = lambda skip_one_repeat=False: "B"
    d._stage_playlist_navigation_transition = lambda name, reason: None

    playmusic_done = asyncio.Event()
    playmusic_names: list[str] = []

    async def _spy_playmusic(
        name, confirm_start_in_background=False, fast_stop=False,
        navigation_generation=None,
    ):
        playmusic_names.append(name)
        playmusic_done.set()
        return True

    d._playmusic = _spy_playmusic

    c_before = d.get_runtime_state().command_generation
    assert c_before == 0

    result = await d._play(name="")
    assert result is True

    # command_generation +1 (from _play_next accepting)
    assert d.get_runtime_state().command_generation == c_before + 1

    await asyncio.wait_for(playmusic_done.wait(), timeout=5.0)
    # _playmusic was called with the resolved track name "B"
    assert playmusic_names == ["B"]


# ══════════════════════════════════════════════════════════════════════
# Test H3: public play(name='') real chain via arbiter → c 0→1 only
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_H3_public_play_empty_name_real_chain_still_plus_one():
    """Public play(name='') with check_play_next=True: public API
    calls _accept_command (+1), arbiter → _execute_play_intent →
    _play(command_already_accepted=True) → _play_internal(True) →
    _play_next(True) → _play(command_already_accepted=True) →
    _playmusic.  Only _playmusic is stubbed.  Total: exactly +1."""
    d = _make_device()

    # Restore the real three-layer chain
    d._play = XiaoMusicDevice._play.__get__(d, XiaoMusicDevice)
    d._play_internal = XiaoMusicDevice._play_internal.__get__(d, XiaoMusicDevice)
    d._play_next = XiaoMusicDevice._play_next.__get__(d, XiaoMusicDevice)

    d.check_play_next = lambda: True
    d.get_cur_music = lambda: "A"
    d.get_next_music = lambda skip_one_repeat=False: "B"
    d._stage_playlist_navigation_transition = lambda name, reason: None

    playmusic_done = asyncio.Event()
    playmusic_names: list[str] = []

    async def _spy_playmusic(
        name, confirm_start_in_background=False, fast_stop=False,
        navigation_generation=None,
    ):
        playmusic_names.append(name)
        playmusic_done.set()
        return True

    d._playmusic = _spy_playmusic

    c_before = d.get_runtime_state().command_generation
    assert c_before == 0

    try:
        assert await d.play(name="") is True

        # Public API already bumped command_generation
        assert d.get_runtime_state().command_generation == c_before + 1

        # Wait for arbiter to finish the real chain
        await asyncio.wait_for(playmusic_done.wait(), timeout=5.0)

        # _playmusic was called with "B" from get_next_music
        assert playmusic_names == ["B"]

        # Still exactly +1 (no second accept)
        assert d.get_runtime_state().command_generation == c_before + 1
    finally:
        await d.close_command_arbiter()


# ══════════════════════════════════════════════════════════════════════
# Test H4: manual play_next real chain via arbiter → c 0→1 only
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_H4_manual_play_next_real_chain_arbiter_single_command_bump():
    """Public play_next() → _queue_manual_navigation calls _accept_command
    (+1), arbiter executor for NEXT calls _play(command_already_accepted=True)
    → _play_internal → _playmusic.  Only _playmusic is stubbed.
    Total: exactly +1."""
    d = _make_device()

    # Restore the real chain
    d._play = XiaoMusicDevice._play.__get__(d, XiaoMusicDevice)
    d._play_internal = XiaoMusicDevice._play_internal.__get__(d, XiaoMusicDevice)
    d._play_next = XiaoMusicDevice._play_next.__get__(d, XiaoMusicDevice)

    # Make _wait_manual_navigation_settle return immediately
    async def _instant_settle():
        pass
    d._wait_manual_navigation_settle = _instant_settle

    d._current_index = 0
    d.get_cur_music = lambda: "A"
    d.get_next_music = lambda skip_one_repeat=False: "B"
    d._stage_playlist_navigation_transition = lambda name, reason: None

    playmusic_done = asyncio.Event()
    playmusic_names: list[str] = []

    async def _spy_playmusic(
        name, confirm_start_in_background=False, fast_stop=False,
        navigation_generation=None,
    ):
        playmusic_names.append(name)
        playmusic_done.set()
        return True

    d._playmusic = _spy_playmusic

    c_before = d.get_runtime_state().command_generation
    assert c_before == 0

    try:
        # Public play_next → _queue_manual_navigation → _accept_command + arbiter
        assert await d.play_next() is True

        # Already +1 from _queue_manual_navigation
        assert d.get_runtime_state().command_generation == c_before + 1

        # Wait for arbiter executor to complete real chain
        await asyncio.wait_for(playmusic_done.wait(), timeout=5.0)

        # _playmusic was called with "B" from get_next_music
        assert playmusic_names == ["B"]

        # Still exactly +1 (no second accept from arbiter _play call)
        assert d.get_runtime_state().command_generation == c_before + 1
    finally:
        await d.close_command_arbiter()


# ══════════════════════════════════════════════════════════════════════
# Test I: executor exception → last_error; subsequent PLAY continues
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_I_executor_exception_last_error_play_continues():
    """First PLAY executor raises → last_error set.  Second PLAY
    still executes normally."""
    d = _make_device()

    fail_play_done = asyncio.Event()
    ok_play_done = asyncio.Event()
    call_count = 0

    async def _failing_play(name="", search_key="", **kw):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            fail_play_done.set()
            raise RuntimeError("play boom")
        ok_play_done.set()
        return True

    d._play = _failing_play

    try:
        assert await d.play(name="A") is True
        await asyncio.wait_for(fail_play_done.wait(), timeout=5.0)

        arb = d._command_arbiter
        assert arb.last_error is not None
        assert isinstance(arb.last_error, RuntimeError)
        assert str(arb.last_error) == "play boom"

        assert await d.play(name="B") is True
        await asyncio.wait_for(ok_play_done.wait(), timeout=5.0)

        assert arb.last_error is not None
        assert call_count == 2
    finally:
        await d.close_command_arbiter()


# ══════════════════════════════════════════════════════════════════════
# Test J: public play/playlocal blocked physical still fast accepted
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_J_play_blocked_physical_fast_accepted():
    """play() returns True immediately even though _play is blocked
    on physical work."""
    d = _make_device()

    play_entered = asyncio.Event()
    play_block = asyncio.Event()
    play_done = asyncio.Event()

    async def _slow_play(name="", search_key="", **kw):
        play_entered.set()
        await asyncio.wait_for(play_block.wait(), timeout=5.0)
        play_done.set()
        return True

    d._play = _slow_play

    try:
        t0 = time.monotonic()
        result = await d.play(name="A", search_key="")
        t1 = time.monotonic()

        assert result is True
        assert t1 - t0 < 0.5

        await asyncio.wait_for(play_entered.wait(), timeout=5.0)
        assert not play_done.is_set()

        play_block.set()
        await asyncio.wait_for(play_done.wait(), timeout=5.0)
    finally:
        play_block.set()
        await d.close_command_arbiter()


@pytest.mark.asyncio
async def test_J_playlocal_blocked_physical_fast_accepted():
    """playlocal() returns True immediately even though _play_internal
    is blocked on physical work."""
    d = _make_device()

    play_entered = asyncio.Event()
    play_block = asyncio.Event()

    async def _slow_playlocal(name="", search_key="", **kw):
        play_entered.set()
        await asyncio.wait_for(play_block.wait(), timeout=5.0)
        return True

    d._play_internal = _slow_playlocal

    try:
        t0 = time.monotonic()
        result = await d.playlocal(name="A")
        t1 = time.monotonic()

        assert result is True
        assert t1 - t0 < 0.5

        await asyncio.wait_for(play_entered.wait(), timeout=5.0)
    finally:
        play_block.set()
        await d.close_command_arbiter()
