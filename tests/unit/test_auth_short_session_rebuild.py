from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import xiaomusic.auth as auth_module
from xiaomusic.auth import SimpleAuthManager

pytest.importorskip("aiohttp")


class _TokenStore:
    def __init__(self, data: dict, path: Path):
        self._data = dict(data)
        self.path = path
        self.updates: list[tuple[dict, str]] = []

    def get(self):
        return dict(self._data)

    def update(self, data: dict, reason: str = ""):
        self._data = dict(data)
        self.updates.append((dict(data), reason))

    def flush(self):
        return None


class _DeviceManager:
    async def update_device_info(self, auth):  # noqa: ARG002
        return None


class _Log:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class _FakeCookieJar:
    def update_cookies(self, cookies):  # noqa: ARG002
        return None


class _FakeClientSession:
    def __init__(self, *args, **kwargs):  # noqa: ARG002
        self.cookie_jar = _FakeCookieJar()

    async def close(self):
        return None


@pytest.fixture
def auth_manager(tmp_path: Path, monkeypatch):
    conf_path = tmp_path / "conf"
    conf_path.mkdir(parents=True, exist_ok=True)
    token_path = conf_path / "auth.json"
    token_path.write_text("{}", encoding="utf-8")
    config = SimpleNamespace(
        conf_path=str(conf_path),
        auth_token_path=str(token_path),
        mi_did="",
        devices={},
        get_one_device_id=lambda: "device-1",
    )
    monkeypatch.setattr(auth_module, "ClientSession", _FakeClientSession)
    store = _TokenStore({}, token_path)
    manager = SimpleAuthManager(config, _Log(), _DeviceManager(), token_store=store)
    return manager, store


@pytest.mark.asyncio
async def test_rebuild_short_session_from_persistent_auth_fails_when_persistent_auth_missing(auth_manager):
    manager, _ = auth_manager

    out = await manager.rebuild_short_session_from_persistent_auth(reason="ut-missing")

    assert out["ok"] is False
    assert out["error_code"] == "missing_persistent_auth_fields"
    state = manager.auth_short_session_rebuild_debug_state()["last_short_session_rebuild"]
    assert state["result"] == "failed"
    assert state["error_code"] == "missing_persistent_auth_fields"


@pytest.mark.asyncio
async def test_rebuild_short_session_from_persistent_auth_records_success_state(auth_manager, monkeypatch):
    manager, store = auth_manager
    store.update(
        {
            "userId": "u",
            "passToken": "p",
            "psecurity": "ps",
            "ssecurity": "ss",
            "cUserId": "cu",
            "deviceId": "d",
        },
        reason="seed",
    )

    async def _fake_relogin(before=None, reason="", sid="micoapi"):
        return {
            "ok": True,
            "used_path": "miaccount_persistent_auth_login",
            "serviceToken": "st",
            "yetAnotherServiceToken": "st2",
        }

    async def _fake_rebind(auth_data):
        manager.mina_service = SimpleNamespace(device_list=lambda: _device_list())
        manager.login_signature = manager._get_login_signature()
        return {"ok": True, "result": "ok"}

    async def _device_list():
        return []

    monkeypatch.setattr(manager, "_try_miaccount_persistent_auth_relogin", _fake_relogin)
    monkeypatch.setattr(manager, "_rebind_runtime_from_auth_data", _fake_rebind)

    out = await manager.rebuild_short_session_from_persistent_auth(reason="ut-success")

    assert out["ok"] is True
    assert out["used_path"] == "miaccount_persistent_auth_login"
    assert out["service_token_written"] is True
    state = manager.auth_short_session_rebuild_debug_state()["last_short_session_rebuild"]
    assert state["result"] == "ok"
    assert state["verify_result"] == "ok"
    assert store.get()["serviceToken"] == "st"
    assert store.get()["yetAnotherServiceToken"] == "st2"


@pytest.mark.asyncio
async def test_rebuild_short_session_from_persistent_auth_records_verify_failure(auth_manager, monkeypatch):
    manager, store = auth_manager
    store.update(
        {
            "userId": "u",
            "passToken": "p",
            "psecurity": "ps",
            "ssecurity": "ss",
            "cUserId": "cu",
            "deviceId": "d",
        },
        reason="seed",
    )

    async def _fake_relogin(before=None, reason="", sid="micoapi"):
        return {
            "ok": True,
            "used_path": "miaccount_persistent_auth_login",
            "serviceToken": "st",
        }

    async def _fake_rebind(auth_data):
        async def _device_list():
            raise RuntimeError("verify boom")

        manager.mina_service = SimpleNamespace(device_list=_device_list)
        manager.login_signature = manager._get_login_signature()
        return {"ok": True, "result": "ok"}

    monkeypatch.setattr(manager, "_try_miaccount_persistent_auth_relogin", _fake_relogin)
    monkeypatch.setattr(manager, "_rebind_runtime_from_auth_data", _fake_rebind)

    out = await manager.rebuild_short_session_from_persistent_auth(reason="ut-verify-fail")

    assert out["ok"] is False
    assert out["error_code"] == "verify_failed"
    state = manager.auth_short_session_rebuild_debug_state()["last_short_session_rebuild"]
    assert state["result"] == "failed"
    assert state["verify_result"] == "failed"


