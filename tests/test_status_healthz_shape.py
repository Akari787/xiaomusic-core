import pytest

from xiaomusic.network_audio.runtime import NetworkAudioRuntime


@pytest.mark.asyncio
async def test_v1_status_contains_observability_fields(monkeypatch):
    pytest.importorskip("aiofiles")
    from xiaomusic.api.routers import v1

    class _Facade:
        async def status(self, target):  # noqa: ARG002
            return {
                "ok": False,
                "sid": "s_1",
                "speaker_id": "did-1",
                "state": "failed",
                "stream_url": "",
                "error_code": "E_RESOLVE_NONZERO_EXIT",
                "cache_hit": False,
                "resolve_ms": 234,
                "fail_stage": "resolve",
                "raw": {
                    "session": {
                        "reconnect_count": 2,
                        "last_transition_at": 123456,
                        "last_error_code": "E_RESOLVE_NONZERO_EXIT",
                    }
                },
            }

    monkeypatch.setattr(v1, "_get_facade", lambda: _Facade())
    out = await v1.api_v1_status(speaker_id="did-1", sid=None)
    assert out["ok"] is False
    assert out["success"] is False
    assert out["error_code"] == "E_RESOLVE_NONZERO_EXIT"
    assert "stage" in out
    assert "last_transition_at" in out
    assert "last_error_code" in out
    assert "reconnect_count" in out
    assert "cache_hit" in out
    assert "resolve_ms" in out


def test_runtime_healthz_contains_cache_and_active_stats():
    class _XM:
        def __init__(self):
            self.config = type("C", (), {"hostname": "http://127.0.0.1", "public_port": 58090})()
            self.music_library = object()
            self.log = object()

    rt = NetworkAudioRuntime(_XM())
    out = rt.healthz()
    assert "active_sessions" in out
    assert "cache_stats" in out
    assert "session_count" in out
