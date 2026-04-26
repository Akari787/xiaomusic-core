from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from xiaomusic.api.routers import music
from xiaomusic.api import websocket as ws_router


class _Facade:
    async def build_player_state_snapshot(self, device_id: str):
        assert device_id == "did-1"
        return {
            "device_id": device_id,
            "revision": 2,
            "play_session_id": "sess-2",
            "transport_state": "playing",
            "track": {
                "id": "playlist-item-2",
                "entity_id": "jellyfin:item-2",
                "title": "Song B",
            },
            "context": {"id": "中文", "name": "中文", "current_index": 1},
            "position_ms": 15000,
            "duration_ms": 180000,
            "volume": 21,
            "snapshot_at_ms": 1710000000000,
        }


class _XM:
    def did_exist(self, did: str) -> bool:
        return did == "did-1"


def test_legacy_playingmusic_includes_identity_fields(monkeypatch) -> None:
    monkeypatch.setattr(music, "xiaomusic", _XM())
    monkeypatch.setattr("xiaomusic.playback.facade.PlaybackFacade", lambda _xm: _Facade())

    app = FastAPI()
    app.dependency_overrides[music.verification] = lambda: True
    app.include_router(music.router)
    client = TestClient(app)

    resp = client.get("/playingmusic", params={"did": "did-1"})
    assert resp.status_code == 200
    assert resp.json() == {
        "ret": "OK",
        "is_playing": True,
        "cur_music": "Song B",
        "cur_playlist": "中文",
        "offset": 15.0,
        "duration": 180.0,
        "entity_id": "jellyfin:item-2",
        "playlist_item_id": "playlist-item-2",
        "current_index": 1,
        "context_id": "中文",
    }


def test_ws_playingmusic_pushes_identity_fields(monkeypatch) -> None:
    monkeypatch.setattr(ws_router, "xiaomusic", _XM())
    monkeypatch.setattr("xiaomusic.playback.facade.PlaybackFacade", lambda _xm: _Facade())

    app = FastAPI()
    app.dependency_overrides[ws_router.verification] = lambda: True
    app.include_router(ws_router.router)
    client = TestClient(app)

    token_resp = client.get("/generate_ws_token", params={"did": "did-1"})
    token = token_resp.json()["token"]

    with client.websocket_connect(f"/ws/playingmusic?token={token}") as websocket:
        payload = json.loads(websocket.receive_text())

    assert payload == {
        "ret": "OK",
        "is_playing": True,
        "cur_music": "Song B",
        "cur_playlist": "中文",
        "offset": 15.0,
        "duration": 180.0,
        "entity_id": "jellyfin:item-2",
        "playlist_item_id": "playlist-item-2",
        "current_index": 1,
        "context_id": "中文",
    }
