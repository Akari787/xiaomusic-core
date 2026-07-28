"""Single owner for device-scoped playback background tasks.

This module deliberately has no device or I/O dependencies.  ``PlaybackTaskRegistry``
owns task creation, replacement, cancellation, completion observation, and the
safe data-only diagnostics exposed by ``snapshot``.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Coroutine
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskKind(str, Enum):
    DURATION_PROBE = "duration_probe"
    PLAYBACK_CONFIRMATION = "playback_confirmation"
    STATUS_PROBE = "status_probe"
    COMPLETION_NEXT_TIMER = "completion_next_timer"
    FAILURE_RETRY = "failure_retry"
    TTS_TIMER = "tts_timer"
    ADD_SONG_TIMER = "add_song_timer"
    STOP_TIMER = "stop_timer"
    FAST_GROUP_STOP = "fast_group_stop"


# ── scope classification ──────────────────────────────────────────────

ATTEMPT_SCOPED_KINDS: tuple[TaskKind, ...] = (
    TaskKind.DURATION_PROBE,
    TaskKind.PLAYBACK_CONFIRMATION,
    TaskKind.STATUS_PROBE,
    TaskKind.COMPLETION_NEXT_TIMER,
    TaskKind.FAILURE_RETRY,
)

SESSION_SCOPED_KINDS: tuple[TaskKind, ...] = (
    TaskKind.TTS_TIMER,
    TaskKind.ADD_SONG_TIMER,
    TaskKind.FAST_GROUP_STOP,
)


@dataclass(frozen=True, order=True)
class TaskGeneration:
    """Comparable immutable lifecycle identity for a playback task."""

    queue_session_id: int = 0
    command_generation: int = 0
    track_attempt_id: int = 0
    session_id: int = 0

    @classmethod
    def from_token(cls, token: Any, *, session_id: int = 0) -> TaskGeneration:
        return cls(
            int(getattr(token, "queue_session_id", 0)),
            int(getattr(token, "command_generation", 0)),
            int(getattr(token, "track_attempt_id", 0)),
            int(session_id),
        )


@dataclass(frozen=True)
class TaskSnapshot:
    kind: TaskKind
    generation: TaskGeneration
    status: str  # pending | running | done | cancelled
    active: bool
    metadata: dict[str, Any] = field(default_factory=dict)
    last_error: str = ""


@dataclass
class _Entry:
    """Mutable bookkeeping for one registered task.

    ``source`` holds the original coroutine/awaitable passed to ``start``.
    If the wrapper task is cancelled *before* it executes, ``source`` is
    explicitly closed to prevent "coroutine was never awaited" warnings.
    ``started`` becomes True inside the wrapper's first line.
    """

    generation: TaskGeneration
    task: asyncio.Task[Any]
    metadata: dict[str, Any]
    status: str = "pending"
    last_error: str = ""
    source: Any = None
    started: bool = False


class PlaybackTaskRegistry:
    """Own at most one active task per kind for one Device playback scope."""

    _SENSITIVE_KEY_PATTERNS: tuple[str, ...] = (
        "secret",
        "token",
        "password",
        "api_key",
        "access_key",
        "authorization",
        "url",
    )

    def __init__(self) -> None:
        self._entries: dict[TaskKind, _Entry] = {}
        self._history: dict[TaskKind, _Entry] = {}
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    # ── helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _kind(kind: TaskKind | str) -> TaskKind:
        return kind if isinstance(kind, TaskKind) else TaskKind(kind)

    @staticmethod
    def _generation(generation: TaskGeneration | Any) -> TaskGeneration:
        if isinstance(generation, TaskGeneration):
            return generation
        return TaskGeneration.from_token(generation)

    @classmethod
    def _is_sensitive_key(cls, key: str) -> bool:
        key_lower = key.lower()
        return any(pat in key_lower for pat in cls._SENSITIVE_KEY_PATTERNS)

    @classmethod
    def _redact(cls, value: Any, *, depth: int = 0, key: str = "") -> Any:
        if depth > 4:
            return "<truncated>"
        if value is None or isinstance(value, (str, int, float, bool)):
            if isinstance(value, str) and cls._is_sensitive_key(key):
                return "<redacted>"
            return value
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, (list, tuple, set, frozenset)):
            return [cls._redact(v, depth=depth + 1) for v in value]
        if isinstance(value, dict):
            return {
                str(k): cls._redact(v, depth=depth + 1, key=str(k))
                for k, v in value.items()
                if isinstance(k, (str, int, float, bool))
            }
        return f"<{type(value).__name__}>"

    def _metadata(self, metadata: dict[str, Any] | None) -> dict[str, Any]:
        raw = dict(metadata or {})
        return self._redact(raw, depth=0, key="")

    @staticmethod
    def _close_source(source: Any) -> None:
        """Close a coroutine object that was never awaited.

        Future / Task objects do not have ``.close()`` — they are safely
        ignored.
        """
        if source is None:
            return
        if inspect.iscoroutine(source):
            source.close()

    # ── done observer ─────────────────────────────────────────────────

    def _observe(self, kind: TaskKind, entry: _Entry, task: asyncio.Task[Any]) -> None:
        # If the wrapper never ran, close the original coroutine to
        # prevent "coroutine was never awaited" warnings.
        if not entry.started and entry.source is not None:
            self._close_source(entry.source)
            entry.source = None
        if task.cancelled():
            entry.status = "cancelled"
        else:
            try:
                error = task.exception()
            except asyncio.CancelledError:
                error = None
                entry.status = "cancelled"
            if error is not None:
                entry.last_error = str(error)[:200]
                entry.status = "done"
            else:
                entry.status = "done"
        if self._entries.get(kind) is entry:
            self._entries.pop(kind, None)
            self._history[kind] = entry

    def _attach(self, kind: TaskKind, entry: _Entry) -> asyncio.Task[Any]:
        entry.task.add_done_callback(
            lambda task, k=kind, e=entry: self._observe(k, e, task)
        )
        return entry.task

    # ── cancellation helpers ──────────────────────────────────────────

    def _cancel_entry_source(self, entry: _Entry) -> None:
        """Cancel the underlying task and close the source if never started."""
        if not entry.task.done():
            entry.task.cancel()
        if not entry.started and entry.source is not None:
            self._close_source(entry.source)
            entry.source = None

    # ── public API ────────────────────────────────────────────────────

    def start(
        self,
        kind: TaskKind | str,
        generation: TaskGeneration | Any,
        coro: Awaitable[Any] | Coroutine[Any, Any, Any],
        metadata: dict[str, Any] | None = None,
    ) -> asyncio.Task[Any] | None:
        """Create and own a task, cancelling the previous task of *kind*.

        Returns the *new* task.  The old task is cancelled and remains
        *awaitable* by the caller — the registry does not await it here.
        """
        if self._closed:
            self._close_source(coro)
            return None
        k = self._kind(kind)
        gen = self._generation(generation)
        meta = self._metadata(metadata)

        async def _wrap() -> Any:
            entry.started = True
            if entry.status != "cancelled":
                entry.status = "running"
            return await coro

        task: asyncio.Task[Any] = asyncio.create_task(_wrap())
        entry = _Entry(
            generation=gen,
            task=task,
            metadata=meta,
            status="pending",
            source=coro,
            started=False,
        )
        self.cancel(k)
        self._entries[k] = entry
        return self._attach(k, entry)

    def adopt(
        self,
        kind: TaskKind | str,
        generation: TaskGeneration | Any,
        task: asyncio.Task[Any],
        metadata: dict[str, Any] | None = None,
    ) -> asyncio.Task[Any] | None:
        if self._closed:
            if task is not asyncio.current_task() and not task.done():
                task.cancel()
            return None
        k = self._kind(kind)
        gen = self._generation(generation)
        meta = self._metadata(metadata)
        self.cancel(k)
        status = "running" if not task.done() else "done"
        # adopted tasks have no source — the caller owns lifecycle
        entry = _Entry(
            generation=gen,
            task=task,
            metadata=meta,
            status=status,
            source=None,
            started=True,
        )
        self._entries[k] = entry
        return self._attach(k, entry)

    def get_task(self, kind: TaskKind | str) -> asyncio.Task[Any] | None:
        entry = self._entries.get(self._kind(kind))
        return entry.task if entry is not None else None

    def cancel(self, kind: TaskKind | str) -> bool:
        entry = self._entries.get(self._kind(kind))
        if entry is None:
            return False
        if entry.task is asyncio.current_task():
            return False
        acted = False
        if not entry.task.done():
            entry.task.cancel()
            acted = True
        if not entry.started and entry.source is not None:
            self._close_source(entry.source)
            entry.source = None
            acted = True
        if acted:
            entry.status = "cancelled"
        return acted

    def cancel_older_than(self, generation: TaskGeneration | Any) -> None:
        target = self._generation(generation)
        for _kind, entry in list(self._entries.items()):
            if entry.generation < target:
                if entry.task is not asyncio.current_task():
                    self._cancel_entry_source(entry)
                    entry.status = "cancelled"

    def cancel_by_kinds(self, *kinds: TaskKind) -> int:
        """Cancel every active task whose kind is in *kinds*.  Returns count."""
        return sum(1 for k in kinds if self.cancel(k))

    def cancel_all(self) -> None:
        for kind in list(self._entries):
            self.cancel(kind)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Cancel all entries — sources are closed inside cancel() / _cancel_entry_source
        for _kind, entry in list(self._entries.items()):
            if entry.task is not asyncio.current_task():
                self._cancel_entry_source(entry)
                entry.status = "cancelled"
        tasks = [
            entry.task
            for entry in self._entries.values()
            if entry.task is not asyncio.current_task()
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._entries.clear()

    def snapshot(self) -> tuple[TaskSnapshot, ...]:
        result: list[TaskSnapshot] = []
        for kind, entry in {**self._history, **self._entries}.items():
            task = entry.task
            active = kind in self._entries and not task.done()
            result.append(
                TaskSnapshot(
                    kind=kind,
                    generation=entry.generation,
                    status=entry.status,
                    active=active,
                    metadata=self._redact(dict(entry.metadata), depth=0, key=""),
                    last_error=entry.last_error,
                )
            )
        return tuple(sorted(result, key=lambda item: item.kind.value))
