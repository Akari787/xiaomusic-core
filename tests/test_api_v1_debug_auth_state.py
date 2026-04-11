from __future__ import annotations

from typing import Any, cast
import pytest
import json

from xiaomusic.api.routers import v1


def _parse_response(resp):
    body = getattr(resp, "body", resp)
    if isinstance(body, bytes):
        return json.loads(body.decode())
    return body


@pytest.mark.asyncio
async def test_api_v1_debug_auth_state_success(monkeypatch):
    class _Auth:
        @staticmethod
        def auth_debug_state():
            return {
                "auth_mode": "healthy",
                "login_at": "2026-03-09T07:30:00Z",
                "expires_at": "2026-03-10T07:30:00Z",
                "ttl_remaining_seconds": 1200,
                "last_refresh_trigger": "scheduled",
                "last_auth_error": "",
            }

    class _XM:
        auth_manager = _Auth()

    monkeypatch.setattr(v1, "_get_xiaomusic", lambda: _XM())
    out = await v1.api_v1_debug_auth_state()
    assert out["code"] == 0
    assert out["data"]["auth_mode"] == "healthy"
    assert out["data"]["last_refresh_trigger"] == "scheduled"


@pytest.mark.asyncio
async def test_api_v1_debug_auth_recovery_state_success(monkeypatch):
    class _Auth:
        @staticmethod
        def auth_debug_state():
            return {
                "auth_mode": "healthy",
                "login_at": None,
                "expires_at": None,
                "ttl_remaining_seconds": None,
                "last_refresh_trigger": "",
                "last_auth_error": "",
            }

        @staticmethod
        def auth_recovery_debug_state():
            return {
                "last_clear_short_session": {"stage": "clear_short_session", "result": "ok"},
                "last_login_exchange": {"stage": "login_exchange", "result": "ok"},
                "last_runtime_rebind": {"stage": "runtime_rebind", "result": "ok"},
                "last_playback_capability_verify": {
                    "stage": "playback_capability_verify",
                    "result": "ok",
                },
            }

    class _XM:
        auth_manager = _Auth()

    monkeypatch.setattr(v1, "_get_xiaomusic", lambda: _XM())
    out = await v1.api_v1_debug_auth_recovery_state()
    assert out["code"] == 0
    assert out["data"]["last_runtime_rebind"]["result"] == "ok"


@pytest.mark.asyncio
async def test_api_v1_debug_miaccount_login_trace_success(monkeypatch):
    class _Auth:
        @staticmethod
        def auth_debug_state():
            return {
                "auth_mode": "healthy",
                "login_at": None,
                "expires_at": None,
                "ttl_remaining_seconds": None,
                "last_refresh_trigger": "",
                "last_auth_error": "",
            }

        @staticmethod
        def auth_recovery_debug_state():
            return {
                "last_clear_short_session": {},
                "last_login_exchange": {},
                "last_runtime_rebind": {},
                "last_playback_capability_verify": {},
            }

        @staticmethod
        def miaccount_login_trace_debug_state():
            return {
                "login_input_snapshot": {"stage": "login_input_snapshot", "result": "ok"},
                "login_http_exchange": {"stage": "login_http_exchange", "result": "failed"},
                "login_response_parse": {"stage": "login_response_parse", "result": "failed"},
                "token_writeback": {"stage": "token_writeback", "result": "skipped"},
                "post_login_runtime_seed": {"stage": "post_login_runtime_seed", "result": "failed"},
            }

    class _XM:
        auth_manager = _Auth()

    monkeypatch.setattr(v1, "_get_xiaomusic", lambda: _XM())
    out = await v1.api_v1_debug_miaccount_login_trace()
    assert out["code"] == 0
    assert out["data"]["login_http_exchange"]["result"] == "failed"


@pytest.mark.asyncio
async def test_api_v1_debug_auth_rebuild_state_success(monkeypatch):
    class _Auth:
        @staticmethod
        def auth_debug_state():
            return {
                "auth_mode": "healthy",
                "login_at": None,
                "expires_at": None,
                "ttl_remaining_seconds": None,
                "last_refresh_trigger": "",
                "last_auth_error": "",
            }

        @staticmethod
        def auth_recovery_debug_state():
            return {
                "last_clear_short_session": {},
                "last_login_exchange": {},
                "last_runtime_rebind": {},
                "last_playback_capability_verify": {},
            }

        @staticmethod
        def miaccount_login_trace_debug_state():
            return {
                "login_input_snapshot": {},
                "login_http_exchange": {},
                "login_response_parse": {},
                "token_writeback": {},
                "post_login_runtime_seed": {},
            }

        @staticmethod
        def auth_rebuild_debug_state():
            return {
                "last_clear_short_session": {"result": "ok"},
                "last_rebuild_short_session": {"result": "ok"},
                "last_runtime_rebind": {"result": "ok"},
                "last_verify": {"result": "ok"},
            }

    class _XM:
        auth_manager = _Auth()

    monkeypatch.setattr(v1, "_get_xiaomusic", lambda: _XM())
    out = await v1.api_v1_debug_auth_rebuild_state()
    assert out["code"] == 0
    assert out["data"]["last_rebuild_short_session"]["result"] == "ok"


