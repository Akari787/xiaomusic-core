from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from xiaomusic.api.routers import v1


def _v1_client() -> TestClient:
    app = FastAPI()
    app.include_router(v1.router)
    return TestClient(app)


def _snapshot(**overrides):
    data = {
        "device_id": "did-1",
        "revision": 1,
        "play_session_id": "sess-1",
        "transport_state": "idle",
        "track": None,
        "context": None,
        "position_ms": 0,
        "duration_ms": 0,
        "snapshot_at_ms": 1710000000000,
    }
    data.update(overrides)
    return data


def test_player_state_requires_device_id_query_param():
    client = _v1_client()
    resp = client.get("/api/v1/player/state")
    assert resp.status_code == 422


def test_player_state_success_shape(monkeypatch):
    class _Facade:
        async def build_player_state_snapshot(self, device_id: str):
            return _snapshot(device_id=device_id)

    monkeypatch.setattr(v1, "_get_facade", lambda: _Facade())
    client = _v1_client()
    resp = client.get("/api/v1/player/state", params={"device_id": "did-1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert set(body.keys()) == {"code", "message", "data", "request_id"}
    assert body["data"] == {
        "device_id": "did-1",
        "revision": 1,
        "play_session_id": "sess-1",
        "transport_state": "idle",
        "track": None,
        "context": None,
        "position_ms": 0,
        "duration_ms": 0,
        "snapshot_at_ms": 1710000000000,
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
    class _Facade:
        async def build_player_state_snapshot(self, device_id: str):
            return _snapshot(
                device_id=device_id,
                revision=3,
                play_session_id="sess-3",
                transport_state="playing",
                track={
                    "id": "abc123",
                    "title": "song-a",
                    "artist": "artist-a",
                    "album": "album-a",
                    "source": "jellyfin",
                },
                context={
                    "id": "OTS",
                    "name": "OTS",
                    "current_index": 3,
                },
                position_ms=12000,
                duration_ms=180000,
                snapshot_at_ms=1710000001234,
            )

    monkeypatch.setattr(v1, "_get_facade", lambda: _Facade())
    client = _v1_client()
    resp = client.get("/api/v1/player/state", params={"device_id": "did-1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == {
        "device_id": "did-1",
        "revision": 3,
        "play_session_id": "sess-3",
        "transport_state": "playing",
        "track": {
            "id": "abc123",
            "title": "song-a",
            "artist": "artist-a",
            "album": "album-a",
            "source": "jellyfin",
        },
        "context": {
            "id": "OTS",
            "name": "OTS",
            "current_index": 3,
        },
        "position_ms": 12000,
        "duration_ms": 180000,
        "snapshot_at_ms": 1710000001234,
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
    class _Facade:
        def __init__(self):
            self._calls = 0

        async def build_player_state_snapshot(self, device_id: str):
            self._calls += 1
            return _snapshot(
                device_id=device_id,
                revision=1,
                play_session_id="sess-stable",
                transport_state="playing",
                track={"id": "stable-track-id", "title": "stable-song"},
                context={"id": "test-list", "name": "test-list", "current_index": 0},
                position_ms=self._calls * 10000,
                duration_ms=180000,
            )

    facade = _Facade()
    monkeypatch.setattr(v1, "_get_facade", lambda: facade)
    client = _v1_client()
    resp1 = client.get("/api/v1/player/state", params={"device_id": "did-1"})
    resp2 = client.get("/api/v1/player/state", params={"device_id": "did-1"})
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert resp1.json()["data"]["current_track_id"] == "stable-track-id"
    assert resp2.json()["data"]["current_track_id"] == "stable-track-id"


def test_player_state_track_id_changes_on_next(monkeypatch):
    class _Facade:
        def __init__(self):
            self._calls = 0

        async def build_player_state_snapshot(self, device_id: str):
            idx = self._calls
            self._calls += 1
            return _snapshot(
                device_id=device_id,
                revision=idx + 1,
                play_session_id=f"sess-{idx + 1}",
                transport_state="playing",
                track={"id": f"track-{idx + 1}", "title": f"song-{idx}"},
                context={"id": "test-list", "name": "test-list", "current_index": idx},
                duration_ms=180000,
            )

    facade = _Facade()
    monkeypatch.setattr(v1, "_get_facade", lambda: facade)
    client = _v1_client()
    resp1 = client.get("/api/v1/player/state", params={"device_id": "did-1"})
    resp2 = client.get("/api/v1/player/state", params={"device_id": "did-1"})
    assert resp1.json()["data"]["current_track_id"] == "track-1"
    assert resp2.json()["data"]["current_track_id"] == "track-2"
    assert resp1.json()["data"]["current_track_id"] != resp2.json()["data"]["current_track_id"]


def test_player_state_context_fields(monkeypatch):
    class _Facade:
        async def build_player_state_snapshot(self, device_id: str):
            return _snapshot(
                device_id=device_id,
                transport_state="playing",
                track={"id": "ctx-track-1", "title": "context-song"},
                context={"id": "my-playlist", "name": "我的歌单", "current_index": 2},
                position_ms=5000,
                duration_ms=200000,
            )

    monkeypatch.setattr(v1, "_get_facade", lambda: _Facade())
    client = _v1_client()
    resp = client.get("/api/v1/player/state", params={"device_id": "did-1"})
    data = resp.json()["data"]
    assert data["context_type"] == "playlist"
    assert data["context_id"] == "my-playlist"
    assert data["context_name"] == "我的歌单"
    assert data["current_index"] == 2


def test_player_state_no_context_returns_null(monkeypatch):
    class _Facade:
        async def build_player_state_snapshot(self, device_id: str):
            return _snapshot(
                device_id=device_id,
                transport_state="playing",
                track={"id": "no-ctx-track", "title": "no-context-song"},
                context=None,
                position_ms=3000,
                duration_ms=150000,
            )

    monkeypatch.setattr(v1, "_get_facade", lambda: _Facade())
    client = _v1_client()
    resp = client.get("/api/v1/player/state", params={"device_id": "did-1"})
    data = resp.json()["data"]
    assert data["current_index"] is None
    assert data["context_type"] is None
    assert data["context_id"] is None
    assert data["context_name"] is None
    assert data["current_track_id"] == "no-ctx-track"


def test_player_state_route_normalizes_jellyfin_track_source(monkeypatch):
    class _DevicePlayer:
        _play_session_id = 42
        _current_index = 3
        _play_list = ["Song A", "Song B", "Song C", "Jellyfin Song"]
        _last_cmd = "play"
        _next_timer = None
        _play_failed_cnt = 0
        _degraded = False

        def get_cur_music(self):
            return "Jellyfin Song"

    class _XM:
        def __init__(self):
            self.device_manager = SimpleNamespace(devices={"did-jf": _DevicePlayer()})
            self.config = SimpleNamespace(
                music_list_json='[{"name": "Jellyfin Favorites", "source": "jellyfin", "musics": []}]'
            )
            self.music_library = SimpleNamespace(all_music={}, is_web_music=lambda _name: True)

        def did_exist(self, did: str) -> bool:
            return did == "did-jf"

        def isplaying(self, did: str) -> bool:
            return True

        def get_offset_duration(self, did: str):
            return (12, 180)

        async def get_player_status(self, did: str):
            return {
                "status": 1,
                "play_song_detail": {
                    "audio_name": "Jellyfin Song",
                    "artist": "Artist A",
                    "album": "Album A",
                    "source": "xm_unknown_device_value",
                    "position": 12000,
                    "duration": 180000,
                },
            }

        def get_cur_play_list(self, did: str) -> str:
            return "Jellyfin Favorites"

    v1._facade = None
    monkeypatch.setattr(v1, "_get_xiaomusic", lambda: _XM())
    client = _v1_client()
    resp = client.get("/api/v1/player/state", params={"device_id": "did-jf"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["track"]["source"] == "jellyfin"


def test_player_state_unknown_error_has_non_null_stage(monkeypatch):
    class _Facade:
        async def build_player_state_snapshot(self, device_id: str):
            _ = device_id
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
