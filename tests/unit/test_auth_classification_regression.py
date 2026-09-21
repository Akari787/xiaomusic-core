from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import xiaomusic.auth as auth_module
from xiaomusic.auth import SimpleAuthManager


class _Store:
    def __init__(self, data: dict):
        self.data = dict(data)

    def get(self):
        return dict(self.data)

    def commit(self, data, reason=""):  # noqa: ARG002
        self.data = dict(data)


class _Log:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class _DeviceManager:
    async def update_device_info(self, auth):  # noqa: ARG002
        return None


class _Session:
    def __init__(self):
        self.cookie_jar = SimpleNamespace(update_cookies=lambda cookies: None)

    async def close(self):
        return None


@pytest.fixture
def manager(tmp_path: Path, monkeypatch):
    config = SimpleNamespace(
        conf_path=str(tmp_path),
        auth_token_path=str(tmp_path / "auth.json"),
        mi_did="",
        devices={},
        get_one_device_id=lambda: "device-1",
    )
    store = _Store({"userId": "u", "passToken": "p", "deviceId": "d", "ssecurity": "ss"})
    monkeypatch.setattr(auth_module, "ClientSession", _Session)
    return SimpleAuthManager(config, _Log(), _DeviceManager(), token_store=store), store


@pytest.mark.parametrize(
    ("evidence", "expected"),
    [
        ({"code": 70016, "captchaUrl": None}, "credential_session_rejected"),
        ({"code": 70016, "captchaUrl": "https://captcha.test"}, "interactive_captcha_challenge"),
        ({"code": 87001, "captchaUrl": None}, "interactive_captcha_challenge"),
        ({"code": 10001, "captchaUrl": None}, ""),
    ],
)
def test_classifier_structured_priority(evidence, expected):
    assert auth_module.classify_auth_challenge(evidence) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected_class", "expected_qr"),
    [
        ({"code": 70016, "captchaUrl": None}, "credential_session_rejected", True),
        ({"code": 70016, "captchaUrl": "https://captcha.test"}, "interactive_captcha_challenge", True),
        ({"code": 87001, "captchaUrl": None}, "interactive_captcha_challenge", True),
        ({"code": 10001, "captchaUrl": None}, "auth_error", False)
    ],
)
async def test_real_miaccount_service_login_classification(
    manager, monkeypatch, response, expected_class, expected_qr
):
    auth, store = manager

    class _MiAccount:
        def __init__(self, *args, **kwargs):  # noqa: ARG001
            self.token = {}

        async def _serviceLogin(self, path):  # noqa: ARG001
            return response

    monkeypatch.setattr(auth_module, "MiAccount", _MiAccount)
    out = await auth._try_miaccount_persistent_auth_relogin(
        before=store.get(), reason="ut-classification"
    )
    assert out["ok"] is False
    assert out.get("auth_class", "") == expected_class
    assert bool(out.get("need_qr_scan")) is expected_qr
    assert bool(out.get("long_term_expired")) is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected_class"),
    [
        (
            {"code": 70016, "captchaUrl": "https://captcha.test", "captchaurl": None},
            "interactive_captcha_challenge",
        ),
        (
            {"code": 0, "captchaUrl": "https://captcha.test"},
            "interactive_captcha_challenge",
        ),
        (
            {"code": 70016, "captchaUrl": "", "captchaurl": "0"},
            "interactive_captcha_challenge",
        ),
        (
            {"code": 70016, "captchaUrl": None, "captchaurl": None},
            "credential_session_rejected",
        ),
    ],
)
async def test_reactive_short_session_rebuild_preserves_structured_classification(
    manager, monkeypatch, response, expected_class
):
    auth, store = manager
    store.data.update(
        {
            "psecurity": "ps",
            "cUserId": "cu",
            "deviceId": "d",
        }
    )
    original_token = store.get()
    calls = {"security": 0, "login": 0}

    class _MiAccount:
        def __init__(self, *args, **kwargs):  # noqa: ARG001
            self.token = {}

        async def _serviceLogin(self, path):  # noqa: ARG001
            return response

        async def _securityTokenService(self, location, nonce, ssecurity):  # noqa: ARG001
            calls["security"] += 1
            return "unexpected-service-token"

        async def login(self, *args, **kwargs):  # noqa: ARG001
            calls["login"] += 1
            raise AssertionError("reactive recovery must not use MiAccount.login")

    monkeypatch.setattr(auth_module, "MiAccount", _MiAccount)
    assert await auth._try_login(reason="ut-reactive-classification") is False

    assert auth._last_manual_login_required_reason == expected_class
    assert auth._last_recovery_error_code == "auth_error"
    assert auth._last_login_trace["auth_class"] == expected_class
    assert auth._last_login_trace["error_type"] == "auth_error"
    assert calls["login"] == 0
    assert calls["security"] == 0
    assert store.get() == original_token


