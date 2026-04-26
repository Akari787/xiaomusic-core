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


def test_gen_all_music_list_dedupes_same_entity_in_aggregate_playlists(monkeypatch):
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
                    "name": "其他语言",
                    "source": "jellyfin",
                    "source_playlist_id": "pl-other",
                    "musics": [
                        {
                            "entity_id": "jellyfin:58ccd8",
                            "source": "jellyfin",
                            "source_item_id": "58ccd8",
                            "name": "Ana-Lia",
                            "canonical_name": "Ana-Lia",
                            "url": "http://jf/Audio/58ccd8/stream.mp3?api_key=1",
                            "type": "music",
                        }
                    ],
                },
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
                },
            ],
            ensure_ascii=False,
        ),
        custom_play_list_json="{}",
        picture_cache_path="",
    )

    library = MusicLibrary(config=config, log=logging.getLogger("test"), event_bus=None)
    library.gen_all_music_list()

    assert library.get_music_list()["所有歌曲"] == ["Ana-Lia"]
    assert library.get_music_list()["全部"] == ["Ana-Lia"]

    playlist_items = library.get_playlist_items()
    assert [item["display_name"] for item in playlist_items["其他语言"]] == ["Ana-Lia"]
    assert [item["display_name"] for item in playlist_items["日语"]] == ["Ana-Lia-[58ccd8]"]
    assert [item["entity_id"] for item in playlist_items["所有歌曲"]] == ["jellyfin:58ccd8"]
