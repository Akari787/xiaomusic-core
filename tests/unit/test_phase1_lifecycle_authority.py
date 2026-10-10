import asyncio
import types

import pytest

from xiaomusic.device_player import XiaoMusicDevice
from xiaomusic.playback.runtime_state import (
    PlaybackPhase,
    PlaybackRuntimeState,
    TrackReference,
    begin_confirm,
    begin_play_dispatch,
    begin_play_request,
)


def _device():
    d = XiaoMusicDevice.__new__(XiaoMusicDevice)
    events = []
    d.event_bus = types.SimpleNamespace(
        publish=lambda event, **payload: events.append((event, payload))
    )
    d._phase1_events = events
    d.device = types.SimpleNamespace(did="did-test", cur_music="", current_display_name="")
    d._runtime_state = PlaybackRuntimeState()
    d._play_session_id = 1
    d._last_volume = 0
    d._duration = 0
    d.is_playing = False
    d._start_time = 0
    d._paused_time = 0
    d._paused_position = 0
    d.log = types.SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None)
    d._manual_nav_generation = 0
    d._manual_nav_target = None
    d._submitted_intents = []
    def _submit(kind, payload=None):
        d._submitted_intents.append((kind, payload))
        return types.SimpleNamespace(accepted=True, sequence=len(d._submitted_intents))
    d._command_arbiter = types.SimpleNamespace(
        active_kind=None,
        pending_kind=None,
        after_barrier_kind=None,
        submit=_submit,
    )
    return d


async def _async_noop():
    return None


def _to_confirming(d):
    track = TrackReference(display_name="song")
    d._set_runtime_state(begin_play_request(d.get_runtime_state(), desired_track=track, updated_at=1))
    d._set_runtime_state(begin_play_dispatch(d.get_runtime_state(), updated_at=2))
    d._set_runtime_state(begin_confirm(d.get_runtime_state(), updated_at=3))


def test_runtime_semantic_transitions_publish_once_and_counter_only_does_not():
    d = _device()
    _to_confirming(d)
    before = len(d._phase1_events)
    d._accept_command(updated_at=4)
    assert len(d._phase1_events) == before
    d._confirm_runtime_playing(updated_at=5)
    assert len(d._phase1_events) == before + 1


@pytest.mark.asyncio
async def test_repeated_pause_is_idempotent_without_session_or_physical_submit():
    d = _device()
    d._runtime_state = PlaybackRuntimeState(phase=PlaybackPhase.PLAYING)
    d._start_time = 1
    d._duration = 100
    d.is_playing = True
    d._get_or_create_arbiter = lambda: d._command_arbiter
    assert await d.pause() is True
    assert d._play_session_id == 1
    assert d.get_runtime_state().phase == PlaybackPhase.PAUSED
    submitted = len(d._submitted_intents)
    assert await d.pause() is True
    assert d._play_session_id == 1
    assert len(d._submitted_intents) == submitted


@pytest.mark.asyncio
async def test_resume_idempotence_and_paused_submission_contract():
    d = _device()
    d._get_or_create_arbiter = lambda: d._command_arbiter
    d._runtime_state = PlaybackRuntimeState(phase=PlaybackPhase.PLAYING)
    assert await d.resume() is True
    assert d._submitted_intents == []

    d._runtime_state = PlaybackRuntimeState(phase=PlaybackPhase.PAUSED)
    d._play = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("user resume must not replay")
    )
    assert await d.resume() is True
    assert len(d._submitted_intents) == 1
    from xiaomusic.playback.command_arbiter import IntentKind
    assert d._submitted_intents[0][0] == IntentKind.RESUME

    d._runtime_state = PlaybackRuntimeState()
    assert await d.resume() is False


@pytest.mark.asyncio
async def test_resume_pending_is_idempotent_without_command_bump_or_submit():
    d = _device()
    from xiaomusic.playback.command_arbiter import IntentKind

    d._runtime_state = PlaybackRuntimeState(phase=PlaybackPhase.PAUSED)
    d._command_arbiter.pending_kind = IntentKind.RESUME
    before = d.get_runtime_state().command_generation
    assert await d.resume() is True
    assert d.get_runtime_state().command_generation == before
    assert d._submitted_intents == []


