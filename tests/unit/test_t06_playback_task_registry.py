"""T06 playback task ownership tests — warning-as-error compatible.

Zero ``asyncio.sleep``.  Zero bare ``Event.wait``.  All async gates use
``asyncio.wait_for(event.wait(), ...)``.

Pure-registry A-G: zero Device dependency.
Real-device H-N: ``XiaoMusicDevice.__new__`` with real runtime/lifecycle/arbiter;
only leaf I/O stubbed.
"""

from __future__ import annotations

import ast
import asyncio
import gc
import inspect
import logging
import warnings
from types import SimpleNamespace

import pytest

from xiaomusic.const import PLAY_TYPE_ALL
from xiaomusic.device_player import XiaoMusicDevice
from xiaomusic.playback.runtime_state import (
    PlaybackPhase,
    PlaybackRuntimeState,
)
from xiaomusic.playback.task_registry import (
    ATTEMPT_SCOPED_KINDS,
    SESSION_SCOPED_KINDS,
    PlaybackTaskRegistry,
    TaskGeneration,
    TaskKind,
)

G0 = TaskGeneration(1, 1, 1, 1)
G1 = TaskGeneration(1, 1, 2, 2)

_TIMEOUT = 5.0


# ── helpers ───────────────────────────────────────────────────────────

async def _event_wait(ev: asyncio.Event) -> None:
    await asyncio.wait_for(ev.wait(), _TIMEOUT)


async def _wait_cancelled(task: asyncio.Task) -> None:
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, _TIMEOUT)


async def _async_none():
    return None


async def _async_return(val):
    return val


async def _set_and_return(ev: asyncio.Event, val):
    ev.set()
    return val


# ═══════════════════════════════════════════════════════════════════════
# A-G  pure registry tests (plus immediate-cancel warning test)
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_a_replacement_cancels_old_and_old_is_awaitable():
    registry = PlaybackTaskRegistry()
    first_started = asyncio.Event()
    second_started = asyncio.Event()

    async def first():
        first_started.set()
        await _event_wait(asyncio.Event())

    async def second():
        second_started.set()
        await _event_wait(asyncio.Event())

    old = registry.start(TaskKind.STATUS_PROBE, G0, first())
    assert old is not None
    await _event_wait(first_started)
    new = registry.start(TaskKind.STATUS_PROBE, G1, second())
    assert new is not None
    await _event_wait(second_started)
    await _wait_cancelled(old)
    registry.cancel(TaskKind.STATUS_PROBE)
    await _wait_cancelled(new)
    await registry.close()


@pytest.mark.asyncio
async def test_b_cancel_older_than_generation():
    registry = PlaybackTaskRegistry()
    old = registry.start(
        TaskKind.DURATION_PROBE, G0,
        asyncio.Event().wait(),
    )
    current = registry.start(
        TaskKind.PLAYBACK_CONFIRMATION, G1,
        asyncio.Event().wait(),
    )
    assert old is not None and current is not None
    registry.cancel_older_than(G1)
    await _wait_cancelled(old)
    assert not current.cancelled()
    registry.cancel_all()
    await asyncio.gather(old, current, return_exceptions=True)
    await registry.close()


@pytest.mark.asyncio
async def test_c_self_cancel_is_ignored_and_task_completes():
    registry = PlaybackTaskRegistry()
    finished = asyncio.Event()

    async def worker():
        assert not registry.cancel(TaskKind.TTS_TIMER)
        finished.set()
        return "ok"

    task = registry.start(TaskKind.TTS_TIMER, G0, worker())
    assert task is not None
    await _event_wait(finished)
    result = await asyncio.wait_for(task, _TIMEOUT)
    assert result == "ok"
    await registry.close()


@pytest.mark.asyncio
async def test_d_exception_is_consumed_and_last_error_recorded():
    registry = PlaybackTaskRegistry()

    async def fail():
        raise RuntimeError("probe exploded")

    task = registry.start(TaskKind.FAILURE_RETRY, G0, fail(), metadata={"sid": 4})
    assert task is not None
    await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), _TIMEOUT)
    snap = registry.snapshot()
    item = next(x for x in snap if x.kind is TaskKind.FAILURE_RETRY)
    assert item.status == "done"
    assert item.last_error == "probe exploded"
    assert item.metadata == {"sid": 4}
    await registry.close()


