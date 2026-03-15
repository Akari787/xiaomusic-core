from __future__ import annotations

import pytest

from xiaomusic.api.models import ControlRequest, PlayRequest
from xiaomusic.api.routers import v1


def test_api_v1_routes_whitelist_only():
    routes = set()
    for route in v1.router.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        for method in methods:
            routes.add((method, path))
    expected = {
        ("POST", "/api/v1/play"),
        ("POST", "/api/v1/resolve"),
        ("POST", "/api/v1/control/stop"),
        ("POST", "/api/v1/control/pause"),
        ("POST", "/api/v1/control/resume"),
        ("POST", "/api/v1/control/tts"),
        ("POST", "/api/v1/control/volume"),
        ("POST", "/api/v1/control/probe"),
        ("POST", "/api/v1/control/previous"),
        ("POST", "/api/v1/control/next"),
        ("POST", "/api/v1/control/play-mode"),
        ("POST", "/api/v1/control/shutdown-timer"),
        ("POST", "/api/v1/library/favorites/add"),
        ("POST", "/api/v1/library/favorites/remove"),
        ("POST", "/api/v1/playlist/play"),
        ("POST", "/api/v1/playlist/play-index"),
        ("POST", "/api/v1/library/refresh"),
        ("GET", "/api/v1/devices"),
        ("GET", "/api/v1/system/status"),
        ("GET", "/api/v1/debug/auth_state"),
        ("GET", "/api/v1/debug/auth_recovery_state"),
        ("GET", "/api/v1/debug/miaccount_login_trace"),
        ("GET", "/api/v1/debug/auth_rebuild_state"),
        ("GET", "/api/v1/debug/auth_short_session_rebuild_state"),
        ("GET", "/api/v1/debug/auth_runtime_reload_state"),
        ("GET", "/api/v1/player/state"),
    }
    assert routes == expected


@pytest.mark.asyncio
async def test_v1_response_has_unified_top_level_fields(monkeypatch):
    class _Facade:
        async def play(self, *, device_id, query, source_hint="auto", options=None, request_id=None):  # noqa: ANN001
            _ = (query, source_hint, options)
            return {
                "status": "playing",
                "device_id": device_id,
                "source_plugin": "direct_url",
                "transport": "mina",
                "request_id": request_id,
                "media": {"title": "song", "stream_url": "http://a/b.mp3", "is_live": False},
                "extra": {},
            }

        async def stop(self, device_id: str, request_id: str | None = None):
            _ = request_id
            return {
                "status": "stopped",
                "device_id": device_id,
                "transport": "mina",
                "request_id": "rid",
                "extra": {},
            }

        async def player_state(self, device_id: str, request_id: str | None = None):
            _ = request_id
            return {
                "device_id": device_id,
                "is_playing": True,
                "cur_music": "song",
                "offset": 3,
                "duration": 30,
                "request_id": "rid-state",
            }

    monkeypatch.setattr(v1, "_get_facade", lambda: _Facade())
    play_out = await v1.api_v1_play(PlayRequest(device_id="did-1", query="http://a/b.mp3"))
    stop_out = await v1.api_v1_control_stop(ControlRequest(device_id="did-1"))
    state_out = await v1.api_v1_player_state(device_id="did-1")
    assert set(play_out.keys()) == {"code", "message", "data", "request_id"}
    assert set(stop_out.keys()) == {"code", "message", "data", "request_id"}
    assert set(state_out.keys()) == {"code", "message", "data", "request_id"}
    assert "speaker_id" not in play_out
    assert play_out["data"]["sid"]
    assert "state" not in play_out