@pytest.mark.asyncio
async def test_resume_active_toggle_is_idempotent_and_commits_once():
    d = _device()
    from xiaomusic.playback.command_arbiter import DeviceCommandArbiter

    d._runtime_state = PlaybackRuntimeState(phase=PlaybackPhase.PAUSED)
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def _resume_physical():
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return [True]

    async def _timeout(*args, **kwargs):
        raise AssertionError("resume must not schedule a zero-duration timer")

    d.group_resume_xiaoai = _resume_physical
    d.set_next_music_timeout = _timeout
    arbiter = DeviceCommandArbiter(d._arbiter_executor)
    d._command_arbiter = arbiter
    d._get_or_create_arbiter = lambda: arbiter
    sid = d._play_session_id
    try:
        assert await d.resume() is True
        await entered.wait()
        before = d.get_runtime_state().command_generation
        assert arbiter.active_kind is not None
        assert await d.resume() is True
        assert d.get_runtime_state().command_generation == before
        release.set()
        for _ in range(100):
            if d.get_runtime_state().phase == PlaybackPhase.PLAYING:
                break
            await asyncio.sleep(0)
        assert calls == 1
        assert d.get_runtime_state().phase == PlaybackPhase.PLAYING
        assert d._play_session_id == sid
    finally:
        release.set()
        await arbiter.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [[], [False]])
async def test_pause_physical_failure_enters_visible_failed(result):
    d = _device()
    d._runtime_state = PlaybackRuntimeState(phase=PlaybackPhase.PAUSED)
    d.cancel_group_next_timer = _async_noop

    async def _pause_result():
        return result

    d.group_pause_xiaoai = _pause_result
    token = d._capture_lifecycle_token()

    await d._execute_pause_intent(
        {"sid": d._play_session_id, "token": token}
    )
    assert d.get_runtime_state().phase == PlaybackPhase.FAILED
    assert d.get_runtime_state().failure.reason == "pause_not_confirmed"


@pytest.mark.asyncio
async def test_pause_physical_exception_enters_visible_failed():
    d = _device()
    d._runtime_state = PlaybackRuntimeState(phase=PlaybackPhase.PAUSED)
    d.cancel_group_next_timer = _async_noop

    async def _raise_pause():
        raise RuntimeError("pause io")

    d.group_pause_xiaoai = _raise_pause
    token = d._capture_lifecycle_token()
    with pytest.raises(RuntimeError, match="pause io"):
        await d._execute_pause_intent({"sid": d._play_session_id, "token": token})
    assert d.get_runtime_state().phase == PlaybackPhase.FAILED
    assert d.get_runtime_state().failure.reason == "pause_not_confirmed"


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [[], [False]])
async def test_resume_physical_failure_enters_visible_failed(result):
    d = _device()
    d._runtime_state = PlaybackRuntimeState(phase=PlaybackPhase.PAUSED)
    async def _resume_result():
        return result

    d.group_resume_xiaoai = _resume_result
    token = d._capture_lifecycle_token()
    await d._execute_resume_intent(
        types.SimpleNamespace(
            sequence=1,
            payload={"sid": d._play_session_id, "accepted_token": token},
        )
    )
    assert d.get_runtime_state().phase == PlaybackPhase.FAILED
    assert d.get_runtime_state().failure.reason == "resume_not_confirmed"


@pytest.mark.asyncio
async def test_resume_failure_after_command_bump_is_strictly_ignored():
    d = _device()
    d._runtime_state = PlaybackRuntimeState(phase=PlaybackPhase.PAUSED)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def _blocked_failure():
        entered.set()
        await release.wait()
        return [False]

    d.group_resume_xiaoai = _blocked_failure
    token = d._capture_lifecycle_token()
    task = asyncio.create_task(
        d._execute_resume_intent(
            types.SimpleNamespace(
                sequence=1,
                payload={"sid": d._play_session_id, "accepted_token": token},
            )
        )
    )
    await entered.wait()
    d._accept_command(updated_at=2)
    release.set()
    await task
    assert d.get_runtime_state().phase == PlaybackPhase.PAUSED
    assert d.get_runtime_state().failure is None