@pytest.mark.asyncio
async def test_try_miaccount_persistent_auth_relogin_writes_service_token(auth_manager, monkeypatch):
    manager, store = auth_manager
    store.update(
        {
            "userId": "u",
            "passToken": "p",
            "psecurity": "ps",
            "ssecurity": "ss",
            "cUserId": "cu",
            "deviceId": "d",
        },
        reason="seed",
    )

    class _FakeMiAccount:
        def __init__(self, *args, **kwargs):  # noqa: ARG002
            self.token = {}

        async def _serviceLogin(self, path):
            return {
                "code": 0,
                "location": "https://api2.mina.mi.com/sts?nonce=abc",
                "ssecurity": "ss-new",
            }

        async def _securityTokenService(self, location, nonce, ssecurity):  # noqa: ARG002
            assert nonce == "abc"
            assert ssecurity == "ss-new"
            return "new-st"

    monkeypatch.setattr(auth_module, "MiAccount", _FakeMiAccount)

    out = await manager._try_miaccount_persistent_auth_relogin(
        before=store.get(), reason="ut-primary", sid="micoapi"
    )

    assert out["ok"] is True
    assert out["used_path"] == "miaccount_persistent_auth_login"
    assert store.get()["serviceToken"] == "new-st"
    assert store.get()["yetAnotherServiceToken"] == "new-st"


@pytest.mark.asyncio
async def test_try_login_prefers_rebuild_when_short_session_missing(auth_manager, monkeypatch):
    manager, store = auth_manager
    store.update(
        {
            "userId": "u",
            "passToken": "p",
            "psecurity": "ps",
            "ssecurity": "ss",
            "cUserId": "cu",
            "deviceId": "d",
        },
        reason="seed",
    )

    class _FailMiAccount:
        def __init__(self, *args, **kwargs):  # noqa: ARG002
            raise AssertionError("full login path should not be used when short session is missing")

    async def _fake_rebuild(reason=""):
        store.update(
            {
                **store.get(),
                "serviceToken": "rebuilt-st",
                "yetAnotherServiceToken": "rebuilt-st",
            },
            reason="ut-rebuild-success",
        )
        manager._record_short_session_rebuild_state(
            {
                "ok": True,
                "result": "ok",
                "used_path": "miaccount_persistent_auth_login",
                "service_token_written": True,
                "runtime_rebind_result": "ok",
                "verify_result": "ok",
            }
        )
        manager.mina_service = object()
        return {
            "ok": True,
            "result": "ok",
            "used_path": "miaccount_persistent_auth_login",
            "service_token_written": True,
            "runtime_rebind_result": "ok",
            "verify_result": "ok",
        }

    monkeypatch.setattr(auth_module, "MiAccount", _FailMiAccount)
    monkeypatch.setattr(manager, "rebuild_short_session_from_persistent_auth", _fake_rebuild)

    ok = await manager._try_login(reason="ut-mainline-success")

    assert ok is True
    assert manager.auth_debug_state()["auth_mode"] == "healthy"
    public_status = manager.map_auth_public_status(runtime_auth_ready=True)
    assert public_status["status_reason"] == "healthy"


@pytest.mark.asyncio
async def test_try_login_short_session_rebuild_failure_exposes_rebuild_failed_status(auth_manager, monkeypatch):
    manager, store = auth_manager
    store.update(
        {
            "userId": "u",
            "passToken": "p",
            "psecurity": "ps",
            "ssecurity": "ss",
            "cUserId": "cu",
            "deviceId": "d",
        },
        reason="seed",
    )

    class _FailMiAccount:
        def __init__(self, *args, **kwargs):  # noqa: ARG002
            raise AssertionError("full login path should not be used when short session is missing")

    async def _fake_rebuild(reason=""):
        manager._record_short_session_rebuild_state(
            {
                "ok": False,
                "result": "failed",
                "used_path": "miaccount_persistent_auth_login",
                "error_code": "redirect_http_401",
                "failed_reason": "redirect_http_401",
                "service_token_written": False,
                "runtime_rebind_result": "skipped",
                "verify_result": "skipped",
            }
        )
        return {
            "ok": False,
            "result": "failed",
            "used_path": "miaccount_persistent_auth_login",
            "error_code": "redirect_http_401",
            "failed_reason": "redirect_http_401",
            "service_token_written": False,
            "runtime_rebind_result": "skipped",
            "verify_result": "skipped",
        }

    monkeypatch.setattr(auth_module, "MiAccount", _FailMiAccount)
    monkeypatch.setattr(manager, "rebuild_short_session_from_persistent_auth", _fake_rebuild)

    ok = await manager._try_login(reason="ut-mainline-fail")

    assert ok is False
    public_status = manager.map_auth_public_status(runtime_auth_ready=False)
    assert public_status["status_reason"] == "short_session_rebuild_failed"
    assert public_status["rebuild_error_code"] == "redirect_http_401"