@pytest.mark.asyncio
async def test_api_v1_debug_auth_runtime_reload_state_success(monkeypatch):
    class _Auth:
        @staticmethod
        def auth_runtime_reload_debug_state():
            return {
                "last_reload_runtime": {
                    "result": "ok",
                    "verify_result": "ok",
                }
            }

    class _XM:
        auth_manager = _Auth()

    monkeypatch.setattr(v1, "_get_xiaomusic", lambda: _XM())
    out = await v1.api_v1_debug_auth_runtime_reload_state()
    assert out["code"] == 0
    assert out["data"]["last_reload_runtime"]["result"] == "ok"


@pytest.mark.asyncio
async def test_api_v1_debug_auth_short_session_rebuild_state_success(monkeypatch):
    class _Auth:
        @staticmethod
        def auth_short_session_rebuild_debug_state():
            return {
                "last_short_session_rebuild": {
                    "result": "ok",
                    "rebuild_source": "persistent_auth",
                },
                "last_persistent_auth_relogin": {
                    "result": "ok",
                    "used_path": "relogin_with_persistent_auth",
                },
                "last_runtime_rebind": {
                    "result": "ok",
                },
                "last_verify": {
                    "result": "ok",
                },
                "last_auth_recovery_flow": {
                    "result": "ok",
                },
                "last_locked_transition": {},
            }

    class _XM:
        auth_manager = _Auth()

    monkeypatch.setattr(v1, "_get_xiaomusic", lambda: _XM())
    out = await v1.api_v1_debug_auth_short_session_rebuild_state()
    assert out["code"] == 0
    assert out["data"]["last_short_session_rebuild"]["result"] == "ok"
    assert out["data"]["last_persistent_auth_relogin"]["used_path"] == "relogin_with_persistent_auth"


@pytest.mark.asyncio
async def test_status_reason_short_session_rebuild_failed(monkeypatch):
    pytest.importorskip("qrcode")
    from xiaomusic.api.routers import system

    class _Auth:
        @staticmethod
        def auth_status_snapshot():
            return {"mode": "degraded", "locked": False, "lock_reason": ""}

        @staticmethod
        def auth_debug_state():
            return {"last_auth_error": ""}

        @staticmethod
        def auth_short_session_rebuild_debug_state():
            return {
                "last_short_session_rebuild": {
                    "result": "failed",
                    "error_code": "redirect_http_401",
                    "failed_reason": "redirect_http_401",
                    "error_message": "redirect status=401",
                },
                "last_auth_recovery_flow": {"result": "failed"},
            }

        @staticmethod
        def map_auth_public_status(runtime_auth_ready: bool | None = None):
            return {
                "status": "degraded",
                "auth_mode": "degraded",
                "status_reason": "short_session_rebuild_failed",
                "status_reason_detail": "rebuild failed: redirect_http_401",
                "status_mapping_source": "short_session_rebuild_failed",
                "recovery_failure_count": 0,
                "persistent_auth_available": True,
                "short_session_available": False,
                "runtime_auth_ready": bool(runtime_auth_ready),
                "auth_locked": False,
                "auth_lock_until": 0,
                "auth_lock_reason": "",
                "auth_lock_transition_reason": "",
                "auth_lock_counter": 0,
                "auth_lock_counter_threshold": 0,
                "manual_login_required_reason": "",
                "runtime_not_ready_reason": "",
                "last_error": "",
                "rebuild_failed": True,
                "rebuild_error_code": "redirect_http_401",
                "rebuild_failed_reason": "redirect_http_401",
            }

    class _FakeTokenStore:
        path = None

        @staticmethod
        def get():
            return {
                "userId": "test",
                "passToken": "test",
                "psecurity": "test",
                "ssecurity": "test",
                "cUserId": "test",
                "deviceId": "test",
            }

    class _XM:
        auth_manager = _Auth()
        token_store = _FakeTokenStore()

    async def _fake_runtime_ready():
        return False

    monkeypatch.setattr(system, "_runtime_auth_ready", _fake_runtime_ready)
    monkeypatch.setattr(system, "xiaomusic", _XM())
    monkeypatch.setattr(system, "config", _FakeConfig())
    monkeypatch.setattr(system, "qrcode_login_task", None)
    monkeypatch.setattr(system, "qrcode_login_started_at", 0.0)
    monkeypatch.setattr(system, "qrcode_login_error", "")

    out = await system.auth_status()
    assert out["success"] is True
    assert out["status_reason"] == "short_session_rebuild_failed"
    assert out["rebuild_failed"] is True
    assert "redirect_http_401" in str(out["rebuild_error_code"])


