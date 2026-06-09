import asyncio
import logging
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from xiaomusic.api.models import PlayListObj
from xiaomusic.api.routers.playlist import playlistadd
from xiaomusic.channel import ChannelContextFilter, ChannelCycleController, ChannelSettings
from xiaomusic.js_adapter import JSAdapter
from xiaomusic.music_library import LEGACY_WRITE_BLOCKED_MESSAGE, LegacyReadOnlyDict, MusicLibrary


class _Config(SimpleNamespace):
    music_path = "music"
    download_path = "music/download"
    music_path_depth = 10
    music_list_json = ""
    custom_play_list_json = ""
    recently_added_playlist_len = 50
    picture_cache_path = "music/cache"
    channel_cycle_interval = 60
    channel_archive_log_enabled = False
    channel_archive_log_max_entries = 500
    channel_context_window_size = 20

    def get_exclude_dirs_set(self):
        return set()


def test_legacy_read_only_dict_blocks_external_mutation():
    view = LegacyReadOnlyDict.from_mapping({"song": "/tmp/song.mp3"})

    assert view["song"] == "/tmp/song.mp3"
    with pytest.raises(PermissionError, match="Identity APIs"):
        view["other"] = "/tmp/other.mp3"
    with pytest.raises(PermissionError, match="Identity APIs"):
        view.update({"other": "/tmp/other.mp3"})


def test_save_custom_playlist_legacy_api_is_blocked_but_identity_api_persists():
    library = MusicLibrary(config=_Config(), log=logging.getLogger("test"), event_bus=None)

    with pytest.raises(PermissionError, match="Identity APIs"):
        library.save_custom_play_list()

    assert library.play_list_add("收藏") is True
    assert "收藏" in library.get_custom_play_list()


def test_legacy_http_playlist_write_returns_403():
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(playlistadd(PlayListObj(name="legacy")))

    exc = exc_info.value
    assert exc.status_code == 403
    assert "Use Identity API instead" in str(exc.detail)


def test_identity_register_music_rebuilds_readonly_legacy_snapshot():
    library = MusicLibrary(config=_Config(), log=logging.getLogger("test"), event_bus=None)

    record = library.register_identity_music(
        entity_id="plugin:test:song-a",
        display_name="song-a",
        source="plugin",
        source_item_id="song-a",
        origin_url="https://example.test/song-a.mp3",
        playlist_name="plugin:test",
        extra={"display_name": "song-a", "api": {"id": "song-a"}},
    )

    assert record["entity_id"] == "plugin:test:song-a"
    assert library.all_music["song-a"] == "https://example.test/song-a.mp3"
    assert library.music_list["plugin:test"] == ["song-a"]
    with pytest.raises(PermissionError):
        library.all_music["evil"] = "mutate"


def test_js_adapter_uses_identity_registration_instead_of_legacy_write():
    library = MusicLibrary(config=_Config(), log=logging.getLogger("test"), event_bus=None)
    adapter = JSAdapter(SimpleNamespace(music_library=library))

    ids = adapter.format_search_results(
        [{"id": "1", "title": "Song", "url": "https://example.test/1.mp3"}],
        "mock",
    )

    assert ids == ["online_mock_1"]
    assert library.all_music["online_mock_1"] == "https://example.test/1.mp3"
    assert library.get_web_music_api()["online_mock_1"]["title"] == "Song"


def test_channel_settings_clamp_cycle_interval_and_filter_noise():
    config = SimpleNamespace(
        channel_cycle_interval=5,
        channel_archive_log_enabled=False,
        channel_archive_log_max_entries=10,
        channel_context_window_size=2,
    )
    settings = ChannelSettings.from_config(config, logging.getLogger("test"))
    assert settings.cycle_interval == 15
    assert settings.archive_log_max_entries == 100
    assert settings.context_window_size == 5

    context_filter = ChannelContextFilter(settings=settings, debug=True)
    assert context_filter.accept({"type": "PLAY", "payload": "song"}) is True
    assert context_filter.accept({"type": "volume_changed", "value": 30}) is False
    assert list(context_filter.context_window) == [{"type": "PLAY", "payload": "song"}]
    assert list(context_filter.archive_log) == [{"type": "volume_changed", "value": 30}]

    context_filter.close()
    assert len(context_filter.context_window) == 0
    assert len(context_filter.archive_log) == 0


def test_channel_cycle_interrupt_wakes_waiter():
    async def _run():
        controller = ChannelCycleController(interval=60)
        task = asyncio.create_task(controller.wait_next_cycle())
        await asyncio.sleep(0)
        controller.interrupt()
        return await asyncio.wait_for(task, timeout=1)

    assert asyncio.run(_run()) is True
