from types import SimpleNamespace

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


def test_resolving_session_timeout_moves_to_failed():
    runtime = NetworkAudioRuntime(_fake_xiaomusic())
    runtime.resolve_timeout_seconds = 15

    s = runtime.session_manager.create_session("https://a")
    runtime.session_manager.update_state(s.sid, "resolving", now_ts=10)

    ret = runtime.sweep_idle_sessions(now_ts=40)
    cur = runtime.session_manager.get_session(s.sid)

    assert ret["resolve_timeouts"] == 1
    assert cur is not None
    assert cur.state == "failed"
    assert cur.last_error_code == "E_RESOLVE_TIMEOUT"
