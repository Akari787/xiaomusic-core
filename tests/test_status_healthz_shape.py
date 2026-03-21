import pytest

from xiaomusic.relay.runtime import RelayRuntime


def test_runtime_healthz_contains_cache_and_active_stats():
    class _XM:
        def __init__(self):
            self.config = type("C", (), {"hostname": "http://127.0.0.1", "public_port": 58090})()
            self.music_library = object()
            self.log = object()

    rt = RelayRuntime(_XM())
    out = rt.healthz()
    assert "active_sessions" in out
    assert "cache_stats" in out
    assert "session_count" in out
