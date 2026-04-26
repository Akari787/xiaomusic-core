from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from xiaomusic.api.routers import music


def _music_client() -> TestClient:
    app = FastAPI()
    app.dependency_overrides[music.verification] = lambda: True
    app.include_router(music.router)
    return TestClient(app)


def test_legacy_musicinfo_accepts_entity_id(monkeypatch) -> None:
    class _Library:
        @staticmethod
        def get_legacy_name_for_entity(entity_id: str) -> str:
            assert entity_id == "jellyfin:item-a"
            return "Song A"

        @staticmethod
        async def get_music_url_by_entity(entity_id: str):
            assert entity_id == "jellyfin:item-a"
            return "http://127.0.0.1/song-a.mp3", None

        @staticmethod
        async def get_music_tags_by_entity(entity_id: str):
            assert entity_id == "jellyfin:item-a"
            return {"duration": 12}

    monkeypatch.setattr(music, "xiaomusic", type("X", (), {"music_library": _Library()})())
    client = _music_client()
    resp = client.get("/musicinfo", params={"entity_id": "jellyfin:item-a", "musictag": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "ret": "OK",
        "name": "Song A",
        "entity_id": "jellyfin:item-a",
        "url": "http://127.0.0.1/song-a.mp3",
        "tags": {"duration": 12},
    }


def test_legacy_musicinfos_accepts_entity_id_list(monkeypatch) -> None:
    class _Library:
        @staticmethod
        def get_legacy_name_for_entity(entity_id: str) -> str:
            mapping = {"jellyfin:item-a": "Song A", "jellyfin:item-b": "Song B"}
            return mapping.get(entity_id, "")

        @staticmethod
        async def get_music_url(name: str):
            return f"http://127.0.0.1/{name}.mp3", None

        @staticmethod
        async def get_music_url_by_entity(entity_id: str):
            mapping = {
                "jellyfin:item-a": "http://127.0.0.1/entity-a.mp3",
                "jellyfin:item-b": "http://127.0.0.1/entity-b.mp3",
            }
            return mapping[entity_id], None

        @staticmethod
        async def get_music_tags(name: str):
            return {"title": name}

        @staticmethod
        async def get_music_tags_by_entity(entity_id: str):
            return {"entity_id": entity_id}

        @staticmethod
        def get_playlist_items():
            return {"中文": [{"item_id": "item-1", "entity_id": "entity-1", "display_name": "Song A"}]}

        @staticmethod
        def get_music_list():
            return {"中文": ["Song A"]}

    monkeypatch.setattr(music, "xiaomusic", type("X", (), {"music_library": _Library()})())
    client = _music_client()
    resp = client.get(
        "/musicinfos",
        params=[("entity_id", "jellyfin:item-a"), ("entity_id", "jellyfin:item-b"), ("musictag", "true")],
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == [
        {
            "name": "Song A",
            "entity_id": "jellyfin:item-a",
            "url": "http://127.0.0.1/entity-a.mp3",
            "tags": {"entity_id": "jellyfin:item-a"},
        },
        {
            "name": "Song B",
            "entity_id": "jellyfin:item-b",
            "url": "http://127.0.0.1/entity-b.mp3",
            "tags": {"entity_id": "jellyfin:item-b"},
        },
    ]


def test_legacy_musiclist_structured_returns_playlist_items(monkeypatch) -> None:
    class _Library:
        @staticmethod
        def get_playlist_items():
            return {"中文": [{"item_id": "item-1", "entity_id": "entity-1", "display_name": "Song A"}]}

        @staticmethod
        def get_music_list():
            return {"中文": ["Song A"]}

    monkeypatch.setattr(music, "xiaomusic", type("X", (), {"music_library": _Library()})())
    client = _music_client()
    resp = client.get("/musiclist", params={"structured": "true"})
    assert resp.status_code == 200
    assert resp.json() == {
        "legacy": False,
        "playlists": {
            "中文": [{"item_id": "item-1", "entity_id": "entity-1", "display_name": "Song A"}]
        },
    }


def test_setmusictag_prefers_entity_route(monkeypatch) -> None:
    calls = []

    class _Library:
        def set_music_tag_by_entity(self, entity_id, info, playlist_name=""):
            calls.append((entity_id, info.musicname, playlist_name))
            return "OK"

    monkeypatch.setattr(music, "xiaomusic", type("X", (), {"music_library": _Library()})())
    client = _music_client()
    resp = client.post(
        "/setmusictag",
        json={"musicname": "Song A", "entity_id": "jellyfin:item-a", "title": "Song A+"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ret": "OK"}
    assert calls == [("jellyfin:item-a", "Song A", "")]
