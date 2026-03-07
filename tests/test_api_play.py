from __future__ import annotations

import pytest

pytest.importorskip("aiofiles")

from xiaomusic.api.models import PlayRequest
from xiaomusic.api.routers import v1
from xiaomusic.core.errors import DeliveryPrepareError, DeviceNotFoundError, SourceResolveError, TransportError


@pytest.mark.asyncio
async def test_api_v1_play_success(monkeypatch):
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
    out = await v1.api_v1_play(PlayRequest(device_id="did-1", query="http://a/b.mp3"))
    assert out["code"] == 0
    assert out["request_id"]
    assert out["data"]["device_id"] == "did-1"
    assert out["data"]["source_plugin"] == "direct_url"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc", "expected_code"),
    [
        (SourceResolveError("resolve failed"), 20002),
        (DeliveryPrepareError("prepare failed"), 30001),
        (TransportError("transport failed"), 40002),
        (DeviceNotFoundError("missing device"), 40004),
    ],
)
async def test_api_v1_play_error_mapping(monkeypatch, exc: Exception, expected_code: int):
    class _Facade:
        async def play(self, *, device_id, query, source_hint="auto", options=None, request_id=None):  # noqa: ANN001
            _ = (device_id, query, source_hint, options, request_id)
            raise exc

    monkeypatch.setattr(v1, "_get_facade", lambda: _Facade())
    out = await v1.api_v1_play(PlayRequest(device_id="did-1", query="http://a/b.mp3"))
    assert out["code"] == expected_code
    assert out["request_id"]
    assert out["message"]
