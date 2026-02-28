from types import SimpleNamespace

import pytest

from xiaomusic.network_audio.runtime import NetworkAudioRuntime


def _fake_xiaomusic():
    class _X:
        def __init__(self):
            self.config = SimpleNamespace(
                hostname="http://127.0.0.1", public_port=58090, mi_did=""
            )
            self.music_library = SimpleNamespace()
            self.log = SimpleNamespace(info=lambda *a, **k: None)

        async def play_url(self, did, arg1):  # noqa: ARG002
            return {"code": 0}

    return _X()


@pytest.mark.asyncio
async def test_max_active_sessions_auto_stops_oldest_then_allows_new_play(monkeypatch):
    runtime = NetworkAudioRuntime(_fake_xiaomusic())
    runtime.max_active_sessions = 3
    monkeypatch.setattr(runtime, "ensure_started", lambda: None)

    created = []
    for i in range(3):
        s = runtime.session_manager.create_session(f"https://x/{i}")
        runtime.session_manager.update_state(s.sid, "resolving", now_ts=10 + i)
        runtime.session_manager.update_state(
            s.sid, "streaming", now_ts=20 + i, last_client_at=20 + i
        )
        created.append(s.sid)

    monkeypatch.setattr(
        runtime.audio_streamer,
        "stop_stream",
        lambda sid: runtime.session_manager.stop_session(sid),
    )

    def _fake_play_url(url, *, no_cache=False):  # noqa: ARG001
        s = runtime.session_manager.create_session(url)
        runtime.session_manager.update_state(s.sid, "resolving", now_ts=100)
        runtime.session_manager.update_state(s.sid, "streaming", now_ts=101)
        return {
            "ok": True,
            "error_code": None,
            "error_message": None,
            "session": {"sid": s.sid, "stream_url": "", "state": "streaming"},
        }

    monkeypatch.setattr(runtime.play_service, "play_url", _fake_play_url)

    out = await runtime.play_and_cast("did-1", "https://www.youtube.com/watch?v=abc")
    assert out["ok"] is True

    oldest = runtime.session_manager.get_session(created[0])
    assert oldest is not None
    assert oldest.state == "stopped"


def test_idle_timeout_sweep_stops_old_active_session(monkeypatch):
    runtime = NetworkAudioRuntime(_fake_xiaomusic())
    runtime.idle_timeout_seconds = 120

    s = runtime.session_manager.create_session("https://x")
    runtime.session_manager.update_state(s.sid, "resolving", now_ts=90)
    runtime.session_manager.update_state(s.sid, "streaming", now_ts=100, last_client_at=100)

    monkeypatch.setattr(runtime.audio_streamer, "stop_stream", lambda sid: runtime.session_manager.stop_session(sid))
    ret = runtime.sweep_idle_sessions(now_ts=500)

    cur = runtime.session_manager.get_session(s.sid)
    assert ret["stopped"] == 1
    assert cur is not None
    assert cur.state == "stopped"


@pytest.mark.asyncio
async def test_max_active_sessions_still_returns_error_when_cannot_free_slot(monkeypatch):
    runtime = NetworkAudioRuntime(_fake_xiaomusic())
    runtime.max_active_sessions = 3
    monkeypatch.setattr(runtime, "ensure_started", lambda: None)
    monkeypatch.setattr(runtime, "_stop_oldest_active_session", lambda: False)

    for i in range(3):
        s = runtime.session_manager.create_session(f"https://x/{i}")
        runtime.session_manager.update_state(s.sid, "resolving", now_ts=10 + i)
        runtime.session_manager.update_state(s.sid, "streaming", now_ts=20 + i)

    out = await runtime.play_and_cast("did-1", "https://www.youtube.com/watch?v=abc")
    assert out["ok"] is False
    assert out["error_code"] == "E_TOO_MANY_SESSIONS"


@pytest.mark.asyncio
async def test_active_limit_uses_selected_device_count(monkeypatch):
    runtime = NetworkAudioRuntime(_fake_xiaomusic())
    runtime.max_active_sessions = 10
    runtime.xiaomusic.config.mi_did = "did-1,did-2"
    monkeypatch.setattr(runtime, "ensure_started", lambda: None)
    monkeypatch.setattr(runtime, "_stop_oldest_active_session", lambda: False)

    for i in range(2):
        s = runtime.session_manager.create_session(f"https://x/{i}")
        runtime.session_manager.update_state(s.sid, "resolving", now_ts=10 + i)
        runtime.session_manager.update_state(s.sid, "streaming", now_ts=20 + i)

    out = await runtime.play_and_cast("did-1", "https://www.youtube.com/watch?v=abc")
    assert out["ok"] is False
    assert out["error_code"] == "E_TOO_MANY_SESSIONS"
