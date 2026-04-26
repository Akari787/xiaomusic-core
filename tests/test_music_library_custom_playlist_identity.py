from __future__ import annotations

import json
import logging
from types import SimpleNamespace

from xiaomusic.music_library import MusicLibrary


class _Config(SimpleNamespace):
    def get_exclude_dirs_set(self):
        return set()

    def get_ignore_tag_dirs(self):
        return []

    def get_public_base_url(self):
        return "http://127.0.0.1:58090"


def test_custom_playlist_add_music_stores_entity_reference(monkeypatch):
    monkeypatch.setattr(
        "xiaomusic.music_library.traverse_music_directory",
        lambda *args, **kwargs: {},
    )

    config = _Config(
        music_path="D:/Music",
        music_path_depth=3,
        download_path="D:/Music/Downloads",
        recently_added_playlist_len=20,
        music_list_json=json.dumps(
            [
                {
                    "name": "日语",
                    "source": "jellyfin",
                    "source_playlist_id": "pl-jp",
                    "musics": [
                        {
                            "entity_id": "jellyfin:58ccd8",
                            "source": "jellyfin",
                            "source_item_id": "58ccd8",
                            "name": "Ana-Lia-[58ccd8]",
                            "canonical_name": "Ana-Lia",
                            "url": "http://jf/Audio/58ccd8/stream.mp3?api_key=2",
                            "type": "music",
                        }
                    ],
                }
            ],
            ensure_ascii=False,
        ),
        custom_play_list_json=json.dumps({"我的收藏": []}, ensure_ascii=False),
        picture_cache_path="",
    )

    library = MusicLibrary(config=config, log=logging.getLogger("test"), event_bus=None)
    library.gen_all_music_list()

    assert library.play_list_add_music("我的收藏", ["Ana-Lia"])
    custom = library.get_custom_play_list()
    assert custom["我的收藏"] == [
        {"entity_id": "jellyfin:58ccd8", "display_name": "Ana-Lia"}
    ]

    status, songs = library.play_list_musics("我的收藏")
    assert status == "OK"
    assert songs == ["Ana-Lia"]
    assert library.get_music_list()["我的收藏"] == ["Ana-Lia"]

    playlist_items = library.get_playlist_items("我的收藏")
    assert [item["entity_id"] for item in playlist_items] == ["jellyfin:58ccd8"]


def test_favorites_playlist_allows_entity_backed_custom_entries(monkeypatch):
    monkeypatch.setattr(
        "xiaomusic.music_library.traverse_music_directory",
        lambda *args, **kwargs: {},
    )

    config = _Config(
        music_path="D:/Music",
        music_path_depth=3,
        download_path="D:/Music/Downloads",
        recently_added_playlist_len=20,
        music_list_json=json.dumps(
            [
                {
                    "name": "日语",
                    "source": "jellyfin",
                    "source_playlist_id": "pl-jp",
                    "musics": [
                        {
                            "entity_id": "jellyfin:58ccd8",
                            "source": "jellyfin",
                            "source_item_id": "58ccd8",
                            "name": "Ana-Lia-[58ccd8]",
                            "canonical_name": "Ana-Lia",
                            "url": "http://jf/Audio/58ccd8/stream.mp3?api_key=2",
                            "type": "music",
                        }
                    ],
                }
            ],
            ensure_ascii=False,
        ),
        custom_play_list_json=json.dumps({}, ensure_ascii=False),
        picture_cache_path="",
    )

    library = MusicLibrary(config=config, log=logging.getLogger("test"), event_bus=None)
    library.gen_all_music_list()

    assert library.play_list_add_music("收藏", ["jellyfin:58ccd8"])

    custom = library.get_custom_play_list()
    assert custom["收藏"] == [{"entity_id": "jellyfin:58ccd8", "display_name": "Ana-Lia"}]
    assert library.get_music_list()["收藏"] == ["Ana-Lia"]
    assert [item["entity_id"] for item in library.get_playlist_items("收藏")] == ["jellyfin:58ccd8"]


def test_custom_playlist_add_music_prefers_playlist_item_id_when_titles_collide(monkeypatch):
    monkeypatch.setattr(
        "xiaomusic.music_library.traverse_music_directory",
        lambda *args, **kwargs: {},
    )

    config = _Config(
        music_path="D:/Music",
        music_path_depth=3,
        download_path="D:/Music/Downloads",
        recently_added_playlist_len=20,
        music_list_json=json.dumps(
            [
                {
                    "name": "日语",
                    "source": "jellyfin",
                    "source_playlist_id": "pl-jp",
                    "musics": [
                        {
                            "entity_id": "jellyfin:item-a",
                            "source": "jellyfin",
                            "source_item_id": "item-a",
                            "id": "playlist-item-a",
                            "name": "Same Song-[a]",
                            "canonical_name": "Same Song",
                            "url": "http://jf/Audio/item-a/stream.mp3",
                            "type": "music",
                        },
                        {
                            "entity_id": "jellyfin:item-b",
                            "source": "jellyfin",
                            "source_item_id": "item-b",
                            "id": "playlist-item-b",
                            "name": "Same Song-[b]",
                            "canonical_name": "Same Song",
                            "url": "http://jf/Audio/item-b/stream.mp3",
                            "type": "music",
                        },
                    ],
                }
            ],
            ensure_ascii=False,
        ),
        custom_play_list_json=json.dumps({"碰撞收藏": []}, ensure_ascii=False),
        picture_cache_path="",
    )

    library = MusicLibrary(config=config, log=logging.getLogger("test"), event_bus=None)
    library.gen_all_music_list()

    playlist_item_id = library.get_playlist_items("日语")[1]["item_id"]
    assert library.play_list_add_music(
        "碰撞收藏",
        [{"playlist_item_id": playlist_item_id, "display_name": "Same Song"}],
    )
    custom = library.get_custom_play_list()
    assert custom["碰撞收藏"] == [{"entity_id": "jellyfin:item-b", "display_name": "Same Song-[b]"}]
