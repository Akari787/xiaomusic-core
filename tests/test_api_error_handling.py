from __future__ import annotations

import pytest
from pydantic import ValidationError

from xiaomusic.api.models import PlayRequest, VolumeRequest
from xiaomusic.api.routers import v1
from xiaomusic.core.errors import DeviceNotFoundError, InvalidRequestError, SourceResolveError, TransportError


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc", "expected_code"),
    [
        (SourceResolveError("resolve failed"), 20002),
        (TransportError("transport failed"), 40002),
        (InvalidRequestError("invalid request"), 40001),
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
async def test_v1_invalid_request_and_device_not_found_are_structured():
    invalid = v1._map_api_exception(InvalidRequestError("invalid request"), "rid-1")
    missing = v1._map_api_exception(DeviceNotFoundError("device not found"), "rid-2")

    assert invalid["code"] == 40001
    assert invalid["data"]["error_code"] == "E_INVALID_REQUEST"
    assert invalid["data"]["stage"] == "request"

    assert missing["code"] == 40004
    assert missing["data"]["error_code"] == "E_DEVICE_NOT_FOUND"
    assert missing["data"]["stage"] == "request"


@pytest.mark.asyncio
async def test_v1_unknown_error_fallback_never_returns_null_stage():
    out = v1._map_api_exception(RuntimeError("boom"), "rid-3")

    assert out["code"] == 10000
    assert out["data"]["error_code"] == "E_INTERNAL"
    assert out["data"]["stage"] == "system"


def test_v1_volume_request_rejects_out_of_range_value():
    with pytest.raises(ValidationError):
        VolumeRequest(device_id="did-1", volume=101)
