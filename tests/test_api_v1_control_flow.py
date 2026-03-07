from __future__ import annotations

import pytest

pytest.importorskip("aiofiles")

from xiaomusic.api.models import (
    ApiV1SetVolumeRequest,
    ApiV1StopRequest,
    ApiV1TtsRequest,
)
from xiaomusic.api.routers import v1


@pytest.mark.asyncio
async def test_api_v1_tts_stop_set_volume_call_facade(monkeypatch):
    called = {
        "tts": False,
        "stop": False,
        "set_volume": False,
    }

    class _Facade:
        async def control_tts(self, device_id: str, text: str, request_id: str | None = None):
            _ = request_id
            assert device_id == "did-1"
            assert text
            called["tts"] = True
            return {"status": "ok", "device_id": device_id, "transport": "miio", "request_id": "r1", "extra": {}}

        async def control_stop(self, device_id: str, request_id: str | None = None):
            _ = request_id
            assert device_id == "did-1"
            called["stop"] = True
            return {
                "status": "stopped",
                "device_id": device_id,
                "transport": "miio",
                "request_id": "r2",
                "extra": {},
            }

        async def control_set_volume(self, device_id: str, volume: int, request_id: str | None = None):
            _ = request_id
            assert device_id == "did-1"
            assert volume == 40
            called["set_volume"] = True
            return {"status": "ok", "device_id": device_id, "transport": "miio", "request_id": "r3", "extra": {}}

    monkeypatch.setattr(v1, "_get_facade", lambda: _Facade())

    tts_out = await v1.api_v1_tts(ApiV1TtsRequest(speaker_id="did-1", text="hello"))
    stop_out = await v1.api_v1_stop(ApiV1StopRequest(speaker_id="did-1"))
    volume_out = await v1.api_v1_set_volume(ApiV1SetVolumeRequest(speaker_id="did-1", volume=40))

    assert tts_out["code"] == 0
    assert stop_out["code"] == 0
    assert volume_out["code"] == 0
    assert all(called.values())
