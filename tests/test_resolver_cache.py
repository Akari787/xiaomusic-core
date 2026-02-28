import time

from xiaomusic.network_audio.contracts import ResolveResult
from xiaomusic.network_audio.resolver_cache import ResolverCache


def _ok_result(is_live=False):
    return ResolveResult(
        ok=True,
        source_url="http://source/audio.mp3",
        title="t",
        is_live=is_live,
        container_hint="mp3",
        error_code=None,
        error_message=None,
        meta={},
    )


def test_resolver_cache_hits_within_ttl():
    cache = ResolverCache(live_ttl_seconds=30, vod_ttl_seconds=1)
    key = "https://example.com/v?id=1"

    calls = {"n": 0}

    def resolve_once():
        calls["n"] += 1
        return _ok_result(is_live=False)

    for _ in range(3):
        cached = cache.get(key)
        if cached is None:
            res = resolve_once()
            cache.set(key, res)

    assert calls["n"] == 1
    stats = cache.stats()
    assert stats["hits"] >= 2


def test_resolver_cache_expires_after_ttl():
    cache = ResolverCache(live_ttl_seconds=1, vod_ttl_seconds=1)
    key = "https://example.com/v?id=2"
    calls = {"n": 0}

    def resolve_once():
        calls["n"] += 1
        return _ok_result(is_live=False)

    if cache.get(key) is None:
        cache.set(key, resolve_once())
    time.sleep(1.1)
    if cache.get(key) is None:
        cache.set(key, resolve_once())

    assert calls["n"] == 2


def test_resolver_cache_does_not_store_failures():
    cache = ResolverCache()
    key = "https://example.com/v?id=3"

    fail = ResolveResult(
        ok=False,
        source_url="",
        title="",
        is_live=False,
        container_hint="unknown",
        error_code="E_RESOLVE_NONZERO_EXIT",
        error_message="err",
        meta={},
    )

    cache.set(key, fail)
    assert cache.get(key) is None
