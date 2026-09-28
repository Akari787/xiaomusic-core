from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from xiaomusic.api.routers import v1


def _v1_client() -> TestClient:
    app = FastAPI()
    app.include_router(v1.router)
    return TestClient(app)


def test_search_online_success(monkeypatch):
    class _XM:
        @staticmethod
        async def get_music_list_online(keyword: str, plugin: str, page: int, limit: int):
            assert keyword == "love"
            assert plugin == "all"
            assert page == 1
            assert limit == 20
            return {
                "success": True,
                "data": [
                    {"name": "Song A", "title": "Song A", "artist": "Artist A", "url": "http://example.com/a.mp3"}
                ],
                "total": 1,
            }

    monkeypatch.setattr(v1, "_get_xiaomusic", lambda: _XM())
    client = _v1_client()
    resp = client.get("/api/v1/search/online", params={"keyword": "love"})
    body = resp.json()
    assert resp.status_code == 200
    assert body["code"] == 0
    assert body["data"] == {
        "items": [{"name": "Song A", "title": "Song A", "artist": "Artist A"}],
        "total": 1,
    }


def test_search_online_creates_opaque_reference_for_supported_item(monkeypatch):
    class _PluginManager:
        @staticmethod
        def get_enabled_plugins():
            return ["qq"]

    class _XM:
        js_plugin_manager = _PluginManager()

        @staticmethod
        async def get_music_list_online(keyword: str, plugin: str, page: int, limit: int):
            return {
                "success": True,
                "data": [
                    {
                        "name": "Song A",
                        "title": "Song A",
                        "artist": "Artist A",
                        "platform": "qq",
                        "id": "media-a",
                        "url": "https://signed.example.invalid/a.mp3",
                    },
                    {
                        "name": "Song B",
                        "title": "Song B",
                        "platform": "OpenAPI-xxx",
                        "id": "media-b",
                        "url": "https://signed.example.invalid/b.mp3",
                    },
                    {
                        "name": "Song C",
                        "title": "Song C",
                        "platform": "jellyfin",
                        "id": "media-c",
                        "url": "https://signed.example.invalid/c.mp3",
                    },
                    {
                        "name": "Song D",
                        "title": "Song D",
                        "platform": "not-enabled",
                        "id": "media-d",
                        "url": "https://signed.example.invalid/d.mp3",
                    },
                ],
                "total": 2,
            }

    monkeypatch.setattr(v1, "_get_xiaomusic", lambda: _XM())
    items = _v1_client().get("/api/v1/search/online", params={"keyword": "love"}).json()["data"]["items"]
    assert items[0]["play_reference"]["source_hint"] == "online_plugin"
    assert items[0]["play_reference"]["media_id"].startswith("opm_")
    assert "media-a" not in str(items[0]["play_reference"])
    assert items[0]["play_reference"]["title"] == "Song A"
    assert items[0]["play_reference"]["query"].startswith("opq_")
    assert "play_reference" not in items[1]
    assert "play_reference" not in items[2]
    assert "play_reference" not in items[3]


def test_search_online_missing_keyword_is_structured_request_error():
    client = _v1_client()
    resp = client.get("/api/v1/search/online", params={"keyword": ""})
    body = resp.json()
    assert resp.status_code == 200
    assert body["code"] == 40001
    assert body["message"] == "keyword is required"
    assert body["data"]["error_code"] == "E_INVALID_REQUEST"
    assert body["data"]["stage"] == "request"
    assert body["data"]["field"] == "keyword"