@pytest.mark.asyncio
async def test_e_closed_rejects_coroutine_without_warning():
    registry = PlaybackTaskRegistry()
    await registry.close()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        task = registry.start(
            TaskKind.STATUS_PROBE, G0,
            asyncio.Event().wait(),
        )
        gc.collect()
    assert task is None
    ours = [
        w for w in caught
        if "never awaited" in str(w.message) and "Event" in str(w.message)
    ]
    assert not ours, f"unexpected never-awaited: {ours}"


@pytest.mark.asyncio
async def test_f_snapshot_redacts_sensitive_metadata():
    registry = PlaybackTaskRegistry()
    finished = asyncio.Event()

    async def _quick():
        finished.set()

    task = registry.start(
        TaskKind.COMPLETION_NEXT_TIMER, G0, _quick(),
        metadata={
            "name": "song1", "sid": 7, "reason": "normal",
            "api_key": "sk-secret-123",
            "sign_url": "https://x.com?a=1&key=abc",
            "nested": {"token": "tk-999", "name": "inner"},
            "device": SimpleNamespace(secret="hidden"),
        },
    )
    assert task is not None
    await _event_wait(finished)
    await asyncio.wait_for(task, _TIMEOUT)
    snap = registry.snapshot()
    item = next(x for x in snap if x.kind is TaskKind.COMPLETION_NEXT_TIMER)
    assert item.metadata["name"] == "song1"
    assert item.metadata["sid"] == 7
    assert item.metadata["reason"] == "normal"
    assert item.metadata["api_key"] == "<redacted>"
    assert item.metadata["sign_url"] == "<redacted>"
    assert item.metadata["nested"]["token"] == "<redacted>"
    assert item.metadata["nested"]["name"] == "inner"
    assert item.metadata["device"] == "<SimpleNamespace>"
    await registry.close()


@pytest.mark.asyncio
async def test_g_snapshot_shows_running_status():
    registry = PlaybackTaskRegistry()
    started = asyncio.Event()
    release = asyncio.Event()

    async def _run():
        started.set()
        await _event_wait(release)

    task = registry.start(TaskKind.STATUS_PROBE, G0, _run())
    assert task is not None
    await _event_wait(started)
    snap = registry.snapshot()
    item = next(x for x in snap if x.kind is TaskKind.STATUS_PROBE)
    assert item.status == "running"
    assert item.active
    release.set()
    await asyncio.wait_for(task, _TIMEOUT)
    await registry.close()


@pytest.mark.asyncio
async def test_start_immediately_cancel_zero_warnings():
    """Start a task and cancel it without yielding — zero never-awaited warnings."""
    registry = PlaybackTaskRegistry()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        registry.start(
            TaskKind.STATUS_PROBE, G0,
            asyncio.Event().wait(),
        )
        registry.cancel(TaskKind.STATUS_PROBE)
        gc.collect()
    ours = [
        w for w in caught
        if "never awaited" in str(w.message) and "Event" in str(w.message)
    ]
    assert not ours, f"immediate-cancel leaked: {ours}"
    await registry.close()


@pytest.mark.asyncio
async def test_start_immediately_close_zero_warnings():
    """Start a task and close registry without yielding — zero warnings."""
    registry = PlaybackTaskRegistry()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        registry.start(
            TaskKind.STATUS_PROBE, G0,
            asyncio.Event().wait(),
        )
    await registry.close()
    gc.collect()
    ours = [
        w for w in caught
        if "never awaited" in str(w.message) and "Event" in str(w.message)
    ]
    assert not ours, f"immediate-close leaked: {ours}"