@pytest.mark.asyncio
async def test_status_reason_short_session_missing_no_failure_recorded(monkeypatch):
    pytest.importorskip("qrcode")
    from xiaomusic.api.routers import system

    class _Auth:
        @staticmethod
        def auth_status_snapshot():
            return {"mode": "degraded", "locked": False, "lock_reason": ""}

        @staticmethod
        def auth_debug_state():
            return {"last_auth_error": ""}

        @staticmethod
        def auth_short_session_rebuild_debug_state():
            return {
                "last_short_session_rebuild": {"result": "ok"},
                "last_auth_recovery_flow": {},
            }

        @staticmethod
        def map_auth_public_status(runtime_auth_ready: bool | None = None):
            return {
                "status": "degraded",
                "auth_mode": "degraded",
                "status_reason": "short_session_missing",
                "status_reason_detail": "short-lived session tokens missing",
                "status_mapping_source": "short_session_missing",
                "recovery_failure_count": 0,
                "persistent_auth_available": True,
                "short_session_available": False,
                "runtime_auth_ready": bool(runtime_auth_ready),
                "auth_locked": False,
                "auth_lock_until": 0,
                "auth_lock_reason": "",
                "auth_lock_transition_reason": "",
                "auth_lock_counter": 0,
                "auth_lock_counter_threshold": 0,
                "manual_login_required_reason": "",
                "runtime_not_ready_reason": "",
                "last_error": "",
                "rebuild_failed": False,
                "rebuild_error_code": "",
                "rebuild_failed_reason": "",
            }

    class _FakeTokenStore:
        path = None

        @staticmethod
        def get():
            return {
                "userId": "test",
                "passToken": "test",
                "psecurity": "test",
                "ssecurity": "test",
                "cUserId": "test",
                "deviceId": "test",
            }

    class _XM:
        auth_manager = _Auth()
        token_store = _FakeTokenStore()

    async def _fake_runtime_ready():
        return False

    monkeypatch.setattr(system, "_runtime_auth_ready", _fake_runtime_ready)
    monkeypatch.setattr(system, "xiaomusic", _XM())
    monkeypatch.setattr(system, "config", _FakeConfig())
    monkeypatch.setattr(system, "qrcode_login_task", None)
    monkeypatch.setattr(system, "qrcode_login_started_at", 0.0)
    monkeypatch.setattr(system, "qrcode_login_error", "")

    out = await system.auth_status()
    assert out["success"] is True
    assert out["status_reason"] == "short_session_missing"
    assert out["rebuild_failed"] is False


@pytest.mark.asyncio
async def test_status_reason_persistent_auth_missing(monkeypatch):
    pytest.importorskip("qrcode")
    from xiaomusic.api.routers import system

    class _FakeTokenStore:
        path = None

        @staticmethod
        def get():
            return {}

    class _Auth:
        @staticmethod
        def auth_status_snapshot():
            return {"mode": "degraded", "locked": False, "lock_reason": ""}

        @staticmethod
        def auth_debug_state():
            return {"last_auth_error": ""}

        @staticmethod
        def auth_short_session_rebuild_debug_state():
            return {
                "last_short_session_rebuild": {"result": "failed"},
                "last_auth_recovery_flow": {"result": "failed"},
            }

        @staticmethod
        def map_auth_public_status(runtime_auth_ready: bool | None = None):
            return {
                "status": "degraded",
                "auth_mode": "degraded",
                "status_reason": "persistent_auth_missing",
                "status_reason_detail": "all long-lived auth fields missing from token",
                "status_mapping_source": "persistent_auth_missing",
                "recovery_failure_count": 0,
                "persistent_auth_available": False,
                "short_session_available": False,
                "runtime_auth_ready": bool(runtime_auth_ready),
                "auth_locked": False,
                "auth_lock_until": 0,
                "auth_lock_reason": "",
                "auth_lock_transition_reason": "",
                "auth_lock_counter": 0,
                "auth_lock_counter_threshold": 0,
                "manual_login_required_reason": "",
                "runtime_not_ready_reason": "",
                "last_error": "",
                "rebuild_failed": True,
                "rebuild_error_code": "",
                "rebuild_failed_reason": "",
            }

    class _XM:
        auth_manager = _Auth()
        token_store = _FakeTokenStore()

    async def _fake_runtime_ready():
        return False

    monkeypatch.setattr(system, "_runtime_auth_ready", _fake_runtime_ready)
    monkeypatch.setattr(system, "xiaomusic", _XM())
    monkeypatch.setattr(system, "config", _FakeConfig())
    monkeypatch.setattr(system, "qrcode_login_task", None)
    monkeypatch.setattr(system, "qrcode_login_started_at", 0.0)
    monkeypatch.setattr(system, "qrcode_login_error", "")

    out = await system.auth_status()
    assert out["success"] is True
    assert out["status_reason"] == "persistent_auth_missing"
    assert out["persistent_auth_available"] is False


