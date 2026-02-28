import pytest

pytest.importorskip("aiofiles")

from xiaomusic.api.routers import v1


@pytest.mark.asyncio
async def test_detect_base_url_success_shape(monkeypatch):
    monkeypatch.setattr(v1, "detect_base_url", lambda request, cfg: "http://192.168.1.2:58090")
    monkeypatch.setattr(v1.xiaomusic, "getconfig", lambda: object())

    out = await v1.api_v1_detect_base_url(None)
    assert out["ok"] is True
    assert out["success"] is True
    assert out["success"] is out["ok"]
    assert out["error_code"] is None
    assert "message" in out
    assert out["base_url"] == "http://192.168.1.2:58090"


@pytest.mark.asyncio
async def test_detect_base_url_failure_shape(monkeypatch):
    monkeypatch.setattr(v1, "detect_base_url", lambda request, cfg: None)
    monkeypatch.setattr(v1.xiaomusic, "getconfig", lambda: object())

    out = await v1.api_v1_detect_base_url(None)
    assert out["ok"] is False
    assert out["success"] is False
    assert out["success"] is out["ok"]
    assert out["error_code"] == "E_INTERNAL"
    assert "message" in out
    assert "base_url" in out