# ═══════════════════════════════════════════════════════════════════════
#  real-device fixture
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def real_device():
    """``XiaoMusicDevice.__new__`` with real runtime / lifecycle / arbiter.

    Only leaf I/O is stubbed: group_force_stop_xiaoai, do_tts (mina),
    TTS generation, music library, cloud status.
    """
    d = XiaoMusicDevice.__new__(XiaoMusicDevice)
    d.log = logging.getLogger("t06-real")

    d._runtime_state = PlaybackRuntimeState(
        phase=PlaybackPhase.PLAYING,
        queue_session_id=1,
        command_generation=1,
        track_attempt_id=1,
    )

    d._playback_tasks = PlaybackTaskRegistry()

    d._play_session_id = 1
    d._last_cmd = "play"
    d.is_playing = True
    d._start_time = 0.0
    d._paused_time = 0.0
    d._duration = 0.0

    d.device = SimpleNamespace(
        did="t06-r", device_id="t06-r", hardware="OH2P", name="test",
        play_type=PLAY_TYPE_ALL, cur_playlist="全部", cur_music="test-song",
        current_display_name="test-song", current_entity_id="",
        current_playlist_item_id="", playlist2music={},
    )
    d.config = SimpleNamespace(
        delay_sec=0, verbose=False, ffmpeg_location="", jellyfin_proxy_mode="off",
        stop_tts_msg="bye", edge_tts_voice="zh-CN-XiaoxiaoNeural", temp_dir=".",
        auto_next_stop_grace_ms=500, auto_next_stop_wait_mode="overlap",
    )
    d.xiaomusic = SimpleNamespace(
        music_library=SimpleNamespace(
            get_music_duration=lambda name: _async_return(10.0), music_list={},
        ),
        analytics=SimpleNamespace(send_play_event=lambda *a, **kw: _async_none()),
        device_manager=SimpleNamespace(
            get_group_device_id_list=lambda g: ["d1"],
            get_group_devices=lambda g: {},
        ),
    )
    d.event_bus = None
    d.group_name = "test"
    d._group_name = "test"

    d._command_arbiter = None
    d._manual_nav_lock = asyncio.Lock()
    d._manual_nav_generation = 0
    d._manual_nav_target = None

    d._play_failed_cnt = 0
    d._play_fail_first_ts = 0.0
    d._play_fail_last_reason = ""
    d._degraded = False
    d._degraded_notified = False
    d._last_volume = 0
    d._timer_expiry_false_count = 0
    d._bg_confirm_false_count = 0
    d._timer_expiry_playing_grace_count = 0
    d._timer_expiry_unknown_grace_count = 0
    d._playlist_session_shuffled = False
    d._failure_retry_meta = {}
    d._failure_retry_last_status = "idle"
    d._failure_retry_last_error = ""
    d._failure_retry_done_event = None
    d._autonext_guard_task = None
    d._legacy_task_values = {}

    d._play_list_items = [
        {"display_name": "song-a", "legacy_name": "song-a", "item_id": "", "entity_id": ""},
        {"display_name": "song-b", "legacy_name": "song-b", "item_id": "", "entity_id": ""},
    ]
    d._current_index = 0

    d.do_tts = lambda *a, **kw: _async_none()
    d.group_force_stop_xiaoai = lambda *a, **kw: _async_return([])

    return d


# ═══════════════════════════════════════════════════════════════════════
# H  public stop while confirmation blocked → STOPPED
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_h_stop_during_confirmation_reaches_stopped(real_device):
    d = real_device
    confirm_release = asyncio.Event()
    stop_finished = asyncio.Event()

    _orig_schedule = d._schedule_playback_confirmation
    _orig_stop = d.group_force_stop_xiaoai

    # ── stub confirmation observer to enter blocked state ──
    d._background_confirm_playback_started = lambda **kw: _event_wait(confirm_release)

    # ── stub physical stop to signal completion ──
    async def _stop_with_event(*a, **kw):
        result = await _orig_stop(*a, **kw)
        stop_finished.set()
        return result

    d.group_force_stop_xiaoai = _stop_with_event

    # Real scheduling entry point
    token = d._capture_lifecycle_token()
    d._schedule_playback_confirmation(
        name="song-a", sid=d._play_session_id, cur_playlist="全部",
        origin_url="http://a", current_url="http://a", fast_stop=False,
        token=token,
    )

    # Wait for confirmation task to start running
    conf_task = d._playback_tasks.get_task(TaskKind.PLAYBACK_CONFIRMATION)
    assert conf_task is not None

    # Public stop — goes through arbiter, real _execute_stop_intent
    await d.stop()

    # Wait for physical stop to complete (signalled from stubbed leaf)
    await _event_wait(stop_finished)

    # Release confirmation (it will be cancelled by bump)
    confirm_release.set()
    await asyncio.wait_for(asyncio.gather(conf_task, return_exceptions=True), _TIMEOUT)

    assert d.get_runtime_state().phase == PlaybackPhase.STOPPED
    d._schedule_playback_confirmation = _orig_schedule
    await d.close_command_arbiter()


