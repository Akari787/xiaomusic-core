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
