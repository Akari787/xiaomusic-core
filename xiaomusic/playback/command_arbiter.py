"""Per-device Command Arbiter — single-worker, barrier-aware (T04-C2a).

No production entry points. No DevicePlayer/Facade changes. Pure core contract.

**Single-event-loop contract**: ``submit()`` is synchronous and must be called
from the same event loop that owns the arbiter.  Cross-thread usage is not
supported.  All internal state transitions happen on the submitting thread /
event loop; no locks are needed.

**Barrier semantics**: STOP and PAUSE are barrier kinds. When a barrier is
pending or active, new regular intents (PLAY/NEXT/PREVIOUS/AUTO_NEXT/RESUME/
RETRY) cannot replace it; they are buffered in an ``_after_barrier`` slot
(latest-wins).  A new barrier always replaces any prior pending barrier and
clears the after-barrier slot.  After a barrier completes, the worker
immediately executes the latest buffered after-barrier intent.
"""

from __future__ import annotations

import asyncio
import copy
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class IntentKind(str, Enum):
    PLAY = "play"
    NEXT = "next"
    PREVIOUS = "previous"
    AUTO_NEXT = "auto_next"
    STOP = "stop"
    PAUSE = "pause"
    RESUME = "resume"
    RETRY = "retry"


@dataclass(frozen=True)
class PlaybackIntent:
    """Immutable intent snapshot.

    ``payload`` is a safe deep-copy taken at submit time; external mutation
    after submission cannot affect the stored data.
    """

    sequence: int
    kind: IntentKind
    payload: dict[str, Any] | None = field(default=None)
    submitted_at: float = field(default_factory=time.monotonic)


