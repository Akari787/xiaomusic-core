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

    class _Facade:
        async def previous(self, device_id: str, request_id: str | None = None):
            calls.append(("facade.previous", (), {"device_id": device_id, "request_id": request_id}))
            return {"status": "ok", "device_id": device_id, "transport": "miio", "request_id": request_id, "action": "previous"}

        async def next(self, device_id: str, request_id: str | None = None):
            calls.append(("facade.next", (), {"device_id": device_id, "request_id": request_id}))
            return {"status": "ok", "device_id": device_id, "transport": "miio", "request_id": request_id, "action": "next"}

    class _XM:
        @staticmethod
        def did_exist(did: str) -> bool:
            return did == "did-1"

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
    monkeypatch.setattr(v1, "_get_facade", lambda: _Facade())

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
        "facade.previous",
        "facade.next",
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
    assert out_prev["data"]["transport"] == "miio"
    assert out_next["data"]["transport"] == "miio"
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
    assert out["data"]["error_code"] == "E_INVALID_REQUEST"
    assert out["data"]["stage"] == "request"


@pytest.mark.asyncio
async def test_api_v1_structured_controls_reject_missing_device(monkeypatch):
    from xiaomusic.core.errors import DeviceNotFoundError

    class _Facade:
        async def next(self, device_id: str, request_id: str | None = None):
            _ = (device_id, request_id)
            raise DeviceNotFoundError("device not found")

    monkeypatch.setattr(v1, "_get_facade", lambda: _Facade())
    out = await v1.api_v1_control_next(ControlRequest(device_id="missing"))
    assert out["code"] == 40004
    assert out["message"] == "device not found"
    assert out["data"]["error_code"] == "E_DEVICE_NOT_FOUND"
    assert out["data"]["stage"] == "request"


@pytest.mark.asyncio
async def test_api_v1_playlist_play_rejects_missing_playlist_name(monkeypatch):
    class _XM:
        @staticmethod
        def did_exist(did: str) -> bool:
            return True

    monkeypatch.setattr(v1, "_get_xiaomusic", lambda: _XM())
    out = await v1.api_v1_playlist_play(
        PlaylistPlayRequest(device_id="did-1", playlist_name="", music_name="song-a")
    )
    assert out["code"] == 40001
    assert out["message"] == "playlist_name is required"
    assert out["data"]["field"] == "playlist_name"
    assert out["data"]["error_code"] == "E_INVALID_REQUEST"
    assert out["data"]["stage"] == "request"


@pytest.mark.asyncio
async def test_api_v1_playlist_play_rejects_missing_device_with_device_error(monkeypatch):
    class _XM:
        @staticmethod
        def did_exist(did: str) -> bool:
            return False

    monkeypatch.setattr(v1, "_get_xiaomusic", lambda: _XM())
    out = await v1.api_v1_playlist_play(
        PlaylistPlayRequest(device_id="missing", playlist_name="收藏", music_name="song-a")
    )
    assert out["code"] == 40004
    assert out["message"] == "device not found"
    assert out["data"]["error_code"] == "E_DEVICE_NOT_FOUND"
    assert out["data"]["stage"] == "request"


@pytest.mark.asyncio
async def test_api_v1_favorites_add_internal_failure_is_structured(monkeypatch):
    class _XM:
        @staticmethod
        def did_exist(did: str) -> bool:
            return True

        async def add_to_favorites(self, **kwargs):
            _ = kwargs
            raise RuntimeError("boom")

    monkeypatch.setattr(v1, "_get_xiaomusic", lambda: _XM())
    out = await v1.api_v1_library_favorites_add(FavoritesRequest(device_id="did-1", track_name="song-a"))
    assert out["code"] == 10000
    assert out["message"] == "favorites add failed"
    assert out["data"]["error_code"] == "E_FAVORITES_ADD_FAILED"
    assert out["data"]["stage"] == "library"


@pytest.mark.asyncio
async def test_api_v1_library_refresh_internal_failure_is_structured(monkeypatch):
    class _XM:
        async def gen_music_list(self, **kwargs):
            _ = kwargs
            raise RuntimeError("refresh failed")

    monkeypatch.setattr(v1, "_get_xiaomusic", lambda: _XM())
    out = await v1.api_v1_library_refresh(LibraryRefreshRequest())
    assert out["code"] == 10000
    assert out["message"] == "library refresh failed"
    assert out["data"]["error_code"] == "E_LIBRARY_REFRESH_FAILED"
    assert out["data"]["stage"] == "library"


@pytest.mark.asyncio
async def test_api_v1_devices_and_system_status_internal_failure_are_structured(monkeypatch):
    class _XM:
        async def getalldevices(self):
            raise RuntimeError("query failed")

    monkeypatch.setattr(v1, "_get_xiaomusic", lambda: _XM())
    devices_out = await v1.api_v1_devices()
    status_out = await v1.api_v1_system_status()

    assert devices_out["code"] == 10000
    assert devices_out["message"] == "devices query failed"
    assert devices_out["data"]["error_code"] == "E_DEVICES_QUERY_FAILED"
    assert devices_out["data"]["stage"] == "system"

    assert status_out["code"] == 10000
    assert status_out["message"] == "system status query failed"
    assert status_out["data"]["error_code"] == "E_SYSTEM_STATUS_FAILED"
    assert status_out["data"]["stage"] == "system"
