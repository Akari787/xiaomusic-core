import pytest

pytest.importorskip("aiofiles")

from xiaomusic.api.models import ApiV1PlayUrlRequest, ApiV1StopRequest
from xiaomusic.api.routers import v1


@pytest.mark.asyncio
async def test_v1_play_url_response_success_and_failure_consistency(monkeypatch):
    class _FacadeOK:
        async def play_url(self, url, speaker_id, options):  # noqa: ARG002
            return {
                "ok": True,
                "sid": "s_1",
                "speaker_id": speaker_id,
                "state": "streaming",
                "title": "song",
                "stream_url": url,
                "error_code": None,
                "raw": {},
            }

    monkeypatch.setattr(v1, "_get_facade", lambda: _FacadeOK())
    out_ok = await v1.api_v1_play_url(ApiV1PlayUrlRequest(url="http://a/b.mp3", speaker_id="did-1"))
    assert out_ok["success"] is out_ok["ok"]

    class _FacadeFail:
        async def play_url(self, url, speaker_id, options):  # noqa: ARG002
            raise RuntimeError("boom")

    monkeypatch.setattr(v1, "_get_facade", lambda: _FacadeFail())
    out_fail = await v1.api_v1_play_url(ApiV1PlayUrlRequest(url="http://a/b.mp3", speaker_id="did-1"))
    assert out_fail["success"] is out_fail["ok"]
    assert out_fail["ok"] is False
    assert out_fail["error_code"]
    assert out_fail["message"]


@pytest.mark.asyncio
async def test_v1_stop_failure_always_has_error_code(monkeypatch):
    class _Facade:
        async def stop(self, target):  # noqa: ARG002
            return {
                "ok": False,
                "sid": "",
                "speaker_id": "did-1",
                "state": "failed",
                "stream_url": "",
                "error_code": None,
            }

    monkeypatch.setattr(v1, "_get_facade", lambda: _Facade())
    out = await v1.api_v1_stop(ApiV1StopRequest(speaker_id="did-1"))
    assert out["ok"] is False
    assert out["success"] is False
    assert out["success"] is out["ok"]
    assert out["error_code"] == "E_INTERNAL"
    assert out["message"]


@pytest.mark.asyncio
async def test_v1_sessions_cleanup_success_consistency(monkeypatch):
    class _Runtime:
        def cleanup_sessions(self, max_sessions=100, ttl_seconds=None):  # noqa: ARG002
            return {"removed": 1, "remaining": 2}

    monkeypatch.setattr(v1, "_shared_runtime", lambda: _Runtime())
    out = await v1.api_v1_sessions_cleanup(v1.ApiSessionsCleanupRequest())
    assert out["ok"] is True
    assert out["success"] is True
    assert out["success"] is out["ok"]
