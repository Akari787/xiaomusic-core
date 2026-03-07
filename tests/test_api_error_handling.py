from __future__ import annotations

import pytest

from xiaomusic.api.models import PlayRequest, VolumeRequest
from xiaomusic.api.routers import v1
from xiaomusic.core.errors import InvalidRequestError, SourceResolveError, TransportError


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc", "expected_code"),
    [
        (SourceResolveError("resolve failed"), 20002),
        (TransportError("transport failed"), 40002),
        (InvalidRequestError("invalid request"), 50001),
    ],
)
async def test_v1_errors_mapped_to_api_response(monkeypatch, exc: Exception, expected_code: int):
    class _Facade:
        async def play(self, *, device_id, query, source_hint="auto", options=None, request_id=None):  # noqa: ANN001
            _ = (device_id, query, source_hint, options, request_id)
            raise exc

    monkeypatch.setattr(v1, "_get_facade", lambda: _Facade())
    out = await v1.api_v1_play(PlayRequest(device_id="did-1", query="http://x/y.mp3"))
    assert out["code"] == expected_code
    assert set(out.keys()) == {"code", "message", "data", "request_id"}
    assert out["request_id"]


@pytest.mark.asyncio
async def test_v1_volume_invalid_request_error(monkeypatch):
    class _Facade:
        async def set_volume(self, device_id: str, volume: int, request_id: str | None = None):
            _ = (device_id, volume, request_id)
            raise InvalidRequestError("volume must be in range 0..100")

    monkeypatch.setattr(v1, "_get_facade", lambda: _Facade())
    out = await v1.api_v1_control_volume(VolumeRequest(device_id="did-1", volume=101))
    assert out["code"] == 50001
    assert out["message"]
