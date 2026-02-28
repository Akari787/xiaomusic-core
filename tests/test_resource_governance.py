from types import SimpleNamespace

import pytest

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


@pytest.mark.asyncio
async def test_max_active_sessions_returns_too_many(monkeypatch):
    runtime = NetworkAudioRuntime(_fake_xiaomusic())
    runtime.max_active_sessions = 3
    monkeypatch.setattr(runtime, "ensure_started", lambda: None)

    for i in range(3):
        s = runtime.session_manager.create_session(f"https://x/{i}")
        runtime.session_manager.update_state(s.sid, "streaming")

    out = await runtime.play_and_cast("did-1", "https://www.youtube.com/watch?v=abc")
    assert out["ok"] is False
    assert out["error_code"] == "E_TOO_MANY_SESSIONS"


def test_idle_timeout_sweep_stops_old_active_session(monkeypatch):
    runtime = NetworkAudioRuntime(_fake_xiaomusic())
    runtime.idle_timeout_seconds = 120

    s = runtime.session_manager.create_session("https://x")
    runtime.session_manager.update_state(s.sid, "streaming", now_ts=100, last_client_at=100)

    monkeypatch.setattr(runtime.audio_streamer, "stop_stream", lambda sid: runtime.session_manager.stop_session(sid))
    ret = runtime.sweep_idle_sessions(now_ts=500)

    cur = runtime.session_manager.get_session(s.sid)
    assert ret["stopped"] == 1
    assert cur is not None
    assert cur.state == "stopped"