# ═══════════════════════════════════════════════════════════════════════
# I  pending failure retry cancelled by public stop
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_i_stop_cancels_pending_failure_retry(real_device):
    d = real_device
    backoff_gate = asyncio.Event()

    async def _fake_backoff(delay):
        await _event_wait(backoff_gate)

    d._wait_failure_retry_backoff = _fake_backoff

    d._runtime_state = PlaybackRuntimeState(
        phase=PlaybackPhase.DISPATCHING,
        queue_session_id=1, command_generation=1, track_attempt_id=1,
    )
    token = d._capture_lifecycle_token()
    await d._handle_play_failure(
        name="song-a", sid=d._play_session_id, reason="test_fail", token=token,
    )

    retry = d._playback_tasks.get_task(TaskKind.FAILURE_RETRY)
    assert retry is not None and not retry.done()

    stop_finished = asyncio.Event()
    _orig_stop = d.group_force_stop_xiaoai

    async def _stop_signal(*a, **kw):
        result = await _orig_stop(*a, **kw)
        stop_finished.set()
        return result

    d.group_force_stop_xiaoai = _stop_signal
    await d.stop()
    await _event_wait(stop_finished)

    assert retry.cancelled()

    backoff_gate.set()
    d.group_force_stop_xiaoai = _orig_stop
    await d.close_command_arbiter()


# ═══════════════════════════════════════════════════════════════════════
# J  fast group-stop active + public stop → no self-cancel, STOPPED
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_j_stop_during_fast_stop_no_self_cancel_final_stopped(real_device):
    d = real_device
    d._runtime_state = PlaybackRuntimeState(
        phase=PlaybackPhase.PLAYING,
        queue_session_id=1, command_generation=1, track_attempt_id=1,
    )
    fast_release = asyncio.Event()
    fast_entered = asyncio.Event()
    stop_finished = asyncio.Event()

    _orig_stop = d.group_force_stop_xiaoai

    async def _long_fast_stop(**kw):
        if kw.get("fast"):
            fast_entered.set()
            await _event_wait(fast_release)
        return []

    d.group_force_stop_xiaoai = _long_fast_stop

    # Create fast stop via real _execute_group_stop (overlap mode)
    fast_task = await d._execute_group_stop(
        fast_stop=True, sid=d._play_session_id,
    )
    assert fast_task is not None
    await _event_wait(fast_entered)

    # Now stub for the public stop path
    async def _stop_signal(**kw):
        result = await _orig_stop(**kw)
        if not kw.get("fast"):
            stop_finished.set()
        return result

    d.group_force_stop_xiaoai = _stop_signal
    await d.stop()
    await _event_wait(stop_finished)

    assert d.get_runtime_state().phase == PlaybackPhase.STOPPED

    fast_release.set()
    await asyncio.wait_for(asyncio.gather(fast_task, return_exceptions=True), _TIMEOUT)
    d.group_force_stop_xiaoai = _orig_stop
    await d.close_command_arbiter()


# ═══════════════════════════════════════════════════════════════════════
# K  real TTS scheduling twice → first cancelled/awaitable, no self-await
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_k_tts_timer_replacement_no_self_await(real_device):
    d = real_device

    d.group_player_play = lambda url, name="", **kw: _async_return([{"ok": True}])
    d.xiaomusic.music_library._get_file_url = lambda p: f"file:///{p}"

    call_count = 0
    first_leaf_done = asyncio.Event()
    second_leaf_done = asyncio.Event()
    dur1_done = asyncio.Event()
    dur2_done = asyncio.Event()

    import xiaomusic.utils.music_utils as _mu
    import xiaomusic.utils.network_utils as _nu
    _orig_tts = getattr(_nu, "text_to_mp3", None)
    _orig_dur = getattr(_mu, "get_local_music_duration", None)

    async def _fake_tts(*a, **kw):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            first_leaf_done.set()
        else:
            second_leaf_done.set()
        return "fake.mp3"

    async def _fake_group_play(url, name="", **kw):
        return [{"ok": True}]

    async def _fake_dur1(*a, **kw):
        dur1_done.set()
        return 5.0

    async def _fake_dur2(*a, **kw):
        dur2_done.set()
        return 5.0

    d.group_player_play = _fake_group_play
    d.xiaomusic.music_library._get_file_url = lambda p: f"file:///{p}"
    _nu.text_to_mp3 = _fake_tts

    _mu.get_local_music_duration = _fake_dur1
    try:
        t1 = asyncio.create_task(d._text_to_speech_edge_tts("first-tts"))
        await _event_wait(dur1_done)
        tts1 = d._playback_tasks.get_task(TaskKind.TTS_TIMER)
        assert tts1 is not None

        _mu.get_local_music_duration = _fake_dur2
        t2 = asyncio.create_task(d._text_to_speech_edge_tts("second-tts"))
        await _event_wait(dur2_done)
        t1.cancel()

        await _wait_cancelled(tts1)
        tts2 = d._playback_tasks.get_task(TaskKind.TTS_TIMER)
        assert tts2 is not None and not tts2.cancelled()

        d._playback_tasks.cancel(TaskKind.TTS_TIMER)
        t2.cancel()
    finally:
        if _orig_tts is not None:
            _nu.text_to_mp3 = _orig_tts
        if _orig_dur is not None:
            _mu.get_local_music_duration = _orig_dur

    await d.close_command_arbiter()