@pytest.mark.asyncio
async def test_rebuild_short_session_fallback_path_success_is_observable(auth_manager, monkeypatch):
    manager, store = auth_manager
    store.update(
        {
            "userId": "u",
            "passToken": "p",
            "psecurity": "ps",
            "ssecurity": "ss",
            "cUserId": "cu",
            "deviceId": "d",
        },
        reason="seed",
    )

    async def _primary(before=None, reason="", sid="micoapi"):
        return {
            "ok": False,
            "used_path": "miaccount_persistent_auth_login",
            "error_code": "redirect_missing_nonce",
            "failed_reason": "service_login_response_missing_nonce",
        }

    async def _fallback(auth_dir=None, sid="micoapi"):
        store.update(
            {
                **store.get(),
                "serviceToken": "fallback-st",
                "yetAnotherServiceToken": "fallback-st",
            },
            reason="ut-fallback-success",
        )
        return {
            "ok": True,
            "used_path": "mijia_persistent_auth_login",
            "writeback_target": "token_store",
            "sid": sid,
        }

    async def _fake_rebind(auth_data):
        manager.mina_service = SimpleNamespace(device_list=lambda: _device_list())
        manager.login_signature = manager._get_login_signature()
        return {"ok": True, "result": "ok"}

    async def _device_list():
        return []

    monkeypatch.setattr(manager, "_try_miaccount_persistent_auth_relogin", _primary)
    monkeypatch.setattr(manager, "_try_mijia_persistent_auth_relogin", _fallback)
    monkeypatch.setattr(manager, "_rebind_runtime_from_auth_data", _fake_rebind)

    out = await manager.rebuild_short_session_from_persistent_auth(reason="ut-fallback")

    assert out["ok"] is True
    assert out["used_path"] == "mijia_persistent_auth_login"
    dbg = manager.auth_short_session_rebuild_debug_state()
    assert dbg["last_short_session_rebuild"]["result"] == "ok"
    assert dbg["last_auth_recovery_flow"]["result"] == "ok"
    assert dbg["last_auth_recovery_flow"]["primary_attempt"]["result"] == "failed"
    assert dbg["last_auth_recovery_flow"]["fallback_attempt"]["result"] == "ok"
    assert dbg["last_auth_recovery_flow"]["used_path"] == "mijia_persistent_auth_login"


@pytest.mark.asyncio
async def test_rebuild_short_session_flow_records_failed_fallback_and_public_status(auth_manager, monkeypatch):
    manager, store = auth_manager
    store.update(
        {
            "userId": "u",
            "passToken": "p",
            "psecurity": "ps",
            "ssecurity": "ss",
            "cUserId": "cu",
            "deviceId": "d",
        },
        reason="seed",
    )

    async def _primary(before=None, reason="", sid="micoapi"):
        return {
            "ok": False,
            "used_path": "miaccount_persistent_auth_login",
            "error_code": "redirect_missing_nonce",
            "failed_reason": "service_login_response_missing_nonce",
        }

    async def _fallback(auth_dir=None, sid="micoapi"):
        return {
            "ok": False,
            "used_path": "mijia_persistent_auth_login",
            "error_code": "redirect_http_401",
            "failed_reason": "redirect_http_401",
        }

    monkeypatch.setattr(manager, "_try_miaccount_persistent_auth_relogin", _primary)
    monkeypatch.setattr(manager, "_try_mijia_persistent_auth_relogin", _fallback)

    out = await manager.rebuild_short_session_from_persistent_auth(reason="ut-fallback-fail")

    assert out["ok"] is False
    assert out["used_path"] == "mijia_persistent_auth_login"
    dbg = manager.auth_short_session_rebuild_debug_state()
    assert dbg["last_auth_recovery_flow"]["result"] == "failed"
    assert dbg["last_auth_recovery_flow"]["fallback_attempt"]["error_code"] == "redirect_http_401"
    public_status = manager.map_auth_public_status(runtime_auth_ready=False)
    assert public_status["status_reason"] == "short_session_rebuild_failed"
    assert public_status["rebuild_error_code"] == "redirect_http_401"
