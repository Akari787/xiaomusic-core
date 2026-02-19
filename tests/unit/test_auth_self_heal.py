import json
import asyncio
import sys
import types
from pathlib import Path

import pytest
import pytest_asyncio

@pytest.fixture(autouse=True)
def _stub_miservice_module():
    if "miservice" in sys.modules:
        yield
        return

    stub = types.ModuleType("miservice")

    class _MiAccount:  # noqa: D401
        def __init__(self, *args, **kwargs):
            self.token = {}

        async def login(self, *args, **kwargs):
            return None

    class _MiNAService:
        def __init__(self, *args, **kwargs):
            pass

        async def device_list(self):
            return []

    class _MiIOService:
        def __init__(self, *args, **kwargs):
            pass

    stub.MiAccount = _MiAccount
    stub.MiNAService = _MiNAService
    stub.MiIOService = _MiIOService
    sys.modules["miservice"] = stub
    try:
        yield
    finally:
        sys.modules.pop("miservice", None)


class _DummyLog:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None


class _DummyDeviceManager:
    async def update_device_info(self, auth):  # noqa: ARG002
        return None


class _DummyConfig:
    def __init__(self, base: Path):
        self.conf_path = str(base)
        self.oauth2_token_path = str(base / "auth.json")
        self.mi_did = "981257654"
        self.devices = {}

    def get_one_device_id(self):
        return "dev0001"


@pytest_asyncio.fixture
async def auth_manager(tmp_path):
    from xiaomusic.auth import AuthManager

    cfg = _DummyConfig(tmp_path)
    Path(cfg.oauth2_token_path).write_text(
        json.dumps(
            {
                "passToken": "x",
                "userId": "u",
                "serviceToken": "st",
                "ssecurity": "ss",
                "deviceId": "d1",
            }
        ),
        encoding="utf-8",
    )
    auth = AuthManager(cfg, _DummyLog(), _DummyDeviceManager())
    yield auth
    await auth.mi_session.close()


@pytest.mark.asyncio
async def test_auth_call_triggers_relogin_and_retries_once(auth_manager):
    calls = {"fn": 0, "ensure": 0}

    async def _ensure(force=False, reason=""):  # noqa: ARG001
        calls["ensure"] += 1
        return True

    auth_manager.ensure_logged_in = _ensure

    async def fn():
        calls["fn"] += 1
        if calls["fn"] == 1:
            raise RuntimeError("401 unauthorized")
        return "OK"

    out = await auth_manager.auth_call(fn, retry=1, ctx="ut-auth-call")
    assert out == "OK"
    assert calls["ensure"] == 1
    assert calls["fn"] == 2


@pytest.mark.asyncio
async def test_concurrent_auth_call_only_one_relogin(auth_manager):
    relogin_calls = {"count": 0}

    async def _need_login():
        return False

    async def _init_all_data():
        relogin_calls["count"] += 1
        auth_manager.mina_service = object()
        auth_manager.login_signature = auth_manager._get_login_signature()

    auth_manager.need_login = _need_login
    auth_manager.init_all_data = _init_all_data
    auth_manager._last_ok_ts = 0

    def make_fn(i):
        state = {"n": 0}

        async def fn():
            if state["n"] == 0:
                state["n"] += 1
                raise RuntimeError("Login failed: unauthorized")
            return i

        return fn

    tasks = [auth_manager.auth_call(make_fn(i), retry=1, ctx=f"concurrent-{i}") for i in range(10)]
    results = await asyncio.gather(*tasks)
    assert results == list(range(10))
    assert relogin_calls["count"] == 1


@pytest.mark.parametrize(
    ("exc", "status", "body", "expected"),
    [
        (RuntimeError("any"), 401, None, True),
        (RuntimeError("any"), 403, None, True),
        (RuntimeError("Login failed"), None, None, True),
        (RuntimeError("unauthorized"), None, None, True),
        (RuntimeError("other"), None, {"message": "invalid token"}, True),
        (RuntimeError("timeout"), None, "network timeout", False),
        (RuntimeError("500"), 500, None, False),
    ],
)
def test_is_auth_error_matrix(exc, status, body, expected):
    from xiaomusic.auth import is_auth_error

    resp = None
    if status is not None:
        resp = type("_Resp", (), {"status": status})()
    assert is_auth_error(exc=exc, resp=resp, body=body) is expected


@pytest.mark.asyncio
async def test_keepalive_degrades_after_three_failures(auth_manager, monkeypatch):
    delays = []

    async def _sleep(delay):
        delays.append(delay)
        if len(delays) >= 3:
            raise asyncio.CancelledError()

    async def _ensure(force=False, reason=""):  # noqa: ARG001
        return False

    async def _mina_call(*args, **kwargs):  # noqa: ARG001
        raise RuntimeError("401 unauthorized")

    monkeypatch.setattr(asyncio, "sleep", _sleep)
    auth_manager.ensure_logged_in = _ensure
    auth_manager.mina_call = _mina_call

    with pytest.raises(asyncio.CancelledError):
        await auth_manager.keepalive_loop(interval_sec=5)

    assert auth_manager._keepalive_degraded is True
    assert auth_manager._keepalive_fail_streak == 3
    assert delays == [30, 60, 300]


@pytest.mark.asyncio
async def test_keepalive_recovers_from_degraded(auth_manager, monkeypatch):
    delays = []

    async def _sleep(delay):
        delays.append(delay)
        raise asyncio.CancelledError()

    async def _ensure(force=False, reason=""):  # noqa: ARG001
        return False

    async def _mina_call(*args, **kwargs):  # noqa: ARG001
        return []

    monkeypatch.setattr(asyncio, "sleep", _sleep)
    auth_manager.ensure_logged_in = _ensure
    auth_manager.mina_call = _mina_call
    auth_manager._keepalive_degraded = True
    auth_manager._keepalive_fail_streak = 3

    with pytest.raises(asyncio.CancelledError):
        await auth_manager.keepalive_loop(interval_sec=7)

    assert auth_manager._keepalive_degraded is False
    assert auth_manager._keepalive_fail_streak == 0
    assert delays == [7]
