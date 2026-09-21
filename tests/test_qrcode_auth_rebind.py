from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from xiaomusic.api.routers import system
from tests import test_auth_runtime_stability as runtime_tests


@pytest.fixture
def auth_manager(tmp_path):
    return runtime_tests.auth_manager.__wrapped__(tmp_path)


@pytest.mark.asyncio
async def test_qrcode_poll_rebinds_before_reinit_and_does_not_clear_lock(monkeypatch):
    calls = []

    class _QRAPI:
        def get_logint_status(self, lp):
            calls.append(("poll", lp))

    class _Auth:
        async def manual_reload_runtime(self, **kwargs):
            calls.append(("reload", kwargs))
            return {"refreshed": True, "runtime_auth_ready": True}

    async def _reinit():
        calls.append(("reinit",))

    monkeypatch.setattr(
        system,
        "xiaomusic",
        SimpleNamespace(auth_manager=_Auth(), reinit=_reinit),
    )
    monkeypatch.setattr(system, "qrcode_login_error", "old error")

    await system.get_logint_status(_QRAPI(), "lp")

    assert calls == [
        ("poll", "lp"),
        (
            "reload",
            {"reason": "qrcode_login_success", "rebind_current_auth": True},
        ),
        ("reinit",),
    ]
    assert system.qrcode_login_error == ""


@pytest.mark.asyncio
async def test_qrcode_poll_keeps_error_and_skips_reinit_when_rebind_fails(monkeypatch):
    reinit = AsyncMock()

    class _QRAPI:
        def get_logint_status(self, lp):
            return None

    class _Auth:
        async def manual_reload_runtime(self, **kwargs):
            return {
                "refreshed": False,
                "runtime_auth_ready": True,
                "last_error": "verify failed",
            }

    monkeypatch.setattr(
        system,
        "xiaomusic",
        SimpleNamespace(auth_manager=_Auth(), reinit=reinit),
    )
    monkeypatch.setattr(system, "qrcode_login_error", "")
    monkeypatch.setattr(system, "log", SimpleNamespace(exception=lambda *args, **kwargs: None))

    await system.get_logint_status(_QRAPI(), "lp")

    reinit.assert_not_awaited()
    assert system.qrcode_login_error == "verify failed"


@pytest.mark.asyncio
async def test_manual_reload_qrcode_rebind_uses_persisted_short_session_without_exchange(
    auth_manager,
):
    manager, token_store = auth_manager
    manager._state = manager.STATE_LOCKED
    manager._last_manual_login_required_reason = "old manual gate"
    manager._scheduled_refresh_suspended = True
    manager._scheduled_refresh_suspend_reason = "old scheduled gate"
    manager._scheduled_refresh_suspend_code = "credential_session_rejected"
    manager._last_error = "old 70016"
    manager._last_login_trace = {
        "auth_class": "credential_session_rejected",
        "error_type": "auth_error",
        "need_qr_scan": True,
        "user_action_required": True,
    }
    old_runtime = manager.mina_service
    candidate = {
        "ok": True,
        "account": object(),
        "mina_service": object(),
        "miio_service": object(),
        "session": None,
        "device_id": token_store.get().get("deviceId"),
    }
    manager._build_verified_runtime_candidate = AsyncMock(return_value=candidate)
    exchange = AsyncMock(side_effect=AssertionError("QR path exchanged passToken"))
    login = AsyncMock(side_effect=AssertionError("QR path called MiAccount.login"))
    manager._try_miaccount_persistent_auth_relogin = exchange

    with patch("xiaomusic.auth.MiAccount") as mi_account:
        mi_account.return_value.login = login
        out = await manager.manual_reload_runtime(
            reason="qrcode_login_success", rebind_current_auth=True
        )
        mi_account.return_value.login.assert_not_awaited()

    assert out["refreshed"] is True
    assert out["runtime_auth_ready"] is True
    assert manager.mina_service is candidate["mina_service"]
    assert manager.mina_service is not old_runtime
    assert manager._state == manager.STATE_HEALTHY
    assert manager._scheduled_refresh_suspended is False
    assert manager._scheduled_refresh_suspend_reason == ""
    assert manager._scheduled_refresh_suspend_code == ""
    assert manager._last_manual_login_required_reason == ""
    assert manager._last_error == ""
    assert manager._last_login_trace["auth_class"] == ""
    assert manager._last_login_trace["error_type"] == ""
    assert manager._last_login_trace["need_qr_scan"] is False
    assert manager._last_login_trace["user_action_required"] is False
    assert manager._last_login_trace["long_term_expired"] is False
    assert manager._last_refresh_trigger == "qrcode_login_success"
    assert manager._last_recovery_error_code == ""
    exchange.assert_not_awaited()
    rebuild = manager.auth_short_session_rebuild_debug_state()["last_short_session_rebuild"]
    assert rebuild["used_path"] == "qrcode_persisted_short_session_rebind"
    assert rebuild["service_token_written"] is False
    assert rebuild["verify_result"] == "ok"
    assert rebuild["runtime_rebind_result"] == "ok"
    flow = manager.auth_short_session_rebuild_debug_state()["last_auth_recovery_flow"]
    assert flow["used_path"] == "qrcode_persisted_short_session_rebind"
    assert flow["service_token_written"] is False


@pytest.mark.asyncio
async def test_manual_reload_qrcode_failure_preserves_healthy_runtime_and_gate(auth_manager):
    manager, _ = auth_manager
    manager._state = manager.STATE_HEALTHY
    manager._scheduled_refresh_suspended = True
    manager._scheduled_refresh_suspend_code = "old_gate"
    manager._last_error = "old error"
    old_runtime = manager.mina_service
    manager._build_verified_runtime_candidate = AsyncMock(
        return_value={"ok": False, "error": "new verify failed"}
    )

    out = await manager.manual_reload_runtime(
        reason="qrcode_login_success", rebind_current_auth=True
    )

    assert out["refreshed"] is False
    assert out["runtime_auth_ready"] is True
    assert manager.mina_service is old_runtime
    assert manager._state == manager.STATE_HEALTHY
    assert manager._scheduled_refresh_suspended is True
    assert manager._scheduled_refresh_suspend_code == "old_gate"
    assert manager._last_error == "new verify failed"
    assert manager.auth_short_session_rebuild_debug_state()["last_short_session_rebuild"]["result"] == "failed"
