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


def test_aggregate_playlist_keeps_two_entities_with_same_display_name(monkeypatch):
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
                    "name": "歌单A",
                    "source": "jellyfin",
                    "source_playlist_id": "pl-a",
                    "musics": [
                        {
                            "entity_id": "jellyfin:item-a",
                            "source": "jellyfin",
                            "source_item_id": "item-a",
                            "name": "同名歌曲",
                            "canonical_name": "同名歌曲",
                            "url": "http://jf/Audio/item-a/stream.mp3?api_key=1",
                            "type": "music",
                        }
                    ],
                },
                {
                    "name": "歌单B",
                    "source": "jellyfin",
                    "source_playlist_id": "pl-b",
                    "musics": [
                        {
                            "entity_id": "jellyfin:item-b",
                            "source": "jellyfin",
                            "source_item_id": "item-b",
                            "name": "同名歌曲",
                            "canonical_name": "同名歌曲",
                            "url": "http://jf/Audio/item-b/stream.mp3?api_key=1",
                            "type": "music",
                        }
                    ],
                },
            ],
            ensure_ascii=False,
        ),
        custom_play_list_json="{}",
        picture_cache_path="",
    )

    library = MusicLibrary(config=config, log=logging.getLogger("test"), event_bus=None)
    library.gen_all_music_list()

    songs = library.get_music_list()["所有歌曲"]
    assert len(songs) == 2
    assert songs[0] == "同名歌曲"
    assert songs[1].startswith("同名歌曲-[")

    playlist_items = library.get_playlist_items("所有歌曲")
    assert {item["entity_id"] for item in playlist_items} == {"jellyfin:item-a", "jellyfin:item-b"}
    assert library.resolve_entity_id_by_name("同名歌曲") in {"jellyfin:item-a", "jellyfin:item-b"}


def test_custom_playlist_can_add_by_entity_id_string(monkeypatch):
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
                    "name": "歌单A",
                    "source": "jellyfin",
                    "source_playlist_id": "pl-a",
                    "musics": [
                        {
                            "entity_id": "jellyfin:item-a",
                            "source": "jellyfin",
                            "source_item_id": "item-a",
                            "name": "同名歌曲",
                            "canonical_name": "同名歌曲",
                            "url": "http://jf/Audio/item-a/stream.mp3?api_key=1",
                            "type": "music",
                        }
                    ],
                }
            ],
            ensure_ascii=False,
        ),
        custom_play_list_json=json.dumps({"收藏夹": []}, ensure_ascii=False),
        picture_cache_path="",
    )

    library = MusicLibrary(config=config, log=logging.getLogger("test"), event_bus=None)
    library.gen_all_music_list()

    assert library.play_list_add_music("收藏夹", ["jellyfin:item-a"])
    custom = library.get_custom_play_list()
    assert custom["收藏夹"] == [{"entity_id": "jellyfin:item-a", "display_name": "同名歌曲"}]

    status, songs = library.play_list_musics("收藏夹")
    assert status == "OK"
    assert songs == ["同名歌曲"]