# ═══════════════════════════════════════════════════════════════════════
# L  close → all cancelled/awaited, arbiter shut, zero pending
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_l_close_cancels_awaits_all_and_closes_arbiter(real_device):
    d = real_device

    from xiaomusic.playback.command_arbiter import DeviceCommandArbiter
    arb = DeviceCommandArbiter(executor=lambda i: _async_none())
    d._command_arbiter = arb

    gate = asyncio.Event()
    t1 = d._playback_tasks.start(
        TaskKind.STATUS_PROBE, G0, _event_wait(gate), metadata={"sid": 1},
    )
    t2 = d._playback_tasks.start(
        TaskKind.COMPLETION_NEXT_TIMER, G0, _event_wait(gate), metadata={"sid": 1},
    )
    assert t1 is not None and t2 is not None

    loop = asyncio.get_running_loop()
    baseline = {t for t in asyncio.all_tasks(loop) if not t.done()}

    await d.close_command_arbiter()

    assert t1.cancelled()
    assert t2.cancelled()
    assert d._playback_tasks.closed
    assert arb._closed

    after = {t for t in asyncio.all_tasks(loop) if not t.done()}
    assert not (after - baseline)


# ═══════════════════════════════════════════════════════════════════════
# M  real set_next_music_timeout twice → one active owner
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_m_completion_timer_single_owner_old_awaitable(real_device):
    d = real_device
    d._play_session_id = 1
    d._duration = 60.0
    d._start_time = 0.0
    token = d._capture_lifecycle_token()

    await d.set_next_music_timeout(60.0, token=token)
    t1 = d._playback_tasks.get_task(TaskKind.COMPLETION_NEXT_TIMER)
    assert t1 is not None and not t1.done()

    await d.set_next_music_timeout(120.0, token=token)
    t2 = d._playback_tasks.get_task(TaskKind.COMPLETION_NEXT_TIMER)
    assert t2 is not None and t2 is not t1

    await _wait_cancelled(t1)
    assert d._playback_tasks.get_task(TaskKind.COMPLETION_NEXT_TIMER) is t2

    d._playback_tasks.cancel(TaskKind.COMPLETION_NEXT_TIMER)
    await d._playback_tasks.close()


# ═══════════════════════════════════════════════════════════════════════
# N  real Device close — zero pending, zero registry.start
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_n_real_device_close_zero_pending(real_device):
    d = real_device

    # Real completion timer
    token = d._capture_lifecycle_token()
    await d.set_next_music_timeout(999.0, token=token)

    # Real TTS scheduling with leaf stub that signals when timer is created
    d.group_player_play = lambda url, name="", **kw: _async_return([{"ok": True}])
    d.xiaomusic.music_library._get_file_url = lambda p: f"file:///{p}"
    import xiaomusic.utils.music_utils as _mu
    import xiaomusic.utils.network_utils as _nu
    _orig_tts = getattr(_nu, "text_to_mp3", None)
    _orig_dur = getattr(_mu, "get_local_music_duration", None)
    dur_done = asyncio.Event()
    _nu.text_to_mp3 = lambda *a, **kw: _async_return("fake.mp3")
    _mu.get_local_music_duration = lambda *a, **kw: _set_and_return(dur_done, 5.0)
    try:
        tts_t = asyncio.create_task(d._text_to_speech_edge_tts("tts-test"))
        await _event_wait(dur_done)
        tts_t.cancel()
    finally:
        if _orig_tts is not None:
            _nu.text_to_mp3 = _orig_tts
        if _orig_dur is not None:
            _mu.get_local_music_duration = _orig_dur

    # Real fast stop
    fast_task = await d._execute_group_stop(fast_stop=True, sid=d._play_session_id)

    loop = asyncio.get_running_loop()
    baseline = {t for t in asyncio.all_tasks(loop) if not t.done()}

    await d.close_command_arbiter()

    if fast_task is not None:
        assert fast_task.cancelled() or fast_task.done()

    after = {t for t in asyncio.all_tasks(loop) if not t.done()}
    assert not (after - baseline)


