from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from xiaomusic.api.models import PlayListMusicObj
from xiaomusic.api.routers import playlist


def _playlist_client() -> TestClient:
    app = FastAPI()
    app.dependency_overrides[playlist.verification] = lambda: True
    app.include_router(playlist.router)
    return TestClient(app)


def test_playlist_music_obj_accepts_entity_dict_entries() -> None:
    payload = PlayListMusicObj(
        name="收藏夹",
        music_list=[
            {"entity_id": "jellyfin:item-a", "display_name": "Song A"},
            "Song B",
        ],
    )
    assert payload.music_list == [
        {"entity_id": "jellyfin:item-a", "display_name": "Song A"},
        "Song B",
    ]


def test_legacy_playlist_addmusic_route_passes_entity_dict_entries(monkeypatch) -> None:
    calls: list[tuple[str, list[object]]] = []

    class _Library:
        def play_list_add_music(self, name, music_list):
            calls.append((name, music_list))
            return True

    monkeypatch.setattr(playlist, "xiaomusic", type("X", (), {"music_library": _Library()})())
    client = _playlist_client()
    resp = client.post(
        "/playlistaddmusic",
        json={
            "name": "收藏夹",
            "music_list": [
                {"entity_id": "jellyfin:item-a", "display_name": "Song A"},
                "Song B",
            ],
        },
    )

    assert resp.status_code == 200
    assert calls == [
        (
            "收藏夹",
            [{"entity_id": "jellyfin:item-a", "display_name": "Song A"}, "Song B"],
        )
    ]


def test_playlistmusics_structured_returns_identity_items(monkeypatch) -> None:
    class _Library:
        def play_list_musics(self, name):
            assert name == "收藏夹"
            return "OK", ["Song A"]

        def play_list_items(self, name):
            assert name == "收藏夹"
            return "OK", [
                {
                    "playlist_item_id": "",
                    "entity_id": "jellyfin:item-a",
                    "display_name": "Song A",
                    "title": "Song A",
                }
            ]

    monkeypatch.setattr(playlist, "xiaomusic", type("X", (), {"music_library": _Library()})())
    client = _playlist_client()
    resp = client.get("/playlistmusics", params={"name": "收藏夹", "structured": "true"})

    assert resp.status_code == 200
    assert resp.json() == {
        "ret": "OK",
        "musics": ["Song A"],
        "items": [
            {
                "playlist_item_id": "",
                "entity_id": "jellyfin:item-a",
                "display_name": "Song A",
                "title": "Song A",
            }
        ],
        "legacy": False,
    }
