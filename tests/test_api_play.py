from __future__ import annotations

import pytest

from xiaomusic.api.models import PlayRequest
from xiaomusic.api.routers import v1
from xiaomusic.core.errors import (
    DeliveryPrepareError,
    DeviceNotFoundError,
    InvalidRequestError,
    SourceResolveError,
    TransportError,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source_hint",
    ["auto", "direct_url", "site_media", "jellyfin", "local_library"],
)
async def test_api_v1_play_success(monkeypatch, source_hint: str):
    calls: list[dict] = []

    class _Facade:
        async def play(self, *, device_id, query, source_hint="auto", options=None, request_id=None):  # noqa: ANN001
            calls.append(
                {
                    "device_id": device_id,
                    "query": query,
                    "source_hint": source_hint,
                    "options": options,
                    "request_id": request_id,
                }
            )
            return {
                "status": "playing",
                "device_id": device_id,
                "source_plugin": source_hint,
                "transport": "mina",
                "request_id": request_id,
                "media": {"title": "song", "stream_url": "http://a/b.mp3", "is_live": False},
                "extra": {},
            }

    monkeypatch.setattr(v1, "_get_facade", lambda: _Facade())
    out = await v1.api_v1_play(
        PlayRequest(
            device_id="did-1",
            query="http://a/b.mp3",
            source_hint=source_hint,
            options={"prefer_proxy": False},
        )
    )
    assert out["code"] == 0
    assert set(out.keys()) == {"code", "message", "data", "request_id"}
    assert out["request_id"]
    assert out["message"] == "ok"
    assert out["data"]["device_id"] == "did-1"
    assert out["data"]["source_plugin"] == source_hint
    assert calls and calls[0]["source_hint"] == source_hint


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc", "expected_code"),
    [
        (SourceResolveError("resolve failed"), 20002),
        (DeliveryPrepareError("prepare failed"), 30001),
        (TransportError("transport failed"), 40002),
        (DeviceNotFoundError("missing device"), 40004),
        (InvalidRequestError("invalid payload"), 50001),
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
