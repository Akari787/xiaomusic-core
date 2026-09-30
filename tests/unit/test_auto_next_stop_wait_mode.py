import asyncio
import logging
import sys
import types

import pytest

if "miservice" not in sys.modules:
    sys.modules["miservice"] = types.SimpleNamespace(miio_command=lambda *args, **kwargs: None)

if "opencc" not in sys.modules:
    class _OpenCC:
        def __init__(self, *_args, **_kwargs):
            pass

        def convert(self, text):
            return text

    sys.modules["opencc"] = types.SimpleNamespace(OpenCC=_OpenCC)

from xiaomusic.device_player import XiaoMusicDevice
from xiaomusic.playback.runtime_state import PlaybackRuntimeState
from xiaomusic.playback.task_registry import TaskKind


@pytest.mark.parametrize(
    ("fast_stop", "mode", "expected"),
    [
        (False, "sync", "sync"),
        (True, "sync", "sync"),
        (True, "overlap", "sync"),
        (True, "async", "sync"),
        (True, "background", "sync"),
        (True, "weird", "sync"),
    ],
)
def test_resolve_fast_stop_wait_mode(fast_stop, mode, expected):
    d = XiaoMusicDevice.__new__(XiaoMusicDevice)
    d.config = types.SimpleNamespace(auto_next_stop_wait_mode=mode)
    assert d._resolve_fast_stop_wait_mode(fast_stop=fast_stop) == expected


@pytest.mark.asyncio
async def test_execute_group_stop_sync_waits_for_completion():
    d = XiaoMusicDevice.__new__(XiaoMusicDevice)
    d.log = logging.getLogger("stop-wait-sync")
    d.config = types.SimpleNamespace(auto_next_stop_wait_mode="sync", auto_next_stop_grace_ms=0)
    d._inflight_fast_stop_tasks = set()
    d._runtime_state = PlaybackRuntimeState()

    state = {"completed": False}

    async def _group_force_stop_xiaoai(*, fast=False):
        await asyncio.sleep(0)
        state["completed"] = True
        return [fast]

    d.group_force_stop_xiaoai = _group_force_stop_xiaoai

    out = await d._execute_group_stop(fast_stop=True, sid=1)

    assert out is None
    assert state["completed"] is True
    assert d._inflight_fast_stop_tasks == set()


@pytest.mark.asyncio
async def test_execute_group_stop_legacy_overlap_waits_for_completion():
    d = XiaoMusicDevice.__new__(XiaoMusicDevice)
    d.log = logging.getLogger("stop-wait-overlap")
    d.config = types.SimpleNamespace(
        auto_next_stop_wait_mode="overlap", auto_next_stop_timeout_ms=1000
    )
    d._runtime_state = PlaybackRuntimeState()

    blocker = asyncio.Event()
    state = {"completed": False}

    async def _group_force_stop_xiaoai(*, fast=False):
        await blocker.wait()
        state["completed"] = True
        return [fast]

    d.group_force_stop_xiaoai = _group_force_stop_xiaoai

    stop_task = asyncio.create_task(
        d._execute_group_stop(fast_stop=True, sid=2)
    )
    await asyncio.sleep(0)
    assert stop_task.done() is False
    assert state["completed"] is False
    barrier = d.get_group_stop_barrier_status()
    assert barrier["state"] == "pending"
    assert barrier["locked"] is True
    assert barrier["degraded"] is False
    blocker.set()
    assert await asyncio.wait_for(stop_task, timeout=5.0) is None
    assert state["completed"] is True
    assert d.get_group_stop_barrier_status() == {
        "state": "clear",
        "degraded": False,
        "locked": False,
        "pending": False,
        "task_done": False,
        "requires_physical_confirmation": False,
    }


@pytest.mark.asyncio
async def test_execute_group_stop_timeout_keeps_cooperative_child_and_barrier():
    d = XiaoMusicDevice.__new__(XiaoMusicDevice)
    d.log = logging.getLogger("stop-wait-timeout")
    d.config = types.SimpleNamespace(
        auto_next_stop_wait_mode="overlap", auto_next_stop_timeout_ms=10
    )
    d._runtime_state = PlaybackRuntimeState()
    release = asyncio.Event()
    child_started = asyncio.Event()

    async def _cooperative_slow_stop(*, fast=False):
        child_started.set()
        await release.wait()
        return [fast]

    d.group_force_stop_xiaoai = _cooperative_slow_stop

    stop_call = asyncio.create_task(d._execute_group_stop(fast_stop=True, sid=3))
    await asyncio.wait_for(child_started.wait(), timeout=5.0)
    with pytest.raises(asyncio.TimeoutError):
        await stop_call

    unresolved = d._unresolved_group_stop_task
    assert unresolved is not None
    assert unresolved.done() is False
    assert d.get_group_stop_barrier_status()["state"] == "timeout"

    # A later command remains blocked by the unresolved old stop; it cannot
    # dispatch a new play while that stop may still reach the device.
    with pytest.raises(asyncio.TimeoutError):
        await d._execute_group_stop(fast_stop=True, sid=4)

    assert d.confirm_group_stop_resolved(physical_stopped=False) is False
    assert d.get_group_stop_barrier_status()["locked"] is True
    assert d.confirm_group_stop_resolved(physical_stopped=True) is True
    assert d.get_group_stop_barrier_status()["state"] == "clear"
    release.set()
    await asyncio.wait_for(unresolved, timeout=5.0)
    await asyncio.sleep(0)
    assert getattr(d, "_unresolved_group_stop_task", None) is None
    assert d.get_group_stop_barrier_status()["state"] == "clear"


@pytest.mark.asyncio
async def test_nonpositive_stop_timeout_uses_safe_default_not_one_millisecond():
    d = XiaoMusicDevice.__new__(XiaoMusicDevice)
    d.log = logging.getLogger("stop-timeout-default")
    d.config = types.SimpleNamespace(
        auto_next_stop_wait_mode="sync", auto_next_stop_timeout_ms=0
    )
    d._runtime_state = PlaybackRuntimeState()
    completed = asyncio.Event()

    async def _slow_stop(*, fast=False):
        await asyncio.sleep(0.02)
        completed.set()
        return [True]

    d.group_force_stop_xiaoai = _slow_stop
    assert await d._execute_group_stop(fast_stop=True, sid=5) is None
    assert completed.is_set()
