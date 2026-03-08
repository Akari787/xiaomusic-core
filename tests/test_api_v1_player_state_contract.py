from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from xiaomusic.api.routers import v1


def _v1_client() -> TestClient:
    app = FastAPI()
    app.include_router(v1.router)
    return TestClient(app)


def test_player_state_requires_device_id_query_param():
    client = _v1_client()
    resp = client.get("/api/v1/player/state")
    assert resp.status_code == 422


def test_player_state_success_shape(monkeypatch):
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

    monkeypatch.setattr(v1, "_get_facade", lambda: _Facade())
    client = _v1_client()
    resp = client.get("/api/v1/player/state", params={"device_id": "did-1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert set(body.keys()) == {"code", "message", "data", "request_id"}
    assert body["data"]["device_id"] == "did-1"
