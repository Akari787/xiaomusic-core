from __future__ import annotations

import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

from xiaomusic.core.errors.source_errors import SourceResolveError


@dataclass(frozen=True, slots=True)
class _Reference:
    item: dict
    expires_at: float


class OnlinePluginReferenceStore:
    """Process-local, bounded TTL store for opaque online-plugin references."""

    def __init__(self, *, ttl_seconds: float = 300.0, capacity: int = 512) -> None:
        if ttl_seconds <= 0 or capacity <= 0:
            raise ValueError("ttl_seconds and capacity must be positive")
        self.ttl_seconds = float(ttl_seconds)
        self.capacity = int(capacity)
        self._items: OrderedDict[str, _Reference] = OrderedDict()
        self._lock = threading.Lock()

    def put(self, item: dict) -> str:
        if not isinstance(item, dict):
            raise ValueError("online plugin item must be a dict")
        token = f"opq_{secrets.token_urlsafe(32)}"
        now = time.monotonic()
        with self._lock:
            self._purge_expired(now)
            self._items[token] = _Reference(dict(item), now + self.ttl_seconds)
            self._items.move_to_end(token)
            while len(self._items) > self.capacity:
                self._items.popitem(last=False)
        return token

    def get(self, token: str) -> dict:
        now = time.monotonic()
        with self._lock:
            self._purge_expired(now)
            reference = self._items.get(str(token))
        if reference is None:
            raise SourceResolveError("online plugin reference unavailable")
        return dict(reference.item)

    def consume(self, token: str) -> dict:
        """Consume a reference for direct store tests; playback uses retry-safe get()."""
        now = time.monotonic()
        with self._lock:
            self._purge_expired(now)
            reference = self._items.pop(str(token), None)
        if reference is None:
            raise SourceResolveError("online plugin reference unavailable")
        return dict(reference.item)

    def __len__(self) -> int:
        with self._lock:
            self._purge_expired(time.monotonic())
            return len(self._items)

    def _purge_expired(self, now: float) -> None:
        expired = [key for key, ref in self._items.items() if ref.expires_at <= now]
        for key in expired:
            self._items.pop(key, None)
