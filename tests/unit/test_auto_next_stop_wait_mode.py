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
        (True, "overlap", "overlap"),
        (True, "async", "overlap"),
        (True, "background", "overlap"),
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
async def test_execute_group_stop_overlap_returns_before_completion():
    d = XiaoMusicDevice.__new__(XiaoMusicDevice)
    d.log = logging.getLogger("stop-wait-overlap")
    d.config = types.SimpleNamespace(auto_next_stop_wait_mode="overlap", auto_next_stop_grace_ms=0)
    d._runtime_state = PlaybackRuntimeState()

    blocker = asyncio.Event()
    state = {"completed": False}

    async def _group_force_stop_xiaoai(*, fast=False):
        await blocker.wait()
        state["completed"] = True
        return [fast]

    d.group_force_stop_xiaoai = _group_force_stop_xiaoai

    task = await d._execute_group_stop(fast_stop=True, sid=2)

    assert task is not None
    assert task.done() is False
    assert state["completed"] is False
    assert d._playback_tasks.get_task(TaskKind.FAST_GROUP_STOP) is task

    blocker.set()
    await asyncio.wait_for(task, timeout=5.0)

    assert state["completed"] is True
    assert d._playback_tasks.get_task(TaskKind.FAST_GROUP_STOP) is None
    await d._playback_tasks.close()
