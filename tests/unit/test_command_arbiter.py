"""Tests for DeviceCommandArbiter (T04-A).

Deterministic coordination exclusively via asyncio.Event + wait_for.
Zero asyncio.sleep(0).  Zero background task leaks.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from xiaomusic.playback.command_arbiter import (
    ArbiterClosedError,
    DeviceCommandArbiter,
    IntentKind,
    IntentReceipt,
    PlaybackIntent,
)


async def _noop(_intent: PlaybackIntent) -> None:
    pass


# ── basic smoke ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_all_eight_kinds_submit_and_return_receipt():
    """Every IntentKind can be submitted and returns an IntentReceipt."""
    arb = DeviceCommandArbiter(_noop)
    try:
        receipts: list[IntentReceipt] = []
        for kind in IntentKind:
            rec = arb.submit(kind)
            assert isinstance(rec, IntentReceipt)
            assert rec.accepted is True
            receipts.append(rec)

        assert len(receipts) == 8
        for i in range(1, len(receipts)):
            assert receipts[i].sequence > receipts[i - 1].sequence
    finally:
        await arb.close()


@pytest.mark.asyncio
async def test_sequence_starts_at_one_and_monotonic():
    arb = DeviceCommandArbiter(_noop)
    try:
        r1 = arb.submit(IntentKind.PLAY)
        r2 = arb.submit(IntentKind.NEXT)
        r3 = arb.submit(IntentKind.STOP)
        assert r1.sequence == 1
        assert r2.sequence == 2
        assert r3.sequence == 3
    finally:
        await arb.close()


# ── fast return ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_submit_returns_immediately_while_executor_blocked():
    """submit() must not await the executor — it returns instantly."""
    started = asyncio.Event()
    block = asyncio.Event()

    async def _exec(intent: PlaybackIntent) -> None:
        started.set()
        await asyncio.wait_for(block.wait(), timeout=2.0)

    arb = DeviceCommandArbiter(_exec)
    try:
        arb.submit(IntentKind.PLAY, {"track": "A"})
        await asyncio.wait_for(started.wait(), timeout=2.0)
        assert arb.active_sequence == 1

        rec = arb.submit(IntentKind.NEXT, {"track": "B"})
        assert rec.accepted is True
        assert rec.sequence == 2
        assert arb.pending_sequence == 2
    finally:
        block.set()
        await arb.close()


# ── max concurrency = 1 ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_max_concurrency_is_one():
    """Only one executor call is in flight at any time."""
    started = asyncio.Event()
    block = asyncio.Event()

    async def _exec(intent: PlaybackIntent) -> None:
        started.set()
        await asyncio.wait_for(block.wait(), timeout=2.0)

    arb = DeviceCommandArbiter(_exec)
    try:
        arb.submit(IntentKind.PLAY, {"track": "A"})
        await asyncio.wait_for(started.wait(), timeout=2.0)
        assert arb.active_sequence == 1

        arb.submit(IntentKind.NEXT, {"track": "B"})
        # submit() is synchronous; active unchanged because worker is
        # still blocked inside the first executor call.
        assert arb.active_sequence == 1
    finally:
        block.set()
        await arb.close()


# ── latest-pending ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_latest_pending_only_last_survives():
    """During execution of A, B/C arrive → worker executes A then D."""
    call_count = 0
    a_started = asyncio.Event()
    block_a = asyncio.Event()
    d_started = asyncio.Event()
    block_d = asyncio.Event()
    received_d_payload: dict[str, Any] | None = None

    async def _exec(intent: PlaybackIntent) -> None:
        nonlocal call_count, received_d_payload
        call_count += 1
        if call_count == 1:  # A
            a_started.set()
            await asyncio.wait_for(block_a.wait(), timeout=2.0)
        else:  # D
            received_d_payload = intent.payload
            d_started.set()
            await asyncio.wait_for(block_d.wait(), timeout=2.0)

    arb = DeviceCommandArbiter(_exec)
    try:
        arb.submit(IntentKind.PLAY, {"track": "A"})
        await asyncio.wait_for(a_started.wait(), timeout=2.0)
        assert arb.active_sequence == 1

        arb.submit(IntentKind.PREVIOUS, {"track": "B"})
        arb.submit(IntentKind.AUTO_NEXT, {"track": "C"})
        arb.submit(IntentKind.NEXT, {"track": "D"})

        assert arb.pending_sequence == 4  # only D survives

        block_a.set()
        await asyncio.wait_for(d_started.wait(), timeout=2.0)

        assert arb.active_sequence == 4
        assert received_d_payload == {"track": "D"}
    finally:
        block_a.set()
        block_d.set()
        await arb.close()


@pytest.mark.asyncio
async def test_worker_executes_pending_after_active_completes():
    """When active completes, the pending intent is executed next."""
    calls: list[int] = []
    started_1 = asyncio.Event()
    block_1 = asyncio.Event()
    started_2 = asyncio.Event()
    block_2 = asyncio.Event()

    async def _exec(intent: PlaybackIntent) -> None:
        calls.append(intent.sequence)
        if intent.sequence == 1:
            started_1.set()
            await asyncio.wait_for(block_1.wait(), timeout=2.0)
        else:
            started_2.set()
            await asyncio.wait_for(block_2.wait(), timeout=2.0)

    arb = DeviceCommandArbiter(_exec)
    try:
        arb.submit(IntentKind.PLAY, {"n": 1})
        await asyncio.wait_for(started_1.wait(), timeout=2.0)
        assert arb.active_sequence == 1

        arb.submit(IntentKind.NEXT, {"n": 2})
        assert arb.pending_sequence == 2

        block_1.set()
        await asyncio.wait_for(started_2.wait(), timeout=2.0)

        assert calls == [1, 2]
    finally:
        block_1.set()
        block_2.set()
        await arb.close()


# ── exception recovery ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_executor_exception_does_not_kill_arbiter():
    """After an executor raises, the next intent is still processed."""
    calls: list[int] = []
    failed = asyncio.Event()
    succeeded = asyncio.Event()

    async def _exec(intent: PlaybackIntent) -> None:
        calls.append(intent.sequence)
        if intent.sequence == 1:
            failed.set()
            raise RuntimeError("boom")
        succeeded.set()

    arb = DeviceCommandArbiter(_exec)
    try:
        arb.submit(IntentKind.PLAY)
        await asyncio.wait_for(failed.wait(), timeout=2.0)

        assert arb.last_error is not None
        assert isinstance(arb.last_error, RuntimeError)
        assert str(arb.last_error) == "boom"
        assert arb.active_sequence is None

        arb.submit(IntentKind.STOP)
        await asyncio.wait_for(succeeded.wait(), timeout=2.0)

        assert calls == [1, 2]
    finally:
        await arb.close()


@pytest.mark.asyncio
async def test_last_error_persists_after_successful_intent():
    """last_error retains the most recent error; success does not clear it."""
    failed = asyncio.Event()
    succeeded = asyncio.Event()

    async def _exec(intent: PlaybackIntent) -> None:
        if intent.sequence == 1:
            failed.set()
            raise ValueError("first fail")
        else:
            succeeded.set()

    arb = DeviceCommandArbiter(_exec)
    try:
        arb.submit(IntentKind.PLAY)
        await asyncio.wait_for(failed.wait(), timeout=2.0)
        assert isinstance(arb.last_error, ValueError)

        arb.submit(IntentKind.STOP)
        await asyncio.wait_for(succeeded.wait(), timeout=2.0)
        # error persists — successful execution does not clear it
        assert isinstance(arb.last_error, ValueError)
        assert arb.active_sequence is None
    finally:
        await arb.close()


# ── idle restart ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_idle_restart_after_completion():
    """After worker goes idle (no pending), a new submit wakes it again."""
    calls: list[int] = []
    done_1 = asyncio.Event()
    done_2 = asyncio.Event()

    async def _exec(intent: PlaybackIntent) -> None:
        calls.append(intent.sequence)
        if intent.sequence == 1:
            done_1.set()
        else:
            done_2.set()

    arb = DeviceCommandArbiter(_exec)
    try:
        arb.submit(IntentKind.PLAY)
        await asyncio.wait_for(done_1.wait(), timeout=2.0)
        assert calls == [1]
        assert arb.active_sequence is None

        arb.submit(IntentKind.PAUSE)
        await asyncio.wait_for(done_2.wait(), timeout=2.0)
        assert calls == [1, 2]
        assert arb.active_sequence is None
    finally:
        await arb.close()


# ── two arbiter devices are independent ─────────────────────────────────


@pytest.mark.asyncio
async def test_two_arbiters_independent():
    """Two arbiters on different 'devices' do not interfere."""
    calls_a: list[int] = []
    calls_b: list[int] = []
    started_a1 = asyncio.Event()
    started_a2 = asyncio.Event()
    block_a = asyncio.Event()
    started_b1 = asyncio.Event()
    started_b2 = asyncio.Event()
    block_b = asyncio.Event()

    async def _exec_a(intent: PlaybackIntent) -> None:
        calls_a.append(intent.sequence)
        if len(calls_a) == 1:
            started_a1.set()
            await asyncio.wait_for(block_a.wait(), timeout=2.0)
        else:
            started_a2.set()

    async def _exec_b(intent: PlaybackIntent) -> None:
        calls_b.append(intent.sequence)
        if len(calls_b) == 1:
            started_b1.set()
            await asyncio.wait_for(block_b.wait(), timeout=2.0)
        else:
            started_b2.set()

    arb_a = DeviceCommandArbiter(_exec_a)
    arb_b = DeviceCommandArbiter(_exec_b)
    try:
        arb_a.submit(IntentKind.PLAY)
        await asyncio.wait_for(started_a1.wait(), timeout=2.0)
        assert calls_a == [1]

        arb_b.submit(IntentKind.NEXT)
        await asyncio.wait_for(started_b1.wait(), timeout=2.0)
        assert calls_b == [1]

        # submit while both blocked
        arb_a.submit(IntentKind.STOP)
        arb_b.submit(IntentKind.PAUSE)

        block_a.set()
        await asyncio.wait_for(started_a2.wait(), timeout=2.0)
        assert calls_a == [1, 2]

        block_b.set()
        await asyncio.wait_for(started_b2.wait(), timeout=2.0)
        assert calls_b == [1, 2]
    finally:
        block_a.set()
        block_b.set()
        await arb_a.close()
        await arb_b.close()


# ── close ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_close_cleans_worker_and_rejects_submit():
    """After close(), submit raises and worker task is done."""
    arb = DeviceCommandArbiter(_noop)
    await arb.close()

    assert arb.is_closed is True
    assert arb.active_sequence is None
    assert arb.pending_sequence is None
    assert arb._worker_task.done()  # noqa: SLF001

    with pytest.raises(ArbiterClosedError, match="arbiter is closed"):
        arb.submit(IntentKind.PLAY)


@pytest.mark.asyncio
async def test_close_idempotent():
    """Multiple close() calls are safe."""
    arb = DeviceCommandArbiter(_noop)
    await arb.close()
    await arb.close()
    assert arb.is_closed


@pytest.mark.asyncio
async def test_close_cancels_running_worker():
    """Close while executor is blocked cancels the worker task."""
    started = asyncio.Event()
    block = asyncio.Event()

    async def _exec(intent: PlaybackIntent) -> None:
        started.set()
        await asyncio.wait_for(block.wait(), timeout=2.0)

    arb = DeviceCommandArbiter(_exec)
    arb.submit(IntentKind.PLAY)
    await asyncio.wait_for(started.wait(), timeout=2.0)
    assert arb.active_sequence == 1

    await arb.close()
    assert arb.is_closed
    assert arb.active_sequence is None
    assert arb._worker_task.done()  # noqa: SLF001
    assert arb.pending_sequence is None


@pytest.mark.asyncio
async def test_close_with_active_and_pending_clears_both():
    """Close while executor has active + pending: both become None,
    pending never executes."""
    started = asyncio.Event()
    block = asyncio.Event()
    pending_executed = False

    async def _exec(intent: PlaybackIntent) -> None:
        nonlocal pending_executed
        if intent.sequence == 1:
            started.set()
            await asyncio.wait_for(block.wait(), timeout=2.0)
        else:
            pending_executed = True

    arb = DeviceCommandArbiter(_exec)
    arb.submit(IntentKind.PLAY)
    await asyncio.wait_for(started.wait(), timeout=2.0)
    assert arb.active_sequence == 1

    arb.submit(IntentKind.STOP)
    assert arb.pending_sequence == 2

    await arb.close()
    assert arb.is_closed
    assert arb.active_sequence is None
    assert arb.pending_sequence is None
    assert not pending_executed


# ── payload immutability ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_payload_mutation_after_submit_does_not_affect_intent():
    """Mutating the dict passed to submit() must not change the stored
    payload."""
    payload: dict[str, Any] = {"track": "original"}
    captured = asyncio.Event()
    executor_intent: PlaybackIntent | None = None

    async def _exec(intent: PlaybackIntent) -> None:
        nonlocal executor_intent
        executor_intent = intent
        captured.set()

    arb = DeviceCommandArbiter(_exec)
    try:
        arb.submit(IntentKind.PLAY, payload)
        payload["track"] = "mutated"
        payload["extra"] = "sneaked"
        await asyncio.wait_for(captured.wait(), timeout=2.0)

        assert executor_intent is not None
        assert executor_intent.payload == {"track": "original"}
        assert "extra" not in executor_intent.payload  # type: ignore[operator]
    finally:
        await arb.close()


# ═══════════════════════════════════════════════════════════════════════
# Barrier ordering tests (T04-C2a)
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_barrier_stop_prevents_regular_replacement():
    """active PLAY, submit STOP, submit PLAY2/PLAY3 → PLAY,STOP,PLAY3."""
    executed: list[int] = []
    play_started = asyncio.Event()
    block_play = asyncio.Event()
    stop_started = asyncio.Event()
    block_stop = asyncio.Event()
    play3_started = asyncio.Event()
    block_play3 = asyncio.Event()
    play3_done = asyncio.Event()

    async def _exec(intent: PlaybackIntent) -> None:
        executed.append(intent.sequence)
        if intent.sequence == 1:  # PLAY
            play_started.set()
            await asyncio.wait_for(block_play.wait(), timeout=2.0)
        elif intent.sequence == 2:  # STOP
            stop_started.set()
            await asyncio.wait_for(block_stop.wait(), timeout=2.0)
        else:  # PLAY3 (seq 4)
            play3_started.set()
            await asyncio.wait_for(block_play3.wait(), timeout=2.0)
            play3_done.set()

    arb = DeviceCommandArbiter(_exec)
    try:
        arb.submit(IntentKind.PLAY)
        await asyncio.wait_for(play_started.wait(), timeout=2.0)
        assert arb.active_sequence == 1

        arb.submit(IntentKind.STOP)
        arb.submit(IntentKind.PLAY, {"n": 2})
        arb.submit(IntentKind.PLAY, {"n": 3})

        # STOP is pending barrier; PLAY3 in after_barrier (seq 4)
        assert arb.pending_sequence == 2
        assert arb.after_barrier_sequence == 4

        block_play.set()
        await asyncio.wait_for(stop_started.wait(), timeout=2.0)
        assert arb.active_sequence == 2

        block_stop.set()
        await asyncio.wait_for(play3_started.wait(), timeout=2.0)
        assert arb.active_sequence == 4

        block_play3.set()
        await asyncio.wait_for(play3_done.wait(), timeout=2.0)
        assert arb.active_sequence is None

        assert executed == [1, 2, 4]
    finally:
        block_play.set()
        block_stop.set()
        block_play3.set()
        await arb.close()


@pytest.mark.asyncio
async def test_barrier_active_stop_buffers_regulars():
    """active STOP, submit PLAY2/PLAY3 → STOP,PLAY3."""
    executed: list[int] = []
    stop_started = asyncio.Event()
    block_stop = asyncio.Event()
    play_started = asyncio.Event()
    block_play = asyncio.Event()
    play_done = asyncio.Event()

    async def _exec(intent: PlaybackIntent) -> None:
        executed.append(intent.sequence)
        if intent.kind == IntentKind.STOP:
            stop_started.set()
            await asyncio.wait_for(block_stop.wait(), timeout=2.0)
        else:
            play_started.set()
            await asyncio.wait_for(block_play.wait(), timeout=2.0)
            play_done.set()

    arb = DeviceCommandArbiter(_exec)
    try:
        arb.submit(IntentKind.STOP)
        await asyncio.wait_for(stop_started.wait(), timeout=2.0)
        assert arb.active_sequence == 1

        arb.submit(IntentKind.PLAY, {"n": 2})
        arb.submit(IntentKind.PLAY, {"n": 3})

        # STOP is active barrier; after_barrier == PLAY3 (seq 3)
        assert arb.pending_sequence is None
        assert arb.after_barrier_sequence == 3

        block_stop.set()
        await asyncio.wait_for(play_started.wait(), timeout=2.0)
        assert arb.active_sequence == 3

        block_play.set()
        await asyncio.wait_for(play_done.wait(), timeout=2.0)

        assert executed == [1, 3]
        assert arb.active_sequence is None
    finally:
        block_stop.set()
        block_play.set()
        await arb.close()


@pytest.mark.asyncio
async def test_barrier_discards_regular_pending_on_arrival():
    """active PLAY, pending PLAY2, submit STOP → PLAY,STOP (PLAY2 discarded)."""
    executed: list[int] = []
    play_started = asyncio.Event()
    block_play = asyncio.Event()
    stop_started = asyncio.Event()
    block_stop = asyncio.Event()
    stop_done = asyncio.Event()

    async def _exec(intent: PlaybackIntent) -> None:
        executed.append(intent.sequence)
        if intent.kind == IntentKind.PLAY:
            play_started.set()
            await asyncio.wait_for(block_play.wait(), timeout=2.0)
        else:
            stop_started.set()
            await asyncio.wait_for(block_stop.wait(), timeout=2.0)
            stop_done.set()

    arb = DeviceCommandArbiter(_exec)
    try:
        arb.submit(IntentKind.PLAY)
        await asyncio.wait_for(play_started.wait(), timeout=2.0)

        arb.submit(IntentKind.PLAY, {"n": 2})
        assert arb.pending_sequence == 2

        arb.submit(IntentKind.STOP)
        # STOP replaces PLAY2, after_barrier is cleared
        assert arb.pending_sequence == 3
        assert arb.after_barrier_sequence is None

        block_play.set()
        await asyncio.wait_for(stop_started.wait(), timeout=2.0)
        assert arb.active_sequence == 3

        block_stop.set()
        await asyncio.wait_for(stop_done.wait(), timeout=2.0)

        assert executed == [1, 3]
    finally:
        block_play.set()
        block_stop.set()
        await arb.close()


@pytest.mark.asyncio
async def test_barrier_latest_wins_and_clears_after():
    """pending STOP + after_barrier PLAY_X, submit STOP2 → only STOP2."""
    executed: list[int] = []
    play_started = asyncio.Event()
    block_play = asyncio.Event()
    stop2_started = asyncio.Event()
    block_stop2 = asyncio.Event()
    stop2_done = asyncio.Event()

    async def _exec(intent: PlaybackIntent) -> None:
        executed.append(intent.sequence)
        if intent.kind == IntentKind.PLAY:
            play_started.set()
            await asyncio.wait_for(block_play.wait(), timeout=2.0)
        else:
            stop2_started.set()
            await asyncio.wait_for(block_stop2.wait(), timeout=2.0)
            stop2_done.set()

    arb = DeviceCommandArbiter(_exec)
    try:
        arb.submit(IntentKind.PLAY)
        await asyncio.wait_for(play_started.wait(), timeout=2.0)

        arb.submit(IntentKind.STOP)
        assert arb.pending_sequence == 2

        arb.submit(IntentKind.PLAY, {"n": "X"})
        assert arb.after_barrier_sequence == 3

        # New barrier replaces old barrier + clears after_barrier
        arb.submit(IntentKind.STOP)
        assert arb.pending_sequence == 4
        assert arb.after_barrier_sequence is None

        block_play.set()
        await asyncio.wait_for(stop2_started.wait(), timeout=2.0)
        assert arb.active_sequence == 4

        block_stop2.set()
        await asyncio.wait_for(stop2_done.wait(), timeout=2.0)

        assert executed == [1, 4]
    finally:
        block_play.set()
        block_stop2.set()
        await arb.close()


@pytest.mark.asyncio
async def test_barrier_pause_is_also_barrier():
    """PAUSE is a barrier kind just like STOP."""
    executed: list[int] = []
    play_started = asyncio.Event()
    block_play = asyncio.Event()
    pause_started = asyncio.Event()
    block_pause = asyncio.Event()
    pause_done = asyncio.Event()

    async def _exec(intent: PlaybackIntent) -> None:
        executed.append(intent.sequence)
        if intent.kind == IntentKind.PLAY:
            play_started.set()
            await asyncio.wait_for(block_play.wait(), timeout=2.0)
        else:
            pause_started.set()
            await asyncio.wait_for(block_pause.wait(), timeout=2.0)
            pause_done.set()

    arb = DeviceCommandArbiter(_exec)
    try:
        arb.submit(IntentKind.PLAY)
        await asyncio.wait_for(play_started.wait(), timeout=2.0)

        arb.submit(IntentKind.NEXT)  # regular pending
        assert arb.pending_sequence == 2

        arb.submit(IntentKind.PAUSE)  # barrier replaces regular pending
        assert arb.pending_sequence == 3
        assert arb.after_barrier_sequence is None

        block_play.set()
        await asyncio.wait_for(pause_started.wait(), timeout=2.0)

        block_pause.set()
        await asyncio.wait_for(pause_done.wait(), timeout=2.0)

        assert executed == [1, 3]
    finally:
        block_play.set()
        block_pause.set()
        await arb.close()


@pytest.mark.asyncio
async def test_close_clears_barrier_and_after():
    """Close with pending barrier + after_barrier: both cleared, neither exec."""
    executed: list[int] = []
    play_started = asyncio.Event()
    block_play = asyncio.Event()

    async def _exec(intent: PlaybackIntent) -> None:
        executed.append(intent.sequence)
        play_started.set()
        await asyncio.wait_for(block_play.wait(), timeout=2.0)

    arb = DeviceCommandArbiter(_exec)
    arb.submit(IntentKind.PLAY)
    await asyncio.wait_for(play_started.wait(), timeout=2.0)

    arb.submit(IntentKind.STOP)  # pending barrier
    arb.submit(IntentKind.PLAY, {"n": "X"})  # after_barrier
    assert arb.pending_sequence == 2
    assert arb.after_barrier_sequence == 3

    await arb.close()
    assert arb.is_closed
    assert arb.active_sequence is None
    assert arb.pending_sequence is None
    assert arb.after_barrier_sequence is None

    block_play.set()
    # PLAY_X never executed — close cancelled before STOP was reached
    assert 3 not in executed


@pytest.mark.asyncio
async def test_barrier_exception_continues_after_sequence():
    """Executor raises on barrier → next regular still executes."""
    executed: list[int] = []
    stop_failed = asyncio.Event()
    after_done = asyncio.Event()

    async def _exec(intent: PlaybackIntent) -> None:
        executed.append(intent.sequence)
        if intent.kind == IntentKind.STOP:
            stop_failed.set()
            raise RuntimeError("barrier boom")
        else:
            after_done.set()

    arb = DeviceCommandArbiter(_exec)
    try:
        arb.submit(IntentKind.STOP)
        await asyncio.wait_for(stop_failed.wait(), timeout=2.0)
        assert arb.last_error is not None
        assert arb.active_sequence is None

        # After STOP failed, active is not barrier → PLAY goes to pending
        arb.submit(IntentKind.PLAY)
        await asyncio.wait_for(after_done.wait(), timeout=2.0)

        assert executed == [1, 2]
        assert arb.active_sequence is None
    finally:
        await arb.close()


@pytest.mark.asyncio
async def test_barrier_exception_with_pending_after():
    """Executor raises on active barrier → buffered after_barrier executes."""
    executed: list[int] = []
    stop_started = asyncio.Event()
    block_stop = asyncio.Event()
    after_done = asyncio.Event()

    async def _exec(intent: PlaybackIntent) -> None:
        executed.append(intent.sequence)
        if intent.kind == IntentKind.STOP:
            stop_started.set()
            await asyncio.wait_for(block_stop.wait(), timeout=2.0)
            raise RuntimeError("barrier boom")
        else:
            after_done.set()

    arb = DeviceCommandArbiter(_exec)
    try:
        arb.submit(IntentKind.STOP)
        await asyncio.wait_for(stop_started.wait(), timeout=2.0)
        assert arb.active_sequence == 1

        # Submit while STOP is active (barrier)
        arb.submit(IntentKind.PLAY)  # → after_barrier
        assert arb.after_barrier_sequence == 2

        block_stop.set()
        await asyncio.wait_for(after_done.wait(), timeout=2.0)

        assert executed == [1, 2]
        assert arb.last_error is not None
        assert arb.active_sequence is None
    finally:
        block_stop.set()
        await arb.close()


@pytest.mark.asyncio
async def test_regular_no_barrier_normal_latest_pending():
    """No barrier present: regular replaces regular (normal latest-pending)."""
    executed: list[int] = []
    a_started = asyncio.Event()
    block_a = asyncio.Event()
    b_started = asyncio.Event()
    block_b = asyncio.Event()
    b_done = asyncio.Event()

    async def _exec(intent: PlaybackIntent) -> None:
        executed.append(intent.sequence)
        if len(executed) == 1:
            a_started.set()
            await asyncio.wait_for(block_a.wait(), timeout=2.0)
        else:
            b_started.set()
            await asyncio.wait_for(block_b.wait(), timeout=2.0)
            b_done.set()

    arb = DeviceCommandArbiter(_exec)
    try:
        arb.submit(IntentKind.PLAY)
        await asyncio.wait_for(a_started.wait(), timeout=2.0)

        arb.submit(IntentKind.NEXT)
        arb.submit(IntentKind.PREVIOUS)
        arb.submit(IntentKind.RESUME)
        # No barrier → normal latest-pending, RESUME survives
        assert arb.pending_sequence == 4
        assert arb.after_barrier_sequence is None

        block_a.set()
        await asyncio.wait_for(b_started.wait(), timeout=2.0)

        block_b.set()
        await asyncio.wait_for(b_done.wait(), timeout=2.0)

        assert executed == [1, 4]
        assert arb.active_sequence is None
    finally:
        block_a.set()
        block_b.set()
        await arb.close()


@pytest.mark.asyncio
async def test_barrier_without_after_executes_and_idles():
    """Barrier submitted alone executes normally and arbiter goes idle."""
    executed: list[int] = []
    done = asyncio.Event()

    async def _exec(intent: PlaybackIntent) -> None:
        executed.append(intent.sequence)
        done.set()

    arb = DeviceCommandArbiter(_exec)
    try:
        arb.submit(IntentKind.STOP)
        await asyncio.wait_for(done.wait(), timeout=2.0)

        assert executed == [1]
        assert arb.active_sequence is None
        assert arb.pending_sequence is None
        assert arb.after_barrier_sequence is None
    finally:
        await arb.close()


@pytest.mark.asyncio
async def test_after_barrier_sequence_property_readonly():
    """after_barrier_sequence reflects the after_barrier slot correctly."""
    play_started = asyncio.Event()
    block_play = asyncio.Event()
    final_done = asyncio.Event()

    async def _exec(intent: PlaybackIntent) -> None:
        if intent.kind == IntentKind.STOP:
            play_started.set()
            await asyncio.wait_for(block_play.wait(), timeout=2.0)
        else:
            final_done.set()

    arb = DeviceCommandArbiter(_exec)
    try:
        arb.submit(IntentKind.STOP)
        await asyncio.wait_for(play_started.wait(), timeout=2.0)
        assert arb.active_sequence == 1

        # No after_barrier yet
        assert arb.after_barrier_sequence is None

        arb.submit(IntentKind.PLAY)
        assert arb.after_barrier_sequence == 2

        arb.submit(IntentKind.PLAY)
        assert arb.after_barrier_sequence == 3  # latest wins

        block_play.set()
        await asyncio.wait_for(final_done.wait(), timeout=2.0)

        assert arb.after_barrier_sequence is None  # consumed
        assert arb.active_sequence is None
    finally:
        block_play.set()
        await arb.close()