@pytest.mark.asyncio
async def test_status_reason_temporarily_locked_when_not_manual(monkeypatch):
    pytest.importorskip("qrcode")
    from xiaomusic.api.routers import system

    class _Auth:
        @staticmethod
        def auth_status_snapshot():
            return {
                "mode": "locked",
                "locked": True,
                "lock_reason": "retry threshold reached",
                "locked_until_ts": 9999999999,
                "lock_transition_reason": "ensure_auth:verify:runtime_error:threshold_reached",
                "lock_counter": 3,
                "lock_counter_threshold": 3,
                "need_qr_scan": False,
                "long_term_expired": False,
                "user_action_required": False,
            }

        @staticmethod
        def auth_debug_state():
            return {"last_auth_error": "retry threshold reached"}

        @staticmethod
        def auth_short_session_rebuild_debug_state():
            return {
                "last_short_session_rebuild": {"result": "failed"},
                "last_auth_recovery_flow": {"result": "failed"},
            }

        @staticmethod
        def map_auth_public_status(runtime_auth_ready: bool | None = None):
            return {
                "status": "failed",
                "auth_mode": "locked",
                "status_reason": "temporarily_locked",
                "status_reason_detail": "retry threshold reached",
                "status_mapping_source": "locked_temporary",
                "recovery_failure_count": 0,
                "persistent_auth_available": False,
                "short_session_available": False,
                "runtime_auth_ready": bool(runtime_auth_ready),
                "auth_locked": True,
                "auth_lock_until": 9999999999,
                "auth_lock_reason": "retry threshold reached",
                "auth_lock_transition_reason": "ensure_auth:verify:runtime_error:threshold_reached",
                "auth_lock_counter": 3,
                "auth_lock_counter_threshold": 3,
                "manual_login_required_reason": "",
                "runtime_not_ready_reason": "",
                "last_error": "retry threshold reached",
                "rebuild_failed": True,
                "rebuild_error_code": "",
                "rebuild_failed_reason": "",
            }

    class _XM:
        auth_manager = _Auth()
        token_store = None

    async def _fake_runtime_ready():
        return False

    monkeypatch.setattr(system, "_runtime_auth_ready", _fake_runtime_ready)
    monkeypatch.setattr(system, "xiaomusic", _XM())
    monkeypatch.setattr(system, "config", _FakeConfig())
    monkeypatch.setattr(system, "qrcode_login_task", None)
    monkeypatch.setattr(system, "qrcode_login_started_at", 0.0)
    monkeypatch.setattr(system, "qrcode_login_error", "")

    out = await system.auth_status()
    assert out["success"] is True
    assert out["status_reason"] == "temporarily_locked"
    assert out["status_mapping_source"] == "locked_temporary"
    assert out["auth_locked"] is True


