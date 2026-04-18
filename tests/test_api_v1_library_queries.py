from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from xiaomusic.api.routers import v1
from xiaomusic.playback.facade import build_track_id


def _v1_client() -> TestClient:
    app = FastAPI()
    app.include_router(v1.router)
    return TestClient(app)


def test_library_playlists_success(monkeypatch):
    class _Library:
        all_music = {
            "song-a": "/music/song-a.flac",
            "song-b": "/music/song-b.flac",
        }

        @staticmethod
        def get_music_list():
            return {"收藏": ["song-a", "song-b"]}

    class _XM:
        music_library = _Library()

    monkeypatch.setattr(v1, "_get_xiaomusic", lambda: _XM())
    client = _v1_client()
    resp = client.get("/api/v1/library/playlists")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"] == {
        "playlists": {
            "收藏": [
                {"id": build_track_id("收藏", 0, "song-a", identity_hint="/music/song-a.flac"), "title": "song-a"},
                {"id": build_track_id("收藏", 1, "song-b", identity_hint="/music/song-b.flac"), "title": "song-b"},
            ]
        }
    }


def test_library_music_info_success(monkeypatch):
    class _Library:
        @staticmethod
        async def get_music_url(name: str):
            assert name == "song-a"
            return "http://127.0.0.1/song-a.mp3", None

        @staticmethod
        async def get_music_tags(name: str):
            assert name == "song-a"
            return {"duration": 123.5}

    class _XM:
        music_library = _Library()

    monkeypatch.setattr(v1, "_get_xiaomusic", lambda: _XM())
    client = _v1_client()
    resp = client.get("/api/v1/library/music-info", params={"name": "song-a"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"] == {
        "name": "song-a",
        "url": "http://127.0.0.1/song-a.mp3",
        "duration_seconds": 123.5,
    }


def test_library_music_info_missing_name_is_structured_request_error():
    client = _v1_client()
    resp = client.get("/api/v1/library/music-info", params={"name": ""})
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 40001
    assert body["message"] == "name is required"
    assert body["data"]["error_code"] == "E_INVALID_REQUEST"
    assert body["data"]["stage"] == "request"
    assert body["data"]["field"] == "name"
