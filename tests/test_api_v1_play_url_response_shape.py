import pytest

pytest.importorskip("aiofiles")

from xiaomusic.api.models import ApiV1PlayUrlRequest
from xiaomusic.api.routers import v1


@pytest.mark.asyncio
async def test_v1_play_url_success_shape(monkeypatch):
    class _Facade:
        async def play_url(self, url, speaker_id, options):  # noqa: ARG002
            return {
                "ok": True,
                "sid": "s_1",
                "speaker_id": speaker_id,
                "state": "streaming",
                "title": "song",
                "stream_url": url,
                "error_code": None,
                "raw": {"url_info": {"kind_hint": "audio"}},
            }

    monkeypatch.setattr(v1, "_get_facade", lambda: _Facade())
    out = await v1.api_v1_play_url(ApiV1PlayUrlRequest(url="http://a/b.mp3", speaker_id="did-1"))
    assert out["code"] == 0
    assert out["message"]
    assert out["data"]["ok"] is True
    assert out["data"]["sid"] == "s_1"
    assert out["data"]["speaker_id"] == "did-1"
    assert out["data"]["state"] == "streaming"


@pytest.mark.asyncio
async def test_v1_play_url_failed_shape(monkeypatch):
    class _Facade:
        async def play_url(self, url, speaker_id, options):  # noqa: ARG002
            raise RuntimeError("boom")

    monkeypatch.setattr(v1, "_get_facade", lambda: _Facade())
    out = await v1.api_v1_play_url(ApiV1PlayUrlRequest(url="http://a/b.mp3", speaker_id="did-1"))
    assert out["code"] != 0
    assert out["message"]
    assert out["data"]["ok"] is False
    assert out["data"]["error_code"] == "E_STREAM_NOT_FOUND"
    assert out["data"]["state"] == "failed"
