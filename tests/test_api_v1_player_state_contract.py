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
    assert body["data"] == {
        "device_id": "did-1",
        "is_playing": False,
        "cur_music": "",
        "offset": 0,
        "duration": 0,
    }


def test_player_state_omits_non_contract_extended_fields(monkeypatch):
    class _Facade:
        async def player_state(self, device_id: str, request_id: str | None = None):
            _ = request_id
            return {
                "device_id": device_id,
                "is_playing": True,
                "cur_music": "song-a",
                "offset": 12,
                "duration": 180,
                "current_track_title": "song-a",
                "current_track_id": "track-1",
                "current_track_duration": 180,
                "play_mode": "random",
                "context_type": "playlist",
                "context_id": "OTS",
                "context_name": "OTS",
                "queue_supported": True,
                "current_index": 3,
                "queue_length": 99,
                "has_next": True,
                "has_previous": True,
                "request_id": "rid-state",
            }

    monkeypatch.setattr(v1, "_get_facade", lambda: _Facade())
    client = _v1_client()
    resp = client.get("/api/v1/player/state", params={"device_id": "did-1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == {
        "device_id": "did-1",
        "is_playing": True,
        "cur_music": "song-a",
        "offset": 12,
        "duration": 180,
    }
    assert "current_track_title" not in body["data"]
    assert "current_track_id" not in body["data"]
    assert "current_track_duration" not in body["data"]
    assert "play_mode" not in body["data"]
    assert "context_type" not in body["data"]
    assert "queue_supported" not in body["data"]


def test_player_state_unknown_error_has_non_null_stage(monkeypatch):
    class _Facade:
        async def player_state(self, device_id: str, request_id: str | None = None):
            _ = (device_id, request_id)
            raise RuntimeError("boom")

    monkeypatch.setattr(v1, "_get_facade", lambda: _Facade())
    client = _v1_client()
    resp = client.get("/api/v1/player/state", params={"device_id": "did-1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 10000
    assert body["message"] == "player state query failed"
    assert body["data"]["error_code"] == "E_PLAYER_STATE_FAILED"
    assert body["data"]["stage"] == "system"
