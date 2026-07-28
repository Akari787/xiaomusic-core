"""T04-C2c: external URL play migration to DeviceCommandArbiter.

Tests A-J validate: fast accepted receipt, latest-wins URL dispatch,
barrier semantics (STOP before/after external), manual navigation
invalidation, legacy compatibility, single c/q/a bump, exception
handling, payload safety, context-aware same-command reuse.

Deterministic coordination via asyncio.Event + wait_for.
Zero asyncio.sleep().  Zero bare Event.wait().
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import types

import pytest

from xiaomusic.config import Device
from xiaomusic.const import PLAY_TYPE_ALL
from xiaomusic.device_player import XiaoMusicDevice
from xiaomusic.events import PLAYER_STATE_CHANGED
from xiaomusic.playback.runtime_state import (
    LifecycleToken,
    PlaybackPhase,
    PlaybackRuntimeState,
    TrackReference,
)

# ── async noops ────────────────────────────────────────────────────────────

async def _noop(*args, **kwargs):
    pass


async def _noop_return_false(*args, **kwargs):
    return False


async def _noop_return_true(*args, **kwargs):
    return True


async def _noop_return_zero():
    return 0.0


async def _noop_list(*args, **kwargs):
    return [None]


async def _noop_get_url(name):
    return f"file:///tmp/{name}.mp3", f"file:///tmp/{name}.mp3"


def _real_invalidate(d, reason):
    d._ensure_manual_navigation_state()
    d._manual_nav_generation += 1
    d._manual_nav_target = None


def _bump_sid(d):
    d._play_session_id += 1
    return d._play_session_id


async def _noop_coro():
    pass


# ── device factory ─────────────────────────────────────────────────────────


def _make_device() -> XiaoMusicDevice:
    """Create a test device with all required attributes."""
    d = XiaoMusicDevice.__new__(XiaoMusicDevice)
    d.log = logging.getLogger("test-t04c2c")
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
    # T04-C2c registry init
    d._external_context_registry = {}
    d._external_context_registry_order = []
    d._external_context_next_id = 0
    d.device = types.SimpleNamespace(
        did="did-t04c2c",
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
        stop_tts_msg="",
        enable_force_stop=True,
        auto_next_stop_wait_mode="sync",
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
    d.cancel_group_next_timer = _noop_coro
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
    d._bootstrap_playlist_session_for_external_url = lambda context: None
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


# ══════════════════════════════════════════════════════════════════════════
# Test A: blocked group_player_play → submit returns immediately;
#         release → physical completes in bg
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_A_blocked_physical_fast_accepted():
    """submit_external_url_play returns receipt immediately;
    group_player_play is blocked; release → physical completes in bg."""
    d = _make_device()

    play_entered = asyncio.Event()
    play_release = asyncio.Event()
    play_done = asyncio.Event()
    played_urls: list[str] = []

    async def _blocked_group_player_play(url):
        played_urls.append(url)
        play_entered.set()
        await asyncio.wait_for(play_release.wait(), timeout=5.0)
        play_done.set()
        return [None]

    d.group_player_play = _blocked_group_player_play

    try:
        t0 = time.monotonic()
        receipt = await d.submit_external_url_play(
            url="http://x.com/a.mp3",
            context={"k": "v"},
            resolved={"title": "song"},
        )
        t1 = time.monotonic()

        assert receipt["accepted"] is True
        assert isinstance(receipt["sequence"], int)
        assert t1 - t0 < 0.5

        await asyncio.wait_for(play_entered.wait(), timeout=5.0)
        assert played_urls == ["http://x.com/a.mp3"]
        assert not play_done.is_set()

        play_release.set()
        await asyncio.wait_for(play_done.wait(), timeout=5.0)
    finally:
        play_release.set()
        await d.close_command_arbiter()


# ══════════════════════════════════════════════════════════════════════════
# Test B: three rapid submits with different contexts → c+3, q+1, a+1
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_B_rapid_external_different_contexts():
    """Three rapid submit_external_url_play with different contexts:
    only the latest URL is physically dispatched.  c increments 3 times,
    but q+1 and a+1 only once (latest-wins single dispatch)."""
    d = _make_device()

    executed_urls: list[str] = []
    exec_done = asyncio.Event()

    async def _spy_group_player_play(url):
        executed_urls.append(url)
        exec_done.set()
        return [None]

    d.group_player_play = _spy_group_player_play

    try:
        c0 = d.get_runtime_state().command_generation
        q0 = d.get_runtime_state().queue_session_id
        a0 = d.get_runtime_state().track_attempt_id

        r1 = await d.submit_external_url_play(url="http://x.com/1.mp3", context={"id": "ctx1"})
        c_after_1 = d.get_runtime_state().command_generation
        assert c_after_1 == c0 + 1
        assert r1["accepted"] is True

        r2 = await d.submit_external_url_play(url="http://x.com/2.mp3", context={"id": "ctx2"})
        c_after_2 = d.get_runtime_state().command_generation
        assert c_after_2 == c0 + 2
        assert r2["accepted"] is True

        r3 = await d.submit_external_url_play(url="http://x.com/3.mp3", context={"id": "ctx3"})
        c_after_3 = d.get_runtime_state().command_generation
        assert c_after_3 == c0 + 3
        assert r3["accepted"] is True

        await asyncio.wait_for(exec_done.wait(), timeout=5.0)
        # Only the latest URL is dispatched
        assert executed_urls == ["http://x.com/3.mp3"]
        # q +1 and a +1 from the single physical dispatch
        assert d.get_runtime_state().queue_session_id == q0 + 1
        assert d.get_runtime_state().track_attempt_id == a0 + 1
    finally:
        await d.close_command_arbiter()


# ══════════════════════════════════════════════════════════════════════════
# Test B2: same context two-attempt XiaoMusic.play_url chain
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_B2_same_context_two_attempts():
    """Same context dict with two XiaoMusic.play_url calls:
    first dispatch all-None, second dispatch succeeds.
    q=1, c=1, a=2 — same command, two attempts."""
    from xiaomusic.xiaomusic import XiaoMusic

    xm2 = types.SimpleNamespace(
        config=types.SimpleNamespace(
            delay_sec=0, verbose=False, ffmpeg_location="",
            music_list_json="[]",
        ),
        log=logging.getLogger("test"),
        auth_manager=types.SimpleNamespace(),
        music_library=types.SimpleNamespace(
            music_list={"全部": []},
            is_music_exist=lambda n: True,
        ),
        device_manager=types.SimpleNamespace(
            get_group_device_id_list=lambda g: [],
            get_group_devices=lambda g: {},
        ),
        event_bus=None,
        analytics=types.SimpleNamespace(
            send_play_event=lambda *a, **k: None,
        ),
    )
    dev_real = Device(did="d1", device_id="d1", hardware="OH2P", name="T",
                      play_type=PLAY_TYPE_ALL, cur_playlist="全部", playlist2music={})
    d = XiaoMusicDevice(xm2, dev_real, group_name="g")
    d.cancel_group_next_timer = _noop_coro
    d._invalidate_manual_navigation = lambda reason: None
    d._bootstrap_playlist_session_for_external_url = lambda context: None

    call_count = 0
    first_group_called = asyncio.Event()
    started_called = asyncio.Event()

    async def _spy_group(url):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            first_group_called.set()
            return [None]
        return [{"code": 0}]

    d.group_player_play = _spy_group

    async def _on_started(context=None, resolved=None, *, token):
        started_called.set()

    d.on_external_url_play_started = _on_started

    xm = XiaoMusic.__new__(XiaoMusic)
    xm.log = logging.getLogger("test")
    xm.device_manager = types.SimpleNamespace(devices={"d1": d})

    ctx: dict = {}

    try:
        r1 = await XiaoMusic.play_url(xm, did="d1", arg1="http://x/direct.mp3", context=ctx)
        assert r1["accepted"] is True

        # Wait for first dispatch
        await asyncio.wait_for(first_group_called.wait(), timeout=5.0)

        r2 = await XiaoMusic.play_url(xm, did="d1", arg1="http://x/fallback.mp3", context=ctx)
        assert r2["accepted"] is True

        await asyncio.wait_for(started_called.wait(), timeout=5.0)
    finally:
        await d.close_command_arbiter()

    s = d.get_runtime_state()
    assert s.queue_session_id == 1
    assert s.command_generation == 1
    assert s.track_attempt_id == 2
    assert call_count == 2


# ══════════════════════════════════════════════════════════════════════════
# Test C: STOP active → external accepted (c+1), STOP barrier completes
#         STOPPED, then external executes after barrier
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_C_stop_active_external_after_barrier():
    """STOP active → external accepted (c+1), STOP barrier completes
    STOPPED, then external executes after barrier.  External c bump
    does NOT kill the STOP barrier."""
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

    ext_executed_urls: list[str] = []
    ext_done = asyncio.Event()

    async def _spy_group_player_play(url):
        ext_executed_urls.append(url)
        ext_done.set()
        return [None]

    d.group_player_play = _spy_group_player_play

    events: list[str] = []
    class _Bus:
        def publish(self, event_type, **kw):
            events.append(event_type)
    d.event_bus = _Bus()

    try:
        assert await d.stop(arg1="notts") is True
        await asyncio.wait_for(stop_entered.wait(), timeout=5.0)

        c_before_ext = d.get_runtime_state().command_generation
        receipt = await d.submit_external_url_play(
            url="http://x.com/ext.mp3",
        )
        assert receipt["accepted"] is True
        assert d.get_runtime_state().command_generation == c_before_ext + 1

        arb = d._command_arbiter
        assert arb.after_barrier_sequence is not None

        stop_release.set()
        await asyncio.wait_for(stop_done.wait(), timeout=5.0)
        await asyncio.wait_for(ext_done.wait(), timeout=5.0)

        # STOP completed STOPPED, then after-barrier external executed
        assert ext_executed_urls == ["http://x.com/ext.mp3"]
    finally:
        stop_release.set()
        await d.close_command_arbiter()


# ══════════════════════════════════════════════════════════════════════════
# Test D: external active → STOP accepted; STOP waits for dispatch
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_D_external_active_stop_queues():
    """External executor active → STOP accepted, queued as barrier.
    STOP waits for external dispatch to finish, then completes.
    Single executor, max concurrency 1."""
    d = _make_playing_device()

    ext_entered = asyncio.Event()
    ext_block = asyncio.Event()
    ext_done = asyncio.Event()
    ext_urls: list[str] = []

    async def _blocked_group_player_play(url):
        ext_urls.append(url)
        ext_entered.set()
        await asyncio.wait_for(ext_block.wait(), timeout=5.0)
        ext_done.set()
        return [None]

    d.group_player_play = _blocked_group_player_play

    stop_executed = asyncio.Event()
    stop_done = asyncio.Event()

    async def _spy_stop(payload):
        stop_executed.set()
        d._complete_runtime_stop(updated_at=time.time())
        if d.event_bus:
            d.event_bus.publish(PLAYER_STATE_CHANGED, device_id=d.did)
        stop_done.set()

    d._execute_stop_intent = _spy_stop

    events: list[str] = []
    class _Bus:
        def publish(self, event_type, **kw):
            events.append(event_type)
    d.event_bus = _Bus()

    try:
        receipt = await d.submit_external_url_play(url="http://x.com/ext.mp3")
        assert receipt["accepted"] is True

        await asyncio.wait_for(ext_entered.wait(), timeout=5.0)

        assert await d.stop(arg1="notts") is True

        arb = d._command_arbiter
        assert arb.pending_sequence is not None

        ext_block.set()
        await asyncio.wait_for(ext_done.wait(), timeout=5.0)
        await asyncio.wait_for(stop_done.wait(), timeout=5.0)

        assert d.get_runtime_state().phase == PlaybackPhase.STOPPED
        assert ext_urls == ["http://x.com/ext.mp3"]
    finally:
        ext_block.set()
        await d.close_command_arbiter()


# ══════════════════════════════════════════════════════════════════════════
# Test E: manual settle pending → external immediately invalidates
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_E_manual_pending_external_invalidates():
    """Manual navigation settle pending → external submit invalidates
    manual immediately and replaces the pending intent."""
    d = _make_device()

    settle_entered = asyncio.Event()
    settle_release = asyncio.Event()

    async def _blocked_settle():
        settle_entered.set()
        await asyncio.wait_for(settle_release.wait(), timeout=5.0)

    d._wait_manual_navigation_settle = _blocked_settle

    ext_done = asyncio.Event()
    ext_urls: list[str] = []

    async def _spy_group_player_play(url):
        ext_urls.append(url)
        ext_done.set()
        return [None]

    d.group_player_play = _spy_group_player_play
    d._ensure_manual_navigation_state()
    d._invalidate_manual_navigation = lambda reason: _real_invalidate(d, reason)

    try:
        assert await d.play_next() is True
        await asyncio.wait_for(settle_entered.wait(), timeout=5.0)

        gen_before = d._manual_nav_generation
        receipt = await d.submit_external_url_play(url="http://x.com/ext.mp3")
        assert receipt["accepted"] is True
        assert d._manual_nav_generation > gen_before

        settle_release.set()
        await asyncio.wait_for(ext_done.wait(), timeout=5.0)

        assert ext_urls == ["http://x.com/ext.mp3"]
    finally:
        settle_release.set()
        await d.close_command_arbiter()


# ══════════════════════════════════════════════════════════════════════════
# Test F: legacy PLAY and external latest share normal slot
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_F_legacy_play_and_external_latest_share_slot():
    """play('A') → submit_external_url_play('B') → only external
    executes (latest-wins, same regular slot)."""
    d = _make_device()

    executed: list[str] = []
    done = asyncio.Event()

    d._play = _noop_return_false  # won't be called (replaced)

    async def _spy_group_player_play(url):
        executed.append(f"external:{url}")
        done.set()
        return [None]

    d.group_player_play = _spy_group_player_play

    try:
        assert await d.play(name="A") is True
        receipt = await d.submit_external_url_play(url="http://x.com/B.mp3")
        assert receipt["accepted"] is True

        await asyncio.wait_for(done.wait(), timeout=5.0)
        assert executed == ["external:http://x.com/B.mp3"]
    finally:
        await d.close_command_arbiter()


# ══════════════════════════════════════════════════════════════════════════
# Test G: direct old on_external_url_play default still q/c each +1
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_G_direct_on_external_url_play_legacy_qc_bump():
    """Direct call to on_external_url_play with default parameters
    (command_already_accepted=False, manual_already_invalidated=False)
    still bumps q and c each +1.  Legacy caller behavior preserved."""
    d = _make_device()

    q0 = d.get_runtime_state().queue_session_id
    c0 = d.get_runtime_state().command_generation

    token = await d.on_external_url_play(context={"title": "legacy"})

    assert token is not None
    assert isinstance(token, LifecycleToken)
    assert d.get_runtime_state().queue_session_id == q0 + 1
    assert d.get_runtime_state().command_generation == c0 + 1


# ══════════════════════════════════════════════════════════════════════════
# Test H: arbiter external path only c+1, q+1, a+1, no double
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_H_arbiter_external_single_each_bump():
    """Through the arbiter path, submit_external_url_play bumps c+1
    only.  Then _execute_external_play_intent calls on_external_url_play
    with command_already_accepted=True, which bumps q+1 but skips c.
    group_player_play bumps a+1.  Total: c+1, q+1, a+1 — no double."""
    d = _make_device()

    exec_done = asyncio.Event()
    group_urls: list[str] = []

    async def _spy_group_player_play(url):
        group_urls.append(url)
        exec_done.set()
        return [None]

    d.group_player_play = _spy_group_player_play

    c0 = d.get_runtime_state().command_generation
    q0 = d.get_runtime_state().queue_session_id
    a0 = d.get_runtime_state().track_attempt_id

    try:
        receipt = await d.submit_external_url_play(
            url="http://x.com/h.mp3",
            context={"title": "h-test"},
        )
        assert receipt["accepted"] is True

        # After submit: only c bumped
        c_after_submit = d.get_runtime_state().command_generation
        q_after_submit = d.get_runtime_state().queue_session_id
        a_after_submit = d.get_runtime_state().track_attempt_id
        assert c_after_submit == c0 + 1
        assert q_after_submit == q0
        assert a_after_submit == a0

        # Wait for physical dispatch
        await asyncio.wait_for(exec_done.wait(), timeout=5.0)

        # After physical: q+1 and a+1
        c_final = d.get_runtime_state().command_generation
        q_final = d.get_runtime_state().queue_session_id
        a_final = d.get_runtime_state().track_attempt_id
        assert c_final == c0 + 1  # still exactly +1
        assert q_final == q0 + 1
        assert a_final == a0 + 1
        assert group_urls == ["http://x.com/h.mp3"]
    finally:
        await d.close_command_arbiter()


# ══════════════════════════════════════════════════════════════════════════
# Test I: dispatch exception → arbiter.last_error, no fake PLAYING
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_I_dispatch_exception_last_error_continues():
    """First external dispatch raises → last_error set, no fake
    PLAYING phase.  Second external dispatch succeeds normally."""
    d = _make_device()

    fail_call_done = asyncio.Event()
    ok_call_done = asyncio.Event()
    call_count = 0

    async def _failing_then_ok_group_player_play(url):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            fail_call_done.set()
            raise RuntimeError("dispatch boom")
        ok_call_done.set()
        return [None]

    d.group_player_play = _failing_then_ok_group_player_play

    try:
        # Wrap executor before submitting so the spy catches the error
        executor_done_i = asyncio.Event()
        _orig_exec_i = d._execute_external_play_intent
        async def _spy_exec_i(payload):
            try:
                await _orig_exec_i(payload)
            finally:
                executor_done_i.set()
        d._execute_external_play_intent = _spy_exec_i

        receipt = await d.submit_external_url_play(
            url="http://x.com/fail.mp3", context={"id": "fail"}
        )
        assert receipt["accepted"] is True

        await asyncio.wait_for(fail_call_done.wait(), timeout=5.0)

        # Wait for executor to finish
        await asyncio.wait_for(executor_done_i.wait(), timeout=5.0)

        arb = d._command_arbiter
        assert arb.last_error is not None
        assert isinstance(arb.last_error, RuntimeError)
        assert "dispatch boom" in str(arb.last_error)

        # Phase should NOT be PLAYING
        assert d.get_runtime_state().phase != PlaybackPhase.PLAYING

        # Second external succeeds (different context)
        receipt2 = await d.submit_external_url_play(
            url="http://x.com/ok.mp3", context={"id": "ok"}
        )
        assert receipt2["accepted"] is True
        await asyncio.wait_for(ok_call_done.wait(), timeout=5.0)
        assert call_count == 2
    finally:
        await d.close_command_arbiter()


# ══════════════════════════════════════════════════════════════════════════
# Test J: caller mutation doesn't affect internal context
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_J_caller_mutation_isolated_from_executor():
    """After submit_external_url_play, mutating the caller's context dict
    does NOT affect the internal context used by the executor."""
    d = _make_device()

    title_seen: list[str] = []
    exec_done = asyncio.Event()

    async def _spy_group_player_play(url):
        exec_done.set()
        return [{"code": 0}]

    d.group_player_play = _spy_group_player_play

    async def _on_started_spy(context=None, resolved=None, *, token):
        title_seen.append(str((context or {}).get("title", "")))

    d.on_external_url_play_started = _on_started_spy

    try:
        context = {"title": "original", "nested": {"deep": "value"}}
        receipt = await d.submit_external_url_play(
            url="http://x.com/j.mp3",
            context=context,
        )

        # Receipt is JSON serializable
        json_str = json.dumps(receipt)
        parsed = json.loads(json_str)
        assert parsed["accepted"] is True
        assert isinstance(parsed["sequence"], int)

        # Mutate caller's context AFTER submission
        context["title"] = "mutated"
        context["nested"]["deep"] = "mutated-deep"

        await asyncio.wait_for(exec_done.wait(), timeout=5.0)

        # Executor should see "original", not "mutated"
        assert len(title_seen) >= 1
        assert title_seen[0] == "original"
    finally:
        await d.close_command_arbiter()


# ══════════════════════════════════════════════════════════════════════════
# AST-1: XiaoMusic.play_url must NOT await on_external_url_play etc.
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_AST_play_url_no_direct_physical_calls():
    """AST: XiaoMusic.play_url source code must not contain direct await
    of on_external_url_play, group_player_play, or
    on_external_url_play_started."""
    import ast
    import os

    src_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "xiaomusic", "xiaomusic.py"
    )
    src_path = os.path.abspath(src_path)

    with open(src_path, encoding="utf-8") as f:
        source = f.read()

    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "play_url":
            forbidden = {
                "on_external_url_play",
                "group_player_play",
                "on_external_url_play_started",
            }
            for child in ast.walk(node):
                if isinstance(child, ast.Attribute) and child.attr in forbidden:
                    pytest.fail(
                        f"XiaoMusic.play_url must not reference {child.attr}"
                    )
                if isinstance(child, ast.Name) and child.id in forbidden:
                    pytest.fail(
                        f"XiaoMusic.play_url must not reference {child.id}"
                    )
            break
    else:
        pytest.fail("play_url method not found in xiaomusic.py")


# ══════════════════════════════════════════════════════════════════════════
# Failure atomicity: preparation / submit failures produce zero writes
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_external_submit_closed_arbiter_has_zero_writes():
    from xiaomusic.playback.command_arbiter import ArbiterClosedError

    d = _make_device()
    context = {"title": "unchanged"}
    arbiter = d._get_or_create_arbiter()
    await arbiter.close()
    before = (
        dict(d._external_context_registry),
        list(d._external_context_registry_order),
        d._external_context_next_id,
        dict(context),
        d.get_runtime_state().command_generation,
        d._manual_nav_generation,
        d._last_cmd,
    )

    with pytest.raises(ArbiterClosedError):
        await d.submit_external_url_play("http://x/closed.mp3", context=context)

    after = (
        dict(d._external_context_registry),
        list(d._external_context_registry_order),
        d._external_context_next_id,
        dict(context),
        d.get_runtime_state().command_generation,
        d._manual_nav_generation,
        d._last_cmd,
    )
    assert after == before


@pytest.mark.asyncio
async def test_external_submit_deepcopy_failure_has_zero_writes():
    class _BadContext(dict):
        def __deepcopy__(self, memo):
            raise RuntimeError("copy failed")

    d = _make_device()
    context = _BadContext(title="unchanged")
    before = (
        dict(d._external_context_registry),
        list(d._external_context_registry_order),
        d._external_context_next_id,
        dict(context),
        d.get_runtime_state().command_generation,
        d._manual_nav_generation,
        d._last_cmd,
        d._command_arbiter,
    )

    with pytest.raises(RuntimeError, match="copy failed"):
        await d.submit_external_url_play("http://x/copy.mp3", context=context)

    after = (
        dict(d._external_context_registry),
        list(d._external_context_registry_order),
        d._external_context_next_id,
        dict(context),
        d.get_runtime_state().command_generation,
        d._manual_nav_generation,
        d._last_cmd,
        d._command_arbiter,
    )
    assert after == before


# ══════════════════════════════════════════════════════════════════════════
# AST-2: submit_external_url_play must NOT call physical methods
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_AST_external_physical_chain_only_in_executor():
    """AST: _execute_external_play_intent is the only method that
    calls group_player_play for external URLs.  submit_external_url_play
    must NOT call group_player_play."""
    import ast
    import os

    src_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "xiaomusic", "device_player.py"
    )
    src_path = os.path.abspath(src_path)

    with open(src_path, encoding="utf-8") as f:
        source = f.read()

    tree = ast.parse(source)

    submit_found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "submit_external_url_play":
            for child in ast.walk(node):
                if isinstance(child, ast.Attribute):
                    assert child.attr not in ("group_player_play", "on_external_url_play_started"), (
                        f"submit_external_url_play must NOT reference {child.attr}"
                    )
                if isinstance(child, ast.Name):
                    assert child.id not in ("group_player_play", "on_external_url_play_started"), (
                        f"submit_external_url_play must NOT reference {child.id}"
                    )
            submit_found = True

    assert submit_found, "submit_external_url_play method not found"

    executor_found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_execute_external_play_intent":
            has_gpp = False
            has_oes = False
            for child in ast.walk(node):
                if isinstance(child, ast.Attribute):
                    if child.attr == "group_player_play":
                        has_gpp = True
                    if child.attr == "on_external_url_play_started":
                        has_oes = True
            assert has_gpp, "_execute_external_play_intent must call group_player_play"
            assert has_oes, "_execute_external_play_intent must call on_external_url_play_started"
            executor_found = True

    assert executor_found, "_execute_external_play_intent method not found"
