from __future__ import annotations

import pytest

from xiaomusic.api.models import (
    ControlRequest,
    VolumeRequest,
)
from xiaomusic.api.routers import v1


@pytest.mark.asyncio
async def test_api_v1_all_controls_call_facade(monkeypatch):
    called = {
        "pause": False,
        "resume": False,
        "stop": False,
        "volume": False,
        "probe": False,
    }

    class _Facade:
        async def pause(self, device_id: str, request_id: str | None = None):
            _ = request_id
            called["pause"] = True
            return {"status": "accepted", "action": "pause", "device_id": device_id, "transport": "mina", "request_id": "r0", "extra": {}}

        async def resume(self, device_id: str, request_id: str | None = None):
            _ = request_id
            called["resume"] = True
            return {"status": "accepted", "action": "resume", "device_id": device_id, "transport": "mina", "request_id": "r00", "extra": {}}

        async def stop(self, device_id: str, request_id: str | None = None):
            _ = request_id
            assert device_id == "did-1"
            called["stop"] = True
            return {
                "status": "accepted",
                "action": "stop",
                "device_id": device_id,
                "transport": "mina",
                "request_id": "r2",
                "extra": {},
            }

        async def set_volume(self, device_id: str, volume: int, request_id: str | None = None):
            _ = request_id
            assert device_id == "did-1"
            assert volume == 40
            called["volume"] = True
            return {"status": "ok", "device_id": device_id, "transport": "mina", "request_id": "r3", "extra": {}}

        async def probe(self, device_id: str, request_id: str | None = None):
            _ = request_id
            called["probe"] = True
            return {
                "status": "ok",
                "device_id": device_id,
                "transport": "mina",
                "request_id": "r4",
                "reachable": True,
                "extra": {"reachability": {"local_reachable": True}},
            }

    monkeypatch.setattr(v1, "_get_facade", lambda: _Facade())

    pause_out = await v1.api_v1_control_pause(ControlRequest(device_id="did-1"))
    resume_out = await v1.api_v1_control_resume(ControlRequest(device_id="did-1"))
    stop_out = await v1.api_v1_control_stop(ControlRequest(device_id="did-1"))
    volume_out = await v1.api_v1_control_volume(VolumeRequest(device_id="did-1", volume=40))
    probe_out = await v1.api_v1_control_probe(ControlRequest(device_id="did-1"))

    assert pause_out["code"] == 0
    assert pause_out["data"]["status"] == "accepted"
    assert pause_out["data"]["action"] == "pause"
    assert pause_out["data"]["device_id"] == "did-1"
    assert pause_out["data"]["transport"] == "mina"
    assert pause_out["request_id"]
    assert resume_out["code"] == 0
    assert resume_out["data"]["status"] == "accepted"
    assert resume_out["data"]["action"] == "resume"
    assert resume_out["data"]["device_id"] == "did-1"
    assert resume_out["data"]["transport"] == "mina"
    assert resume_out["request_id"]
    assert stop_out["code"] == 0
    assert stop_out["data"]["status"] == "accepted"
    assert stop_out["data"]["action"] == "stop"
    assert stop_out["data"]["device_id"] == "did-1"
    assert stop_out["data"]["transport"] == "mina"
    assert stop_out["request_id"]
    assert volume_out["code"] == 0
    assert probe_out["code"] == 0
    assert all(called.values())