@pytest.mark.asyncio
async def test_resume_post_io_stale_does_not_commit_playing_or_timer():
    d = _device()
    d._runtime_state = PlaybackRuntimeState(phase=PlaybackPhase.PAUSED)
    entered = __import__("asyncio").Event()
    release = __import__("asyncio").Event()

    async def _blocked_resume():
        entered.set()
        await release.wait()
        return [True]

    d.group_resume_xiaoai = _blocked_resume
    d.set_next_music_timeout = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("stale resume must not create timer")
    )
    token = d._capture_lifecycle_token()
    task = __import__("asyncio").create_task(
        d._execute_resume_intent(
            types.SimpleNamespace(
                sequence=1,
                payload={"sid": d._play_session_id, "accepted_token": token},
            )
        )
    )
    await entered.wait()
    d._accept_command(updated_at=2)
    release.set()
    await task
    assert d.get_runtime_state().phase == PlaybackPhase.PAUSED
    assert d.is_playing is False


@pytest.mark.asyncio
async def test_stop_empty_group_is_a_successful_noop():
    d = _device()
    d._runtime_state = PlaybackRuntimeState(phase=PlaybackPhase.STOPPING)
    d.cancel_group_next_timer = _async_noop

    async def _empty_stop_group():
        return []

    d.group_force_stop_xiaoai = _empty_stop_group
    token = d._capture_lifecycle_token()
    await d._execute_stop_intent({"sid": d._play_session_id, "token": token, "arg1": "notts"})
    assert d.get_runtime_state().phase == PlaybackPhase.STOPPED
    assert d.get_runtime_state().failure is None


@pytest.mark.asyncio
async def test_non_success_stop_reports_runtime_failure_once():
    d = _device()
    d._runtime_state = PlaybackRuntimeState(phase=PlaybackPhase.STOPPING)
    d.cancel_group_next_timer = _async_noop

    async def _failed_stop_group():
        return [False]

    d.group_force_stop_xiaoai = _failed_stop_group
    token = d._capture_lifecycle_token()
    with pytest.raises(RuntimeError, match="group stop returned"):
        await d._execute_stop_intent(
            {"sid": d._play_session_id, "token": token, "arg1": "notts"}
        )

    failure = d.get_runtime_state().failure
    assert d.get_runtime_state().phase == PlaybackPhase.FAILED
    assert failure is not None
    assert failure.reason == "stop_not_confirmed"
    assert failure.count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("phase, method_name, reason", [
    (PlaybackPhase.STOPPING, "group_force_stop_xiaoai", "stop_not_confirmed"),
    (PlaybackPhase.PAUSED, "group_pause_xiaoai", "pause_not_confirmed"),
])
async def test_barrier_failure_after_command_bump_is_visible(
    phase, method_name, reason
):
    d = _device()
    d._runtime_state = PlaybackRuntimeState(phase=phase)
    d.cancel_group_next_timer = _async_noop
    entered = asyncio.Event()
    release = asyncio.Event()

    async def _failing_physical():
        entered.set()
        await release.wait()
        raise RuntimeError("barrier io")

    setattr(d, method_name, _failing_physical)
    token = d._capture_lifecycle_token()
    execute = (
        d._execute_stop_intent({"sid": d._play_session_id, "token": token, "arg1": "notts"})
        if phase == PlaybackPhase.STOPPING
        else d._execute_pause_intent({"sid": d._play_session_id, "token": token})
    )
    task = asyncio.create_task(execute)
    await entered.wait()
    d._accept_command(updated_at=2)
    release.set()
    with pytest.raises(RuntimeError, match="barrier io"):
        await task
    assert d.get_runtime_state().phase == PlaybackPhase.FAILED
    assert d.get_runtime_state().failure.reason == reason


