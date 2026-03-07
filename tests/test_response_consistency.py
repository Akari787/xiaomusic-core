import pytest

pytest.importorskip("aiofiles")

from xiaomusic.api.models import ApiV1PlayUrlRequest, ApiV1StopRequest
from xiaomusic.api.routers import v1


@pytest.mark.asyncio
async def test_v1_response_has_unified_top_level_fields(monkeypatch):
    class _Facade:
        async def play(self, *, device_id, query, source_hint="auto", options=None, request_id=None):  # noqa: ANN001
            _ = (query, source_hint, options)
            return {
                "status": "playing",
                "device_id": device_id,
                "source_plugin": "direct_url",
                "transport": "mina",
                "request_id": request_id,
                "media": {"title": "song", "stream_url": "http://a/b.mp3", "is_live": False},
                "extra": {},
            }

    monkeypatch.setattr(v1, "_get_facade", lambda: _Facade())
    out = await v1.api_v1_play_url(ApiV1PlayUrlRequest(url="http://a/b.mp3", speaker_id="did-1"))
    assert set(out.keys()) == {"code", "message", "data", "request_id"}
    assert out["code"] == 0
    assert out["request_id"]


@pytest.mark.asyncio
async def test_v1_stop_wrapper_requires_device_id():
    out = await v1.api_v1_stop(ApiV1StopRequest(speaker_id=None))
    assert out["code"] == 50001
    assert out["request_id"]
    assert out["message"]


@pytest.mark.asyncio
async def test_v1_sessions_cleanup_success_consistency(monkeypatch):
    class _Runtime:
        def cleanup_sessions(self, max_sessions=100, ttl_seconds=None):  # noqa: ARG002
            return {"removed": 1, "remaining": 2}

    monkeypatch.setattr(v1, "get_runtime", lambda: _Runtime())
    out = await v1.api_v1_sessions_cleanup(v1.ApiSessionsCleanupRequest())
    assert out["code"] == 0
    assert out["request_id"]
    assert out["data"]["removed"] == 1
    assert out["data"]["remaining"] == 2