@pytest.mark.asyncio
async def test_real_miaccount_code_zero_null_captcha_reaches_security_token(
    manager, monkeypatch
):
    auth, store = manager
    calls = []

    class _MiAccount:
        def __init__(self, *args, **kwargs):  # noqa: ARG001
            self.token = {}

        async def _serviceLogin(self, path):  # noqa: ARG001
            return {
                "code": 0,
                "captchaUrl": None,
                "location": "https://api2.mina.mi.com/sts?nonce=abc",
                "ssecurity": "ss-new",
            }

        async def _securityTokenService(self, location, nonce, ssecurity):  # noqa: ARG001
            calls.append((location, nonce, ssecurity))
            return "service-token"

    monkeypatch.setattr(auth_module, "MiAccount", _MiAccount)
    out = await auth._try_miaccount_persistent_auth_relogin(
        before=store.get(), reason="ut-code-zero"
    )
    assert out["ok"] is True
    assert out["used_path"] == "miaccount_persistent_auth_exchange"
    assert calls
    assert bool(out.get("need_qr_scan", False)) is False
    assert bool(out.get("long_term_expired", False)) is False


@pytest.mark.asyncio
async def test_auth_call_direct_auth_error_preserves_manual_gate_without_schedule(manager, monkeypatch):
    auth, _ = manager
    auth._enter_manual_login_gate("interactive_captcha_challenge")
    preserve_calls = []
    schedule_calls = []
    original_preserve = auth._preserve_manual_login_gate

    def preserve():
        preserve_calls.append(True)
        return original_preserve()

    async def ensure_auth():
        return True

    async def schedule(*args, **kwargs):  # noqa: ARG001
        schedule_calls.append(True)

    monkeypatch.setattr(auth, "_preserve_manual_login_gate", preserve)
    monkeypatch.setattr(auth, "ensure_auth", ensure_auth)
    monkeypatch.setattr(auth, "_schedule_background_recovery", schedule)

    async def fn():
        raise RuntimeError("Login failed")

    with pytest.raises(RuntimeError, match="Login failed"):
        await auth.auth_call(fn, retry=1, ctx="ut-direct-auth-error")

    assert preserve_calls == [True]
    assert schedule_calls == []
    assert auth._state == auth.STATE_LOCKED
    assert auth._locked_until == 0


@pytest.mark.asyncio
async def test_scheduled_orchestration_70016_null_captcha_suspends_without_second_network(
    manager, monkeypatch
):
    auth, store = manager
    import time

    store.data["saveTime"] = int((time.time() - 13 * 3600) * 1000)
    calls = []

    async def service_login(before=None, reason="", sid="micoapi", writeback=True):  # noqa: ARG001
        calls.append("serviceLogin")
        return {
            "ok": False,
            "error_code": "service_login_failed",
            "failed_reason": "service_login_code_70016 response captchaUrl null",
            **auth._classify_auth_failure(
                "service_login_code_70016", store.get(),
                auth_evidence={"code": 70016, "captchaUrl": None},
            ),
        }

    monkeypatch.setattr(auth, "_try_miaccount_persistent_auth_relogin", service_login)
    async def fallback(**kwargs):  # noqa: ARG001
        return {"ok": False, "error_code": "fallback_disabled"}

    monkeypatch.setattr(auth, "_try_mijia_persistent_auth_relogin", fallback)
    first = await auth._maybe_scheduled_refresh()
    second = await auth._maybe_scheduled_refresh()
    debug = auth.auth_recovery_debug_state()
    assert first is False
    assert second is False
    assert calls == ["serviceLogin"]
    assert debug["scheduled_refresh_suspended"] is True
    assert debug["scheduled_refresh_suspend_code"] == "credential_session_rejected"
    assert debug["state"] == auth.STATE_HEALTHY
    assert debug["recovery_failure_count"] == 0
