from __future__ import annotations

from typing import Any, cast

import pytest

pytest.importorskip("qrcode")


@pytest.mark.asyncio
async def test_api_v1_auth_status_returns_v1_envelope_and_core_fields(monkeypatch):
    from xiaomusic.api.routers import system, v1

    class _Auth:
        @staticmethod
        def map_auth_public_status(runtime_auth_ready: bool | None = None):
            return {
                "status": "degraded",
                "auth_mode": "degraded",
                "status_reason": "short_session_missing",
                "status_mapping_source": "short_session_missing",
                "recovery_failure_count": 2,
                "persistent_auth_available": True,
                "short_session_available": False,
                "runtime_auth_ready": bool(runtime_auth_ready),
                "auth_locked": False,
                "auth_lock_until": 0,
                "manual_login_required_reason": "",
                "runtime_not_ready_reason": "",
                "last_error": "missing short session token",
                "rebuild_failed": False,
                "rebuild_error_code": "",
                "rebuild_failed_reason": "",
            }

    class _TokenStore:
        path = None

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

    class _XM:
        auth_manager = _Auth()
        token_store = _TokenStore()

    async def _fake_runtime_ready():
        return False

    monkeypatch.setattr(v1, "_get_xiaomusic", lambda: _XM())
    monkeypatch.setattr(v1, "_runtime_auth_ready_v1", _fake_runtime_ready)

    monkeypatch.setattr(system, "xiaomusic", _XM())
    monkeypatch.setattr(system, "_runtime_auth_ready", _fake_runtime_ready)
    monkeypatch.setattr(system, "config", type("_C", (), {"auth_token_path": "conf/auth.json", "qrcode_timeout": 120})())
    monkeypatch.setattr(system, "qrcode_login_task", None)
    monkeypatch.setattr(system, "qrcode_login_started_at", 0.0)
    monkeypatch.setattr(system, "qrcode_login_error", "")

    v1_out = cast(dict[str, Any], await v1.api_v1_auth_status())
    system_out = cast(dict[str, Any], await system.auth_status())

    assert v1_out["code"] == 0
    assert v1_out["message"] == "ok"
    assert v1_out["request_id"]
    assert v1_out["data"]["status"] == "degraded"
    assert v1_out["data"]["auth_mode"] == "degraded"
    assert v1_out["data"]["status_reason"] == "short_session_missing"
    assert v1_out["data"]["recovery_failure_count"] == 2
    assert isinstance(v1_out["data"]["generated_at_ms"], int)

    assert system_out["success"] is True
    assert v1_out["data"]["auth_mode"] == system_out["auth_mode"]
    assert v1_out["data"]["status_reason"] == system_out["status_reason"]
    assert v1_out["data"]["recovery_failure_count"] == system_out["recovery_failure_count"]
    assert v1_out["data"]["status_mapping_source"] == system_out["status_mapping_source"]
