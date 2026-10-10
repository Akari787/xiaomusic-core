"""Exact-terminal preplay stop regression tests.

All coordination uses events; no real timeout is involved.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from pathlib import Path

import pytest

from xiaomusic.device_player import XiaoMusicDevice
from xiaomusic.playback.command_arbiter import DeviceCommandArbiter, IntentKind
from xiaomusic.playback.runtime_state import PlaybackRuntimeState


def _device(stop):
    d = XiaoMusicDevice.__new__(XiaoMusicDevice)
    d.log = logging.getLogger("exact-terminal-stop")
    d._runtime_state = PlaybackRuntimeState()
    d.group_force_stop_xiaoai = stop
    d._execute_group_stop = XiaoMusicDevice._execute_group_stop.__get__(d)
    return d


async def _run_flow(d, *, url="local", external=False):
    dispatches = []
    stop_done = asyncio.Event()

    async def executor(intent):
        if intent.kind in (IntentKind.PLAY, IntentKind.AUTO_NEXT):
            token = intent.payload["token"]
            await d._execute_group_stop(fast_stop=True, sid=1)
            if d._is_lifecycle_token_stale(token):
                return
            dispatches.append((intent.kind, url, external))
            stop_done.set()
        elif intent.kind is IntentKind.STOP:
            await d._execute_group_stop(fast_stop=False, sid=1)
            stop_done.set()

    d._is_lifecycle_token_stale = lambda token: (
        d._runtime_state.command_generation != token
    )
    arbiter = DeviceCommandArbiter(executor)
    d._command_arbiter = arbiter
    return arbiter, dispatches, stop_done


def _accept(d):
    d._accept_command(updated_at=1.0)
    return d.get_runtime_state().command_generation


@pytest.mark.asyncio
async def test_A_slow_stop_has_no_play_until_exact_success_then_dispatches_once():
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_stop(*, fast=False):
        entered.set()
        await release.wait()
        return [True]

    d = _device(slow_stop)
    arb, dispatches, done = await _run_flow(d)
    try:
        token = _accept(d)
        arb.submit(IntentKind.AUTO_NEXT, {"token": token})
        await asyncio.wait_for(entered.wait(), timeout=1)
        assert dispatches == []
        release.set()
        await asyncio.wait_for(done.wait(), timeout=1)
        assert len(dispatches) == 1
    finally:
        release.set()
        await arb.close()


@pytest.mark.asyncio
async def test_B_stop_arriving_during_preplay_stop_prevents_old_play():
    entered = asyncio.Event()
    release = asyncio.Event()
    stop_calls = []

    async def slow_stop(*, fast=False):
        stop_calls.append(fast)
        entered.set()
        await release.wait()
        return [True]

    d = _device(slow_stop)
    arb, dispatches, stop_done = await _run_flow(d)
    try:
        token = _accept(d)
        arb.submit(IntentKind.PLAY, {"token": token})
        await asyncio.wait_for(entered.wait(), timeout=1)
        _accept(d)
        arb.submit(IntentKind.STOP, {"token": d.get_runtime_state().command_generation})
        release.set()
        await asyncio.wait_for(stop_done.wait(), timeout=1)
    finally:
        release.set()
        await arb.close()
    assert dispatches == []
    assert stop_calls == [True, False]


@pytest.mark.asyncio
async def test_C_new_play_replaces_old_and_only_new_dispatches():
    entered = asyncio.Event()
    release = asyncio.Event()
    stop_count = 0

    async def slow_stop(*, fast=False):
        nonlocal stop_count
        stop_count += 1
        if stop_count == 1:
            entered.set()
            await release.wait()
        return [True]

    d = _device(slow_stop)
    arb, dispatches, done = await _run_flow(d, url="B")
    try:
        token_a = _accept(d)
        arb.submit(IntentKind.PLAY, {"token": token_a})
        await asyncio.wait_for(entered.wait(), timeout=1)
        token_b = _accept(d)
        arb.submit(IntentKind.PLAY, {"token": token_b})
        release.set()
        await asyncio.wait_for(done.wait(), timeout=1)
    finally:
        release.set()
        await arb.close()
    assert dispatches == [(IntentKind.PLAY, "B", False)]
    assert stop_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("raises", [False, True])
async def test_D_failed_or_raised_stop_is_observable_and_blocks_dispatch(raises):
    async def bad_stop(*, fast=False):
        if raises:
            raise OSError("transport down")
        return [False]

    d = _device(bad_stop)
    arb, dispatches, _ = await _run_flow(d)
    try:
        token = _accept(d)
        arb.submit(IntentKind.PLAY, {"token": token})
        for _ in range(20):
            if arb.last_error is not None:
                break
            await asyncio.sleep(0)
    finally:
        await arb.close()
    assert dispatches == []


@pytest.mark.asyncio
async def test_E_external_url_uses_exact_stop_and_stale_token_fence():
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_stop(*, fast=False):
        entered.set()
        await release.wait()
        return [True]

    d = _device(slow_stop)
    arb, dispatches, _ = await _run_flow(d, url="https://example.test/a.mp3", external=True)
    try:
        token = _accept(d)
        arb.submit(IntentKind.PLAY, {"token": token})
        await asyncio.wait_for(entered.wait(), timeout=1)
        _accept(d)
        release.set()
        await asyncio.sleep(0)
    finally:
        release.set()
        await arb.close()
    assert dispatches == []


@pytest.mark.asyncio
async def test_stop_cancellation_propagates_without_becoming_success():
    entered = asyncio.Event()

    async def hanging_stop(*, fast=False):
        entered.set()
        await asyncio.Event().wait()
        return [True]

    d = _device(hanging_stop)
    task = asyncio.create_task(d._execute_group_stop(fast_stop=True, sid=1))
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_F_no_formal_old_abort_or_barrier_path_remains():
    roots = [Path("xiaomusic"), Path("docs")]
    forbidden = (
        "PREPLAY_GROUP_STOP",
        "FAST_GROUP_STOP",
        "fast_group_stop",
        "_unresolved_group_stop_task",
        "_group_stop_barrier_state",
        "_group_stop_barrier_degraded",
        "_explicit_stop_reconcile_candidate",
        "get_group_stop_barrier_status",
        "confirm_group_stop_resolved",
        "_resolve_fast_stop_wait_mode",
        "force_sync",
        "auto_next_stop_wait_mode",
        "auto_next_stop_timeout_ms",
        "auto_next_stop_grace_ms",
    )
    for root in roots:
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert not any(item in text for item in forbidden), path


def test_group_stop_has_no_timeout_or_detached_task():
    source = inspect.getsource(XiaoMusicDevice._execute_group_stop)
    assert "asyncio.wait" not in source
    assert "create_task" not in source
    assert "timeout" not in source.lower()
