from __future__ import annotations

import pytest

from xiaomusic.api.models import (
    ControlRequest,
    FavoritesRequest,
    LibraryRefreshRequest,
    PlayModeRequest,
    PlaylistPlayIndexRequest,
    PlaylistPlayRequest,
    ShutdownTimerRequest,
)
from xiaomusic.api.routers import v1


@pytest.mark.asyncio
async def test_api_v1_structured_controls_call_xiaomusic(monkeypatch):
    calls: list[tuple[str, tuple, dict]] = []

    class _XM:
        @staticmethod
        def did_exist(did: str) -> bool:
            return did == "did-1"

        async def play_prev(self, **kwargs):
            calls.append(("play_prev", (), kwargs))

        async def play_next(self, **kwargs):
            calls.append(("play_next", (), kwargs))

        async def set_play_type_rnd(self, **kwargs):
            calls.append(("set_play_type_rnd", (), kwargs))

        async def set_play_type_one(self, **kwargs):
            calls.append(("set_play_type_one", (), kwargs))

        async def set_play_type_all(self, **kwargs):
            calls.append(("set_play_type_all", (), kwargs))

        async def stop_after_minute(self, **kwargs):
            calls.append(("stop_after_minute", (), kwargs))

        async def add_to_favorites(self, **kwargs):
            calls.append(("add_to_favorites", (), kwargs))

        async def del_from_favorites(self, **kwargs):
            calls.append(("del_from_favorites", (), kwargs))

        async def play_music_list(self, **kwargs):
            calls.append(("play_music_list", (), kwargs))

        async def play_music_list_by_index(self, **kwargs):
            calls.append(("play_music_list_by_index", (), kwargs))

        async def gen_music_list(self, **kwargs):
            calls.append(("gen_music_list", (), kwargs))

    xm = _XM()
    monkeypatch.setattr(v1, "_get_xiaomusic", lambda: xm)

    out_prev = await v1.api_v1_control_previous(ControlRequest(device_id="did-1"))
    out_next = await v1.api_v1_control_next(ControlRequest(device_id="did-1"))
    out_mode = await v1.api_v1_control_play_mode(PlayModeRequest(device_id="did-1", play_mode="random"))
    out_timer = await v1.api_v1_control_shutdown_timer(ShutdownTimerRequest(device_id="did-1", minutes=5))
    out_add = await v1.api_v1_library_favorites_add(FavoritesRequest(device_id="did-1", track_name="song-a"))
    out_remove = await v1.api_v1_library_favorites_remove(FavoritesRequest(device_id="did-1", track_name="song-a"))
    out_playlist = await v1.api_v1_playlist_play(
        PlaylistPlayRequest(device_id="did-1", playlist_name="收藏", music_name="song-a")
    )
    out_index = await v1.api_v1_playlist_play_index(
        PlaylistPlayIndexRequest(device_id="did-1", playlist_name="收藏", index=2)
    )
    out_refresh = await v1.api_v1_library_refresh(LibraryRefreshRequest())

    assert [item[0] for item in calls] == [
        "play_prev",
        "play_next",
        "set_play_type_rnd",
        "stop_after_minute",
        "add_to_favorites",
        "del_from_favorites",
        "play_music_list",
        "play_music_list_by_index",
        "gen_music_list",
    ]
    assert calls[3][2]["arg1"] == 5
    assert calls[4][2]["arg1"] == "song-a"
    assert calls[6][2]["arg1"] == "收藏|song-a"
    assert calls[7][2] == {"did": "did-1", "playlist_name": "收藏", "index": 2}
    assert calls[2][2]["dotts"] is False
    assert calls[2][2]["refresh_playlist"] is False
    for out in (out_prev, out_next, out_mode, out_timer, out_add, out_remove, out_playlist, out_index, out_refresh):
        assert out["code"] == 0
        assert out["message"] == "ok"


@pytest.mark.asyncio
async def test_api_v1_play_mode_rejects_invalid_value(monkeypatch):
    class _XM:
        @staticmethod
        def did_exist(did: str) -> bool:
            return True

    monkeypatch.setattr(v1, "_get_xiaomusic", lambda: _XM())
    out = await v1.api_v1_control_play_mode(PlayModeRequest(device_id="did-1", play_mode="bad-mode"))
    assert out["code"] == 40001
    assert out["message"] == "invalid play_mode"
    assert out["data"]["field"] == "play_mode"


@pytest.mark.asyncio
async def test_api_v1_structured_controls_reject_missing_device(monkeypatch):
    class _XM:
        @staticmethod
        def did_exist(did: str) -> bool:
            return False

    monkeypatch.setattr(v1, "_get_xiaomusic", lambda: _XM())
    out = await v1.api_v1_control_next(ControlRequest(device_id="missing"))
    assert out["code"] == 40004
    assert out["message"] == "device not found"