@pytest.mark.parametrize(
    "result, expected",
    [
        ({"code": 0, "message": "ok", "data": {"code": 0}}, True),
        ({"code": 1, "message": "failed", "data": {"code": 1}}, False),
        ({"message": "unknown"}, False),
        (True, True),
        (False, False),
        ([True], True),
        ([], False),
    ],
)
def test_physical_result_success_contract(result, expected):
    assert XiaoMusicDevice._physical_result_succeeded(result) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("resume_success", [True, False])
async def test_active_resume_then_pause_converges_each_device(resume_success):
    d = _device()
    d._runtime_state = PlaybackRuntimeState(phase=PlaybackPhase.PAUSED)
    states = {"a": False, "b": True}
    toggles = []
    resume_entered = asyncio.Event()
    resume_release = asyncio.Event()

    async def _mina_call(method, device_id, *, retry=1, ctx=""):
        _ = retry
        if method == "player_get_status":
            info = f'{{"status": {1 if states[device_id] else 0}}}'
            return {"data": {"info": info}}
        if ctx == "resume_xiaoai" and device_id == "a":
            resume_entered.set()
            await resume_release.wait()
            if not resume_success:
                return {"code": 1, "data": {"code": 1}}
        toggles.append((ctx, device_id))
        states[device_id] = not states[device_id]
        return {"code": 0, "data": {"code": 0}}

    d.auth_manager = types.SimpleNamespace(mina_call=_mina_call)
    d.group_name = "g"
    d.xiaomusic = types.SimpleNamespace(
        device_manager=types.SimpleNamespace(
            get_group_device_id_list=lambda group: ["a", "b"]
        )
    )
    from xiaomusic.playback.command_arbiter import DeviceCommandArbiter
    arbiter = DeviceCommandArbiter(d._arbiter_executor)
    d._command_arbiter = arbiter
    d._get_or_create_arbiter = lambda: arbiter
    d.cancel_group_next_timer = _async_noop
    try:
        assert await d.resume() is True
        for _ in range(100):
            if resume_entered.is_set():
                break
            await asyncio.sleep(0)
        assert resume_entered.is_set(), (arbiter.last_error, arbiter.active_kind)
        assert await d.pause() is True
        resume_release.set()
        for _ in range(100):
            if arbiter.active_kind is None and arbiter.pending_kind is None:
                break
            await asyncio.sleep(0)
        assert states == {"a": False, "b": False}
        assert d.get_runtime_state().phase == PlaybackPhase.PAUSED
        assert [item for item in toggles if item[0] == "resume_xiaoai"] == (
            [("resume_xiaoai", "a")] if resume_success else []
        )
        assert [item for item in toggles if item[0] == "pause_xiaoai"] == (
            [("pause_xiaoai", "a"), ("pause_xiaoai", "b")]
            if resume_success
            else [("pause_xiaoai", "b")]
        )
    finally:
        resume_release.set()
        await arbiter.close()


@pytest.mark.asyncio
async def test_pause_and_resume_ensure_state_skip_or_toggle_once():
    d = _device()
    states = {"paused": False, "playing": True}
    calls = []

    async def _probe(device_id):
        return states[device_id]

    async def _mina_call(method, device_id, *, retry=1, ctx=""):
        _ = method, retry
        calls.append((ctx, device_id))
        states[device_id] = not states[device_id]
        return {"code": 0, "data": {"code": 0}}

    d.get_if_xiaoai_is_playing = _probe
    d.auth_manager = types.SimpleNamespace(mina_call=_mina_call)
    assert await d.pause_xiaoai("paused") is True
    assert await d.pause_xiaoai("playing") is True
    states["playing"] = True
    assert await d.resume_xiaoai("playing") is True
    assert await d.resume_xiaoai("paused") is True
    assert calls == [("pause_xiaoai", "playing"), ("resume_xiaoai", "paused")]


@pytest.mark.asyncio
async def test_ensure_state_probe_exception_propagates():
    d = _device()

    async def _probe(_device_id):
        raise RuntimeError("probe failed")

    d.get_if_xiaoai_is_playing = _probe
    with pytest.raises(RuntimeError, match="probe failed"):
        await d.pause_xiaoai("did-test")
    with pytest.raises(RuntimeError, match="probe failed"):
        await d.resume_xiaoai("did-test")


