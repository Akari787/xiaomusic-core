from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from xiaomusic.api.dependencies import verification
from xiaomusic.api.routers import device, v1


def _device_client() -> TestClient:
    app = FastAPI()
    app.include_router(device.router)
    app.dependency_overrides[verification] = lambda: True
    return TestClient(app)


def _v1_client() -> TestClient:
    app = FastAPI()
    app.include_router(v1.router)
    return TestClient(app)


def test_removed_device_wrappers_return_404() -> None:
    client = _device_client()
    assert client.get("/getplayerstatus").status_code == 404
    assert client.post("/setvolume", json={"did": "did-1", "volume": 30}).status_code == 404
    assert client.get("/playtts", params={"did": "did-1", "text": "hello"}).status_code == 404
    assert client.post("/device/stop", json={"did": "did-1"}).status_code == 404


def test_v1_routes_still_available_after_wrapper_removal(monkeypatch) -> None:
    class _Facade:
        async def player_state(self, device_id: str, request_id: str | None = None):
            _ = request_id
            return {
                "device_id": device_id,
                "is_playing": False,
                "cur_music": "",
                "offset": 0,
                "duration": 0,
                "request_id": "rid-state",
            }

        async def stop(self, device_id: str, request_id: str | None = None):
            _ = request_id
            return {
                "status": "stopped",
                "device_id": device_id,
                "transport": "mina",
                "request_id": "rid-stop",
                "extra": {"dispatch": {}},
            }

        async def tts(self, device_id: str, text: str, request_id: str | None = None):
            _ = (text, request_id)
            return {
                "status": "ok",
                "device_id": device_id,
                "transport": "mina",
                "request_id": "rid-tts",
                "extra": {"dispatch": {}},
            }

        async def set_volume(self, device_id: str, volume: int, request_id: str | None = None):
            _ = request_id
            return {
                "status": "ok",
                "device_id": device_id,
                "transport": "mina",
                "request_id": "rid-volume",
                "extra": {"volume": volume, "dispatch": {}},
            }

    monkeypatch.setattr(v1, "_get_facade", lambda: _Facade())
    client = _v1_client()

    assert client.get("/api/v1/player/state", params={"device_id": "did-1"}).status_code == 200
    assert client.post("/api/v1/control/stop", json={"device_id": "did-1"}).status_code == 200
    assert client.post("/api/v1/control/tts", json={"device_id": "did-1", "text": "hello"}).status_code == 200
    assert client.post("/api/v1/control/volume", json={"device_id": "did-1", "volume": 30}).status_code == 200