@pytest.mark.asyncio
async def test_status_reason_manual_login_required(monkeypatch):
    pytest.importorskip("qrcode")
    from xiaomusic.api.routers import system

    class _Auth:
        @staticmethod
        def auth_status_snapshot():
            return {
                "mode": "locked",
                "locked": True,
                "lock_reason": "too many failures",
                "locked_until_ts": 9999999999,
                "lock_transition_reason": "qrcode required",
                "lock_counter": 3,
                "lock_counter_threshold": 3,
                "need_qr_scan": True,
                "long_term_expired": True,
                "user_action_required": True,
            }

        @staticmethod
        def auth_debug_state():
            return {"last_auth_error": "too many failures"}

        @staticmethod
        def auth_short_session_rebuild_debug_state():
            return {
                "last_short_session_rebuild": {"result": "failed"},
                "last_auth_recovery_flow": {"result": "failed"},
            }

        @staticmethod
        def map_auth_public_status(runtime_auth_ready: bool | None = None):
            return {
                "status": "failed",
                "auth_mode": "locked",
                "status_reason": "manual_login_required",
                "status_reason_detail": "too many failures",
                "status_mapping_source": "locked_manual",
                "recovery_failure_count": 0,
                "persistent_auth_available": False,
                "short_session_available": False,
                "runtime_auth_ready": bool(runtime_auth_ready),
                "auth_locked": True,
                "auth_lock_until": 9999999999,
                "auth_lock_reason": "too many failures",
                "auth_lock_transition_reason": "qrcode required",
                "auth_lock_counter": 3,
                "auth_lock_counter_threshold": 3,
                "manual_login_required_reason": "qrcode required",
                "runtime_not_ready_reason": "",
                "last_error": "too many failures",
                "rebuild_failed": True,
                "rebuild_error_code": "",
                "rebuild_failed_reason": "",
            }

    class _XM:
        auth_manager = _Auth()
        token_store = None

    async def _fake_runtime_ready():
        return False

    monkeypatch.setattr(system, "_runtime_auth_ready", _fake_runtime_ready)
    monkeypatch.setattr(system, "xiaomusic", _XM())
    monkeypatch.setattr(system, "config", _FakeConfig())
    monkeypatch.setattr(system, "qrcode_login_task", None)
    monkeypatch.setattr(system, "qrcode_login_started_at", 0.0)
    monkeypatch.setattr(system, "qrcode_login_error", "")

    out = await system.auth_status()
    assert out["success"] is True
    assert out["status_reason"] == "manual_login_required"
    assert out["status_mapping_source"] == "locked_manual"
    assert out["auth_locked"] is True


@pytest.mark.asyncio
async def test_status_reason_runtime_not_ready(monkeypatch):
    pytest.importorskip("qrcode")
    from xiaomusic.api.routers import system

    class _FakeTokenStore:
        path = None

        @staticmethod
        def get():
            return {
                "userId": "test",
                "passToken": "test",
                "psecurity": "test",
                "ssecurity": "test",
                "cUserId": "test",
                "deviceId": "test",
                "serviceToken": "test",
            }

    class _Auth:
        @staticmethod
        def auth_status_snapshot():
            return {"mode": "degraded", "locked": False, "lock_reason": ""}

        @staticmethod
        def auth_debug_state():
            return {"last_auth_error": ""}

        @staticmethod
        def auth_short_session_rebuild_debug_state():
            return {
                "last_short_session_rebuild": {"result": "ok"},
                "last_auth_recovery_flow": {"result": "ok"},
            }

        @staticmethod
        def map_auth_public_status(runtime_auth_ready: bool | None = None):
            return {
                "status": "degraded",
                "auth_mode": "degraded",
                "status_reason": "runtime_not_ready",
                "status_reason_detail": "runtime auth ready but not verified",
                "status_mapping_source": "runtime_not_ready",
                "recovery_failure_count": 0,
                "persistent_auth_available": True,
                "short_session_available": True,
                "runtime_auth_ready": bool(runtime_auth_ready),
                "auth_locked": False,
                "auth_lock_until": 0,
                "auth_lock_reason": "",
                "auth_lock_transition_reason": "",
                "auth_lock_counter": 0,
                "auth_lock_counter_threshold": 0,
                "manual_login_required_reason": "",
                "runtime_not_ready_reason": "runtime auth ready but not verified",
                "last_error": "",
                "rebuild_failed": False,
                "rebuild_error_code": "",
                "rebuild_failed_reason": "",
            }

    class _XM:
        auth_manager = _Auth()
        token_store = _FakeTokenStore()

    async def _fake_runtime_ready():
        return False

    monkeypatch.setattr(system, "_runtime_auth_ready", _fake_runtime_ready)
    monkeypatch.setattr(system, "xiaomusic", _XM())
    monkeypatch.setattr(system, "config", _FakeConfig())
    monkeypatch.setattr(system, "qrcode_login_task", None)
    monkeypatch.setattr(system, "qrcode_login_started_at", 0.0)
    monkeypatch.setattr(system, "qrcode_login_error", "")

    out = await system.auth_status()
    assert out["success"] is True
    assert out["status_reason"] == "runtime_not_ready"
    assert out["short_session_available"] is True
    assert out["persistent_auth_available"] is True
    assert out["runtime_auth_ready"] is False
    assert out["rebuild_failed"] is False


class _FakeConfig:
    auth_token_path = "conf/auth.json"
    qrcode_timeout = 120
