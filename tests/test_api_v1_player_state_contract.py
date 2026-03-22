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
                "current_track_id": "",
                "current_index": None,
                "context_type": None,
                "context_id": None,
                "context_name": None,
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
        "current_track_id": "",
        "current_index": None,
        "context_type": None,
        "context_id": None,
        "context_name": None,
    }


def test_player_state_returns_contract_extended_fields(monkeypatch):
    """验证 player/state 返回正式契约扩展字段"""

    class _Facade:
        async def player_state(self, device_id: str, request_id: str | None = None):
            _ = request_id
            return {
                "device_id": device_id,
                "is_playing": True,
                "cur_music": "song-a",
                "offset": 12,
                "duration": 180,
                "current_track_id": "abc123",
                "current_index": 3,
                "context_type": "playlist",
                "context_id": "OTS",
                "context_name": "OTS",
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
        "current_track_id": "abc123",
        "current_index": 3,
        "context_type": "playlist",
        "context_id": "OTS",
        "context_name": "OTS",
    }


def test_player_state_track_id_stability(monkeypatch):
    """验证同一首歌连续读取时 current_track_id 不变"""
    call_count = [0]

    class _Facade:
        async def player_state(self, device_id: str, request_id: str | None = None):
            _ = request_id
            call_count[0] += 1
            return {
                "device_id": device_id,
                "is_playing": True,
                "cur_music": "stable-song",
                "offset": call_count[0] * 10,
                "duration": 180,
                "current_track_id": "stable-track-id",
                "current_index": 0,
                "context_type": "playlist",
                "context_id": "test-list",
                "context_name": "test-list",
                "request_id": f"rid-{call_count[0]}",
            }

    monkeypatch.setattr(v1, "_get_facade", lambda: _Facade())
    client = _v1_client()

    # 连续两次读取
    resp1 = client.get("/api/v1/player/state", params={"device_id": "did-1"})
    resp2 = client.get("/api/v1/player/state", params={"device_id": "did-1"})

    assert resp1.status_code == 200
    assert resp2.status_code == 200

    # current_track_id 应该稳定不变
    assert resp1.json()["data"]["current_track_id"] == "stable-track-id"
    assert resp2.json()["data"]["current_track_id"] == "stable-track-id"


def test_player_state_track_id_changes_on_next(monkeypatch):
    """验证切歌后 current_track_id 必须变化"""
    track_ids = ["track-1", "track-2"]
    call_count = [0]

    class _Facade:
        async def player_state(self, device_id: str, request_id: str | None = None):
            _ = request_id
            idx = call_count[0]
            call_count[0] += 1
            return {
                "device_id": device_id,
                "is_playing": True,
                "cur_music": f"song-{idx}",
                "offset": 0,
                "duration": 180,
                "current_track_id": track_ids[idx % len(track_ids)],
                "current_index": idx,
                "context_type": "playlist",
                "context_id": "test-list",
                "context_name": "test-list",
                "request_id": f"rid-{idx}",
            }

    monkeypatch.setattr(v1, "_get_facade", lambda: _Facade())
    client = _v1_client()

    # 第一次读取
    resp1 = client.get("/api/v1/player/state", params={"device_id": "did-1"})
    # 第二次读取（模拟切歌）
    resp2 = client.get("/api/v1/player/state", params={"device_id": "did-1"})

    assert resp1.json()["data"]["current_track_id"] == "track-1"
    assert resp2.json()["data"]["current_track_id"] == "track-2"
    assert (
        resp1.json()["data"]["current_track_id"]
        != resp2.json()["data"]["current_track_id"]
    )


def test_player_state_context_fields(monkeypatch):
    """验证存在上下文时返回 context_* 字段"""

    class _Facade:
        async def player_state(self, device_id: str, request_id: str | None = None):
            _ = request_id
            return {
                "device_id": device_id,
                "is_playing": True,
                "cur_music": "context-song",
                "offset": 5,
                "duration": 200,
                "current_track_id": "ctx-track-1",
                "current_index": 2,
                "context_type": "playlist",
                "context_id": "my-playlist",
                "context_name": "我的歌单",
                "request_id": "rid-ctx",
            }

    monkeypatch.setattr(v1, "_get_facade", lambda: _Facade())
    client = _v1_client()
    resp = client.get("/api/v1/player/state", params={"device_id": "did-1"})

    data = resp.json()["data"]
    assert data["context_type"] == "playlist"
    assert data["context_id"] == "my-playlist"
    assert data["context_name"] == "我的歌单"
    assert data["current_index"] == 2


def test_player_state_no_context_returns_null(monkeypatch):
    """验证无明确上下文时 context_* 返回 null"""

    class _Facade:
        async def player_state(self, device_id: str, request_id: str | None = None):
            _ = request_id
            return {
                "device_id": device_id,
                "is_playing": True,
                "cur_music": "no-context-song",
                "offset": 3,
                "duration": 150,
                "current_track_id": "no-ctx-track",
                "current_index": None,
                "context_type": None,
                "context_id": None,
                "context_name": None,
                "request_id": "rid-no-ctx",
            }

    monkeypatch.setattr(v1, "_get_facade", lambda: _Facade())
    client = _v1_client()
    resp = client.get("/api/v1/player/state", params={"device_id": "did-1"})

    data = resp.json()["data"]
    assert data["current_index"] is None
    assert data["context_type"] is None
    assert data["context_id"] is None
    assert data["context_name"] is None
    # 即使无上下文，也应提供稳定的 current_track_id
    assert data["current_track_id"] == "no-ctx-track"


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
