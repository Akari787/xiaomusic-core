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


@pytest.mark.asyncio
async def test_auth_status_exposes_recovery_distinction(monkeypatch, tmp_path):
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(
        '{"userId":"u","passToken":"p","psecurity":"ps","ssecurity":"ss","cUserId":"cu","deviceId":"d"}',
        encoding="utf-8",
    )

    class _TokenStore:
        path = auth_file

        @staticmethod
        def get():
            return {
                "userId": "u",
                "passToken": "p",
                "psecurity": "ps",
                "ssecurity": "ss",
                "cUserId": "cu",
                "deviceId": "d",
            }

    class _AuthManager:
        @staticmethod
        def auth_status_snapshot():
            return {"mode": "degraded", "locked": False, "locked_until_ts": None, "lock_reason": ""}

        @staticmethod
        def auth_debug_state():
            return {"last_auth_error": "missing short session token; rebuild from long auth required"}

    class _XM:
        token_store = _TokenStore()
        auth_manager = _AuthManager()

    async def _runtime_ready():
        return False

    monkeypatch.setattr(system, "config", type("_C", (), {"auth_token_path": str(auth_file), "qrcode_timeout": 120})())
    monkeypatch.setattr(system, "xiaomusic", _XM())
    monkeypatch.setattr(system, "_runtime_auth_ready", _runtime_ready)
    monkeypatch.setattr(system, "qrcode_login_task", None)
    monkeypatch.setattr(system, "qrcode_login_started_at", 0.0)
    monkeypatch.setattr(system, "qrcode_login_error", "")

    out = cast(dict[str, Any], await system.auth_status())
    assert out["token_valid"] is False
    assert out["persistent_auth_available"] is True
    assert out["short_session_available"] is False
    assert out["status_reason"] == "short_session_missing"
    assert out["last_error"] == "missing short session token; rebuild from long auth required"