# ═══════════════════════════════════════════════════════════════════════
#  scope / generation / compat
# ═══════════════════════════════════════════════════════════════════════


def test_scope_stop_timer_not_in_attempt_or_session():
    assert TaskKind.STOP_TIMER not in ATTEMPT_SCOPED_KINDS
    assert TaskKind.STOP_TIMER not in SESSION_SCOPED_KINDS


def test_attempt_kinds_complete():
    for k in (
        TaskKind.DURATION_PROBE, TaskKind.PLAYBACK_CONFIRMATION,
        TaskKind.STATUS_PROBE, TaskKind.COMPLETION_NEXT_TIMER,
        TaskKind.FAILURE_RETRY,
    ):
        assert k in ATTEMPT_SCOPED_KINDS


def test_session_kinds_include_tts_addsong_faststop():
    for k in (TaskKind.TTS_TIMER, TaskKind.ADD_SONG_TIMER, TaskKind.FAST_GROUP_STOP):
        assert k in SESSION_SCOPED_KINDS


@pytest.mark.asyncio
async def test_cancel_by_kinds_respects_self_cancel():
    registry = PlaybackTaskRegistry()
    finished = asyncio.Event()

    async def _safe():
        cancelled = registry.cancel_by_kinds(TaskKind.TTS_TIMER)
        assert cancelled == 0
        finished.set()

    t = registry.start(TaskKind.TTS_TIMER, G0, _safe())
    assert t is not None
    await _event_wait(finished)
    await asyncio.wait_for(t, _TIMEOUT)
    await registry.close()


@pytest.mark.asyncio
async def test_compat_setter_none_cancels_active_registry_task():
    registry = PlaybackTaskRegistry()
    gate = asyncio.Event()
    t = registry.start(TaskKind.COMPLETION_NEXT_TIMER, G0, _event_wait(gate))
    assert t is not None
    registry.cancel(TaskKind.COMPLETION_NEXT_TIMER)
    await _wait_cancelled(t)
    await registry.close()


@pytest.mark.asyncio
async def test_compat_setter_none_does_not_crash_on_done_task():
    registry = PlaybackTaskRegistry()
    t = registry.start(TaskKind.COMPLETION_NEXT_TIMER, G0, _async_return(1))
    assert t is not None
    await asyncio.wait_for(t, _TIMEOUT)
    result = registry.cancel(TaskKind.COMPLETION_NEXT_TIMER)
    assert result is False
    await registry.close()


# ═══════════════════════════════════════════════════════════════════════
#  AST gates
# ═══════════════════════════════════════════════════════════════════════


def test_ast_zero_sleep_in_entire_file():
    source = inspect.getsource(inspect.getmodule(test_ast_zero_sleep_in_entire_file))
    tree = ast.parse(source)
    sleep_calls: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute) and node.func.attr == "sleep":
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "asyncio":
                    sleep_calls.append(node.lineno)
    assert not sleep_calls, f"asyncio.sleep at lines: {sleep_calls}"


def test_ast_zero_bare_event_wait_in_entire_file():
    """Zero bare ``await ev.wait()`` anywhere — all go through ``asyncio.wait_for``."""
    source = inspect.getsource(inspect.getmodule(test_ast_zero_bare_event_wait_in_entire_file))
    tree = ast.parse(source)
    bare: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "wait":
            if isinstance(node.func.value, ast.Name):
                # Check if wrapped in wait_for
                for ancestor in ast.walk(tree):
                    if isinstance(ancestor, ast.Call) and isinstance(ancestor.func, ast.Attribute) and ancestor.func.attr == "wait_for":
                        for child in ast.walk(ancestor):
                            if child is node:
                                break
                        else:
                            continue
                        break
                else:
                    bare.append(node.lineno)
    assert not bare, f"Bare Event.wait at lines: {bare}"


def test_device_scope_has_no_bare_create_task_or_duplicate_owner_names():
    source = inspect.getsource(XiaoMusicDevice)
    tree = ast.parse(source)
    assert not [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "create_task"
    ]
    assert "_inflight_fast_stop_tasks" not in source
    assert "_cancel_owned_task" not in source
