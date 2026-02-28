"""Lightweight in-memory cache for URL resolve results."""

from __future__ import annotations

import time
from threading import Lock
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from xiaomusic.network_audio.contracts import ResolveResult


def normalize_cache_key(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urlsplit(raw)
    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()
    path = parsed.path or ""
    keep_params = {"v", "p", "id", "t", "start"}
    q = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=False)
        if k.lower() in keep_params
    ]
    q.sort(key=lambda kv: (kv[0], kv[1]))
    query = urlencode(q)
    return urlunsplit((scheme, netloc, path, query, ""))


class ResolverCache:
    def __init__(self, live_ttl_seconds: int = 30, vod_ttl_seconds: int = 300) -> None:
        self.live_ttl_seconds = int(live_ttl_seconds)
        self.vod_ttl_seconds = int(vod_ttl_seconds)
        self._data: dict[str, tuple[ResolveResult, float]] = {}
        self._lock = Lock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._last_prune_at: int | None = None

    def _prune_locked(self, now: float) -> None:
        stale = [k for k, (_v, exp) in self._data.items() if exp <= now]
        for key in stale:
            self._data.pop(key, None)
        if stale:
            self._evictions += len(stale)
            self._last_prune_at = int(now)

    def get(self, normalized_url: str) -> ResolveResult | None:
        now = time.time()
        with self._lock:
            self._prune_locked(now)
            item = self._data.get(normalized_url)
            if item is None:
                self._misses += 1
                return None
            self._hits += 1
            return item[0]

    def set(self, normalized_url: str, result: ResolveResult, ttl_seconds: int | None = None) -> None:
        if not result.ok or result.error_code:
            return
        ttl = int(ttl_seconds if ttl_seconds is not None else (self.live_ttl_seconds if result.is_live else self.vod_ttl_seconds))
        if ttl <= 0:
            return
        now = time.time()
        with self._lock:
            self._prune_locked(now)
            self._data[normalized_url] = (result, now + ttl)

    def stats(self) -> dict:
        now = time.time()
        with self._lock:
            self._prune_locked(now)
            return {
                "size": len(self._data),
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
                "last_prune_at": self._last_prune_at,
            }
