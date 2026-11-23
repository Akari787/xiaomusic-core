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
    execute = d._execute_stop_intent(
        {"sid": d._play_session_id, "token": token, "arg1": "notts"}
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
    d._duration = 200.0
    d.is_playing = False
    monkeypatch.setattr("xiaomusic.device_player.time.time", lambda: 105.0)
    offset, duration = d.get_offset_duration()
    assert offset == 5.0
    assert duration == 200.0
