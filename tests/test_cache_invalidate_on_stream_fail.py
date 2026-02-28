from types import SimpleNamespace

from xiaomusic.network_audio.contracts import ResolveResult
from xiaomusic.network_audio.runtime import NetworkAudioRuntime


def _fake_xiaomusic():
    class _X:
        def __init__(self):
            self.config = SimpleNamespace(hostname="http://127.0.0.1", public_port=58090)
            self.music_library = SimpleNamespace()
            self.log = SimpleNamespace(info=lambda *a, **k: None)

        async def play_url(self, did, arg1):  # noqa: ARG002
            return {"code": 0}

    return _X()


def test_stream_fail_invalidates_resolver_cache():
    runtime = NetworkAudioRuntime(_fake_xiaomusic())
    key = "https://www.youtube.com/watch?v=abc123"

    result = ResolveResult(
        ok=True,
        source_url="http://source/a.mp3",
        title="x",
        is_live=False,
        container_hint="mp3",
        error_code=None,
        error_message=None,
        meta={},
    )
    runtime.resolver_cache.set(key, result)
    assert runtime.resolver_cache.get(key) is not None

    s = runtime.session_manager.create_session(key)
    runtime.session_manager.update_state(s.sid, "resolving")
    runtime.session_manager.update_state(s.sid, "streaming")
    runtime.session_manager.update_state(s.sid, "failed", error_code="E_STREAM_START_FAILED")
    runtime._on_stream_failed(s.sid, "E_STREAM_START_FAILED")

    assert runtime.resolver_cache.get(key) is None


def test_non_stream_error_does_not_invalidate_cache():
    runtime = NetworkAudioRuntime(_fake_xiaomusic())
    key = "https://www.youtube.com/watch?v=abc124"
    result = ResolveResult(
        ok=True,
        source_url="http://source/a.mp3",
        title="x",
        is_live=False,
        container_hint="mp3",
        error_code=None,
        error_message=None,
        meta={},
    )
    runtime.resolver_cache.set(key, result)

    runtime._invalidate_cache_on_stream_failure(key, "E_TOO_MANY_SESSIONS")
    assert runtime.resolver_cache.get(key) is not None
