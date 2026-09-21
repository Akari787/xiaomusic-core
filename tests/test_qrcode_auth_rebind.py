import json
import time
from pathlib import Path
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
            return {
                "refreshed": True,
                "runtime_auth_ready": True,
                "token_saved": False,
                "device_map_refreshed": True,
            }

        async def need_login(self):
            raise AssertionError("QR reinit probed auth")

        async def ensure_logged_in(self, **kwargs):
            raise AssertionError("QR reinit entered login")

        async def init_all_data(self, **kwargs):
            assert kwargs == {"verified_runtime_only": True}
            calls.append(("verified-only-init", kwargs))

    auth_manager_stub = _Auth()

    async def _reinit(**kwargs):
        assert kwargs == {"auth_already_verified": True}
        calls.append(("reinit", kwargs))
        await auth_manager_stub.init_all_data(
            verified_runtime_only=kwargs["auth_already_verified"]
        )

    monkeypatch.setattr(
        system,
        "xiaomusic",
        SimpleNamespace(auth_manager=auth_manager_stub, reinit=_reinit),
    )
    monkeypatch.setattr(system, "qrcode_login_error", "old error")

    await system.get_logint_status(_QRAPI(), "lp")

    assert calls == [
        ("poll", "lp"),
        (
            "reload",
            {"reason": "qrcode_login_success", "rebind_current_auth": True},
        ),
        ("reinit", {"auth_already_verified": True}),
        ("verified-only-init", {"verified_runtime_only": True}),
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
    assert out["token_saved"] is False
    assert out["device_map_refreshed"] is True
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


@pytest.mark.asyncio
async def test_qrcode_rebind_uses_new_disk_auth_and_converges_public_status(auth_manager):
    manager, _ = auth_manager
    token_path = Path(manager.auth_token_path)
    disk_token = {
        **manager._get_auth_data(),
        "serviceToken": "disk-new-service-token",
        "yetAnotherServiceToken": "disk-new-yast",
        "saveTime": 1789967294853,
    }
    token_path.write_text(json.dumps(disk_token), encoding="utf-8")
    from xiaomusic.security.token_store import TokenStore

    manager.token_store = TokenStore(manager.config, runtime_tests._DummyLog())
    seen = {}

    async def _candidate(auth_data):
        seen.update(auth_data)
        return {
            "ok": True,
            "account": object(),
            "mina_service": object(),
            "miio_service": object(),
            "session": None,
            "device_id": auth_data["deviceId"],
        }

    manager._build_verified_runtime_candidate = _candidate
    manager._last_short_session_rebuild_state = {
        "result": "failed", "error_code": "old_rebuild"
    }
    manager._last_auth_recovery_flow_state = {"result": "failed", "used_path": "old"}
    manager._last_error = "old 70016"
    manager._retry_count = 3
    manager._retry_count_effective = 4
    manager._lock_counter = 5
    manager._probe_failure_count = 6
    manager._recovery_failure_count = 7
    manager._cooldown_until = time.time() + 300
    manager._last_retry_increment_reason = "old failure"
    manager._last_health_probe_result = "failed"
    manager._last_health_probe_error = "old probe"
    manager._last_ok_ts = 1
    manager._last_runtime_verify_ts = 1
    manager._last_session_success_ts = 1
    manager._last_login_ts = 1

    out = await manager.manual_reload_runtime(
        reason="qrcode_login_success", rebind_current_auth=True
    )

    assert seen["serviceToken"] == "disk-new-service-token"
    assert seen["yetAnotherServiceToken"] == "disk-new-yast"
    assert seen["saveTime"] == 1789967294853
    assert out["token_saved"] is False
    assert out["device_map_refreshed"] is True
    assert out["timestamps"]["saveTime"] > 1
    assert out["timestamps"]["last_ok_ts"] > 1
    assert manager._last_runtime_verify_ts > 1
    assert manager._last_session_success_ts > 1
    status = manager.map_auth_public_status(runtime_auth_ready=True)
    snapshot = manager.auth_public_status_snapshot(runtime_auth_ready=True)
    assert status["status_reason"] == "healthy"
    assert status["rebuild_failed"] is False
    assert status["last_error"] == ""
    assert snapshot["rebuild_failed"] is False
    assert snapshot["last_error"] == ""
    auth_snapshot = manager.auth_status_snapshot()
    assert auth_snapshot["recovery_failure_count"] == 0
    assert auth_snapshot["retry_count"] == 0
    assert auth_snapshot["retry_count_effective"] == 0
    assert auth_snapshot["lock_counter"] == 0
    assert auth_snapshot["cooldown_until_ts"] == 0
    assert manager._retry_count == 0
    assert manager._retry_count_effective == 0
    assert manager._lock_counter == 0
    assert manager._probe_failure_count == 0
    assert manager._recovery_failure_count == 0
    assert manager._cooldown_until == 0
    assert manager._last_retry_increment_reason == ""
    assert manager._last_health_probe_result == "ok"
    assert manager._last_health_probe_error == ""


@pytest.mark.asyncio
async def test_env_rebind_does_not_overwrite_short_session_history(auth_manager, monkeypatch):
    manager, _ = auth_manager
    sentinel_rebuild = {"result": "failed", "error_code": "historical"}
    sentinel_flow = {"result": "failed", "used_path": "historical"}
    manager._last_short_session_rebuild_state = dict(sentinel_rebuild)
    manager._last_auth_recovery_flow_state = dict(sentinel_flow)
    manager._build_verified_runtime_candidate = AsyncMock(return_value={
        "ok": True,
        "account": object(),
        "mina_service": object(),
        "miio_service": object(),
        "session": None,
        "device_id": manager.device_id,
    })
    monkeypatch.setenv("AUTH_ACCESS_TOKEN", "env-only")

    out = await manager.manual_reload_runtime(reason="ut-env-rebind")

    assert out["token_saved"] is False
    assert manager._last_short_session_rebuild_state == sentinel_rebuild
    assert manager._last_auth_recovery_flow_state == sentinel_flow


@pytest.mark.asyncio
async def test_verified_only_init_skips_auth_probe_and_login(auth_manager):
    manager, _ = auth_manager
    manager.need_login = AsyncMock(side_effect=AssertionError("verified-only need_login"))
    manager.ensure_logged_in = AsyncMock(side_effect=AssertionError("verified-only login"))
    manager.device_manager.update_device_info = AsyncMock(return_value=True)

    await manager.init_all_data(verified_runtime_only=True)

    manager.need_login.assert_not_awaited()
    manager.ensure_logged_in.assert_not_awaited()
    manager.device_manager.update_device_info.assert_awaited_once_with(manager)