@pytest.mark.asyncio
async def test_set_volume_false_preserves_value_and_emits_no_event():
    d = _device()
    d._last_volume = 10
    d.device_id = "did-test"
    d.auth_manager = types.SimpleNamespace(
        mina_call=lambda *a, **k: __import__("asyncio").sleep(0, result=False)
    )
    assert await d.set_volume(20) is False
    assert d._last_volume == 10
    assert d._phase1_events == []


@pytest.mark.asyncio
async def test_resume_exception_enters_visible_failed():
    d = _device()
    d._runtime_state = PlaybackRuntimeState(phase=PlaybackPhase.PAUSED)

    async def _raise_resume():
        raise RuntimeError("resume io")

    d.group_resume_xiaoai = _raise_resume
    token = d._capture_lifecycle_token()
    with pytest.raises(RuntimeError, match="resume io"):
        await d._execute_resume_intent(
            types.SimpleNamespace(
                sequence=1,
                payload={"sid": d._play_session_id, "accepted_token": token},
            )
        )
    assert d.get_runtime_state().phase == PlaybackPhase.FAILED
    assert d.get_runtime_state().failure.reason == "resume_not_confirmed"


@pytest.mark.asyncio
async def test_stop_exception_enters_visible_failed():
    d = _device()
    d._runtime_state = PlaybackRuntimeState(phase=PlaybackPhase.STOPPING)

    async def _raise_stop():
        raise RuntimeError("stop io")

    d.cancel_group_next_timer = _async_noop
    d.group_force_stop_xiaoai = _raise_stop
    token = d._capture_lifecycle_token()
    with pytest.raises(RuntimeError, match="stop io"):
        await d._execute_stop_intent(
            {"sid": d._play_session_id, "token": token, "arg1": "notts"}
        )
    assert d.get_runtime_state().phase == PlaybackPhase.FAILED
    assert d.get_runtime_state().failure.reason == "stop_not_confirmed"


@pytest.mark.asyncio
async def test_volume_reads_do_not_commit_cache_but_refresh_does():
    d = _device()
    d.device_id = "did-test"
    d._last_volume = 10
    d.auth_manager = types.SimpleNamespace(
        mina_call=lambda *a, **k: __import__("asyncio").sleep(
            0, result={"data": {"info": "{}"}}
        )
    )
    assert await d.get_volume() == 10
    assert d._last_volume == 10
    assert await d.get_player_status() == {}
    assert d._last_volume == 10
    assert d._phase1_events == []

    async def _refresh_success():
        return 20

    d.get_volume = _refresh_success
    assert await d._refresh_runtime_volume(context="test") == 20
    assert d._last_volume == 20
    assert len(d._phase1_events) == 1

    async def _refresh_failure():
        raise RuntimeError("volume read failed")

    d.get_volume = _refresh_failure
    assert await d._refresh_runtime_volume(context="test") == 20
    assert d._last_volume == 20
    assert len(d._phase1_events) == 1


@pytest.mark.asyncio
async def test_player_status_error_is_unknown_not_stopped():
    d = _device()
    d.device_id = "did-test"

    async def _status_error(*args, **kwargs):
        raise RuntimeError("认证不可用")

    d.auth_manager = types.SimpleNamespace(mina_call=_status_error)
    status = await d.get_player_status()
    assert status["status"] is None
    assert status["volume"] is None
    assert status["probe_ok"] is False
    assert "认证不可用" in status["probe_error"]


def test_runtime_playing_position_is_authoritative_over_legacy_flag(monkeypatch):
    d = _device()
    d._runtime_state = PlaybackRuntimeState(phase=PlaybackPhase.PLAYING)
    d._start_time = 100.0
    d._paused_time = 0.0
    d._duration = 200.0
    d.is_playing = False
    monkeypatch.setattr("xiaomusic.device_player.time.time", lambda: 105.0)
    offset, duration = d.get_offset_duration()
    assert offset == 5.0
    assert duration == 200.0
    import asyncio
    assert asyncio.run(d.pause()) is True
    assert d.get_offset_duration() == (5.0, 200.0)
