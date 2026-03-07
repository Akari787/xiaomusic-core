from __future__ import annotations

import pytest

from xiaomusic.api.models import (
    ControlRequest,
    TtsRequest,
    VolumeRequest,
)
from xiaomusic.api.routers import v1


@pytest.mark.asyncio
async def test_api_v1_all_controls_call_facade(monkeypatch):
    called = {
        "pause": False,
        "resume": False,
        "tts": False,
        "stop": False,
        "volume": False,
        "probe": False,
    }

    class _Facade:
        async def pause(self, device_id: str, request_id: str | None = None):
            _ = request_id
            called["pause"] = True
            return {"status": "paused", "device_id": device_id, "transport": "miio", "request_id": "r0", "extra": {}}

        async def resume(self, device_id: str, request_id: str | None = None):
            _ = request_id
            called["resume"] = True
            return {"status": "resumed", "device_id": device_id, "transport": "miio", "request_id": "r00", "extra": {}}

        async def tts(self, device_id: str, text: str, request_id: str | None = None):
            _ = request_id
            assert device_id == "did-1"
            assert text
            called["tts"] = True
            return {"status": "ok", "device_id": device_id, "transport": "miio", "request_id": "r1", "extra": {}}

        async def stop(self, device_id: str, request_id: str | None = None):
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

        async def set_volume(self, device_id: str, volume: int, request_id: str | None = None):
            _ = request_id
            assert device_id == "did-1"
            assert volume == 40
            called["volume"] = True
            return {"status": "ok", "device_id": device_id, "transport": "miio", "request_id": "r3", "extra": {}}

        async def probe(self, device_id: str, request_id: str | None = None):
            _ = request_id
            called["probe"] = True
            return {
                "status": "ok",
                "device_id": device_id,
                "transport": "miio",
                "request_id": "r4",
                "reachable": True,
                "extra": {"reachability": {"local_reachable": True}},
            }

    monkeypatch.setattr(v1, "_get_facade", lambda: _Facade())

    pause_out = await v1.api_v1_control_pause(ControlRequest(device_id="did-1"))
    resume_out = await v1.api_v1_control_resume(ControlRequest(device_id="did-1"))
    tts_out = await v1.api_v1_control_tts(TtsRequest(device_id="did-1", text="hello"))
    stop_out = await v1.api_v1_control_stop(ControlRequest(device_id="did-1"))
    volume_out = await v1.api_v1_control_volume(VolumeRequest(device_id="did-1", volume=40))
    probe_out = await v1.api_v1_control_probe(ControlRequest(device_id="did-1"))

    assert pause_out["code"] == 0
    assert resume_out["code"] == 0
    assert tts_out["code"] == 0
    assert stop_out["code"] == 0
    assert volume_out["code"] == 0
    assert probe_out["code"] == 0
    assert all(called.values())
