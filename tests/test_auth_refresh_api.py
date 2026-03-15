from typing import Any, cast

import pytest

pytest.importorskip("aiofiles")
pytest.importorskip("qrcode")

from xiaomusic.api.routers import system


@pytest.mark.asyncio
async def test_auth_refresh_api_contract(monkeypatch):
    class _AuthManager:
        async def manual_reload_runtime(self, reason="manual_refresh_runtime"):
            assert reason == "manual_refresh_runtime"
            return {
                "refreshed": True,
                "runtime_auth_ready": True,
                "token_saved": False,
                "token_loaded": True,
                "token_store_reloaded": True,
                "runtime_rebound": True,
                "device_map_refreshed": True,
                "verify_result": "ok",
                "last_error": None,
                "error_code": "",
                "timestamps": {
                    "saveTime": 1,
                    "last_ok_ts": 2,
                    "last_refresh_ts": 3,
                },
            }

    monkeypatch.setattr(system, "xiaomusic", type("_XM", (), {"auth_manager": _AuthManager()})())

    out = cast(dict[str, Any], await system.auth_refresh())
    assert out["refreshed"] is True
    assert out["runtime_auth_ready"] is True
    assert out["token_saved"] is False
    assert out["token_loaded"] is True
    assert out["runtime_rebound"] is True
    assert out["device_map_refreshed"] is True
    assert out["verify_result"] == "ok"
    assert out["last_error"] is None
    assert set(out["timestamps"].keys()) == {"saveTime", "last_ok_ts", "last_refresh_ts"}


@pytest.mark.asyncio
async def test_auth_refresh_runtime_api_contract(monkeypatch):
    class _AuthManager:
        async def manual_reload_runtime(self, reason="manual_refresh_runtime"):
            return {
                "refreshed": True,
                "runtime_auth_ready": True,
                "token_saved": False,
                "token_loaded": True,
                "token_store_reloaded": True,
                "runtime_rebound": True,
                "device_map_refreshed": True,
                "verify_result": "ok",
                "last_error": None,
                "error_code": "",
                "timestamps": {
                    "saveTime": 1,
                    "last_ok_ts": 2,
                    "last_refresh_ts": 3,
                },
            }

    monkeypatch.setattr(system, "xiaomusic", type("_XM", (), {"auth_manager": _AuthManager()})())
    out = cast(dict[str, Any], await system.auth_refresh_runtime())
    assert out["runtime_auth_ready"] is True