@dataclass(frozen=True)
class IntentReceipt:
    """Fast acknowledgment returned by ``submit()``."""

    accepted: bool
    sequence: int

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable dict for public API callers."""
        return {"accepted": self.accepted, "sequence": self.sequence}


# Executor signature: async callable receiving a PlaybackIntent.
Executor = Callable[[PlaybackIntent], Awaitable[Any]]


class ArbiterClosedError(RuntimeError):
    """Raised when ``submit()`` is called after ``close()``."""


class DeviceCommandArbiter:
    """Per-device single-worker command arbiter with barrier ordering.

    Guarantees:
    * At most one ``executor(intent)`` call in flight at any time.
    * ``submit()`` returns immediately — never awaits the executor.
    * **Barrier ordering**: STOP / PAUSE are barriers.  A barrier always
      replaces any pending regular intent and clears buffered after-barrier
      work.  Regular intents arriving while a barrier is pending or active
      are buffered in a single ``_after_barrier`` slot (latest-wins) and
      executed immediately after the barrier completes.
    * In the absence of barriers, normal latest-pending applies.
    * Executor exceptions do **not** crash the arbiter; they are recorded
      in ``last_error`` and the worker continues (barrier/after sequence
      also continues).
    * After ``close()``, ``active_sequence``, ``pending_sequence``, and
      ``after_barrier_sequence`` are all ``None``.
    """

    def __init__(self, executor: Executor) -> None:
        self._executor: Executor = executor
        self._wake: asyncio.Event = asyncio.Event()
        self._closed: bool = False

        # ── monotonic sequence ─────────────────────────────────────
        self._next_seq: int = 1

        # ── worker-visible state ───────────────────────────────────
        self._pending: PlaybackIntent | None = None
        self._after_barrier: PlaybackIntent | None = None
        self._active_seq: int | None = None
        self._active_is_barrier: bool = False
        self._last_error: BaseException | None = None

        # ── worker task (started eagerly so close() always has a task) ─
        self._worker_task: asyncio.Task[None] = asyncio.create_task(
            self._worker_loop()
        )

    # ── public read-only properties ──────────────────────────────────

    @property
    def active_sequence(self) -> int | None:
        """Sequence number of the currently executing intent, or None."""
        return self._active_seq

    @property
    def pending_sequence(self) -> int | None:
        """Sequence number of the pending (latest queued) intent, or None.

        May be a barrier or a regular intent; barriers take priority
        over anything in ``after_barrier_sequence``.
        """
        p = self._pending
        return p.sequence if p is not None else None

    @property
    def after_barrier_sequence(self) -> int | None:
        """Sequence number of the latest after-barrier intent, or None.

        Set only when a regular intent arrived while a barrier was
        pending or active.  Executed immediately after the barrier
        completes.
        """
        ab = self._after_barrier
        return ab.sequence if ab is not None else None

    @property
    def is_closed(self) -> bool:
        """True once ``close()`` has been called."""
        return self._closed

    @property
    def last_error(self) -> BaseException | None:
        """Most recent executor exception, or None.

        Successful executions do **not** clear this field; it always
        reflects the last error for observability.
        """
        return self._last_error

    # ── submit ───────────────────────────────────────────────────────

    @staticmethod
    def _is_barrier(kind: IntentKind) -> bool:
        """Return True for STOP / PAUSE — the barrier kinds."""
        return kind in (IntentKind.STOP, IntentKind.PAUSE)

    def submit(
        self,
        kind: IntentKind,
        payload: dict[str, Any] | None = None,
    ) -> IntentReceipt:
        """Submit an intent and return immediately.

        Never awaits the executor.  Barrier-aware routing:

        * **Barrier (STOP/PAUSE)**: always replaces ``_pending`` and
          clears ``_after_barrier`` (latest STOP/PAUSE wins).
        * **Regular**: if a barrier is pending or active the intent
          lands in ``_after_barrier`` (latest-wins); otherwise normal
          latest-pending in ``_pending``.

        Must be called from the owning event loop; not thread-safe.
        """
        if self._closed:
            raise ArbiterClosedError("arbiter is closed")

        seq = self._next_seq
        self._next_seq += 1

        intent = PlaybackIntent(
            sequence=seq,
            kind=kind,
            payload=copy.deepcopy(payload) if payload is not None else None,
            submitted_at=time.monotonic(),
        )

        if self._is_barrier(kind):
            # Barrier always takes the pending slot and clears after.
            self._pending = intent
            self._after_barrier = None
        elif (
            (self._pending is not None and self._is_barrier(self._pending.kind))
            or self._active_is_barrier
        ):
            # A barrier is blocking — buffer in after_barrier (latest-wins).
            self._after_barrier = intent
        else:
            # No barrier blocking — normal latest-pending.
            self._pending = intent

        self._wake.set()  # wake idle worker

        return IntentReceipt(accepted=True, sequence=seq)

    # ── close ────────────────────────────────────────────────────────

    async def close(self) -> None:
        """Cancel and await the worker task.  Subsequent ``submit()`` raises.

        Clears ``_pending``, ``_after_barrier``, and ``_active_seq`` so
        observable properties read ``None``.  Idempotent: multiple calls
        are safe.
        """
        if self._closed:
            return
        self._closed = True
        self._pending = None
        self._after_barrier = None
        self._active_seq = None
        self._active_is_barrier = False
        self._wake.set()  # unblock worker if it's waiting
        task = self._worker_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    # ── worker loop ──────────────────────────────────────────────────

    async def _worker_loop(self) -> None:
        """Serial executor loop — only reached via ``_wake`` events."""
        while True:
            await self._wake.wait()

            if self._closed:
                return

            self._wake.clear()

            # ── grab work: pending first (barrier or regular), ─────
            #     then after_barrier if nothing is pending ───────────
            intent = self._pending
            self._pending = None

            if intent is None:
                intent = self._after_barrier
                self._after_barrier = None

            if intent is None:  # spurious wake — shouldn't happen, but be safe
                continue

            self._active_seq = intent.sequence
            self._active_is_barrier = self._is_barrier(intent.kind)
            try:
                await self._executor(intent)
            except Exception as exc:  # noqa: BLE001
                self._last_error = exc
            finally:
                self._active_seq = None
                self._active_is_barrier = False

            # ── if work remains, ensure wake is set so the loop ─────
            #     continues without missing anything ─────────────────
            if self._pending is not None or self._after_barrier is not None:
                self._wake.set()
