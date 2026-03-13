from __future__ import annotations

import pytest

from xiaomusic.api.routers import v1


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
async def test_api_v1_debug_oauth_runtime_reload_state_success(monkeypatch):
    class _Auth:
        @staticmethod
        def oauth_runtime_reload_debug_state():
            return {
                "last_reload_runtime": {
                    "result": "ok",
                    "verify_result": "ok",
                }
            }

    class _XM:
        auth_manager = _Auth()

    monkeypatch.setattr(v1, "_get_xiaomusic", lambda: _XM())
    out = await v1.api_v1_debug_oauth_runtime_reload_state()
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
                    "rebuild_source": "long_auth",
                },
                "last_runtime_rebind": {
                    "result": "ok",
                },
                "last_verify": {
                    "result": "ok",
                },
            }

    class _XM:
        auth_manager = _Auth()

    monkeypatch.setattr(v1, "_get_xiaomusic", lambda: _XM())
    out = await v1.api_v1_debug_auth_short_session_rebuild_state()
    assert out["code"] == 0
    assert out["data"]["last_short_session_rebuild"]["result"] == "ok"
