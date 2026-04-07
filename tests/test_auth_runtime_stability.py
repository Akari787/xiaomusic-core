import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _stub_miservice_module():
    if "aiohttp" not in sys.modules:
        aiohttp_stub = types.ModuleType("aiohttp")
        aiohttp_abc_stub = types.ModuleType("aiohttp.abc")

        class _ClientSession:
            def __init__(self, *args, **kwargs):
                self.cookie_jar = MagicMock()

            async def close(self):
                return None

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

        class _ClientTimeout:
            def __init__(self, *args, **kwargs):
                return None

        class _TCPConnector:
            def __init__(self, *args, **kwargs):
                return None

        class _ClientError(Exception):
            pass

        class _ClientConnectionError(_ClientError):
            pass

        class _AbstractResolver:
            async def resolve(self, *args, **kwargs):
                return []

            async def close(self):
                return None

        aiohttp_stub.ClientSession = _ClientSession
        aiohttp_stub.ClientTimeout = _ClientTimeout
        aiohttp_stub.TCPConnector = _TCPConnector
        aiohttp_stub.ClientError = _ClientError
        aiohttp_stub.ClientConnectionError = _ClientConnectionError
        aiohttp_abc_stub.AbstractResolver = _AbstractResolver
        aiohttp_stub.abc = aiohttp_abc_stub
        sys.modules["aiohttp"] = aiohttp_stub
        sys.modules["aiohttp.abc"] = aiohttp_abc_stub

    if "miservice" in sys.modules:
        try:
            yield
        finally:
            sys.modules.pop("aiohttp", None)
            sys.modules.pop("aiohttp.abc", None)
        return

    stub = types.ModuleType("miservice")

    class _MiAccount:
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
        sys.modules.pop("aiohttp", None)
        sys.modules.pop("aiohttp.abc", None)


class _DummyLog:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None


class _DummyConfig:
    def __init__(self, base: Path):
        self.conf_path = str(base)
        self.auth_token_path = str(base / "auth.json")
        self.mi_did = "981257654"
        self.devices = {}

    def get_one_device_id(self):
        return "dev0001"


class _DummyDeviceManager:
    async def update_device_info(self, auth):  # noqa: ARG002
        return None


class _DummyTokenStore:
    def __init__(self, data: dict):
        self._data = dict(data)
        self.updated = []

    def get(self):
        return dict(self._data)

    def update(self, data, reason=""):
        self.updated.append((dict(data), reason))
        self._data.update(data)

    def flush(self):
        return None

    def reload_from_disk(self):
        return None


class _HealthyRuntime:
    async def device_list(self):
        return [{"deviceID": "old"}]


class _FailingRuntime:
    async def device_list(self):
        raise RuntimeError(
            "Error https://api2.mina.mi.com/admin/v2/device_list: Login failed"
        )


@pytest.fixture
def auth_manager(tmp_path):
    from xiaomusic.auth import AuthManager

    token = {
        "passToken": "pass-token",
        "userId": "user-id",
        "psecurity": "psec",
        "ssecurity": "ssec",
        "cUserId": "cuser-id",
        "deviceId": "device-id",
        "serviceToken": "short-token",
        "yetAnotherServiceToken": "short-token",
    }
    token_store = _DummyTokenStore(token)
    config = _DummyConfig(tmp_path)

    with patch("xiaomusic.auth.ClientSession") as mock_session:
        mock_session.return_value = MagicMock()
        mock_session.return_value.cookie_jar = MagicMock()
        manager = AuthManager(config, _DummyLog(), _DummyDeviceManager(), token_store)

    manager.mina_service = _HealthyRuntime()
    manager.miio_service = object()
    manager.login_account = object()
    manager.login_signature = manager._get_login_signature()
    manager._state = manager.STATE_HEALTHY
    return manager, token_store


@pytest.mark.asyncio
async def test_manual_reload_failure_preserves_healthy_runtime(auth_manager):
    manager, _ = auth_manager
    old_runtime = manager.mina_service
    with (
        patch("xiaomusic.auth.MiAccount") as mock_account,
        patch("xiaomusic.auth.MiNAService", return_value=_FailingRuntime()),
        patch("xiaomusic.auth.MiIOService", return_value=object()),
    ):
        mock_account.return_value = MagicMock()
        mock_account.return_value.token = {}

        def _factory(*args, **kwargs):  # noqa: ARG001
            if not args and not kwargs:
                raise TypeError()
            return mock_account.return_value

        mock_account.side_effect = _factory

        async def _login(*args, **kwargs):  # noqa: ARG001
            mock_account.return_value.token["micoapi"] = ("ssecurity", "service-token")
            mock_account.return_value.token["serviceToken"] = "service-token"
            mock_account.return_value.token["yetAnotherServiceToken"] = "service-token"
            return True

        mock_account.return_value.login = AsyncMock(side_effect=_login)
        out = await manager.manual_reload_runtime(reason="ut-runtime-reload")

    assert out["refreshed"] is False
    assert out["runtime_auth_ready"] is True
    assert out["state_before"] == manager.STATE_HEALTHY
    assert out["state_after"] == manager.STATE_HEALTHY
    trace = manager._last_login_trace
    assert trace["login_result"] is True
    assert trace["verify_attempted"] is True
    assert trace["runtime_swap_attempted"] is True
    assert trace["runtime_swap_applied"] is False
    assert manager.mina_service is old_runtime
    assert out["need_qr_scan"] is False
    assert out["user_action_required"] is False
    assert out["long_term_expired"] is False


@pytest.mark.asyncio
async def test_try_login_uses_fresh_login_session(auth_manager):
    manager, _ = auth_manager
    old_session = manager.mi_session
    with (
        patch("xiaomusic.auth.MiAccount") as mock_account,
        patch("xiaomusic.auth.MiNAService", return_value=_FailingRuntime()),
        patch("xiaomusic.auth.MiIOService", return_value=object()),
    ):
        mock_account.return_value = MagicMock()
        mock_account.return_value.token = {}

        def _factory(*args, **kwargs):  # noqa: ARG001
            if not args and not kwargs:
                raise TypeError()
            return mock_account.return_value

        mock_account.side_effect = _factory

        async def _login(*args, **kwargs):  # noqa: ARG001
            mock_account.return_value.token["micoapi"] = ("ssecurity", "service-token")
            mock_account.return_value.token["serviceToken"] = "service-token"
            mock_account.return_value.token["yetAnotherServiceToken"] = "service-token"
            return True

        mock_account.return_value.login = AsyncMock(side_effect=_login)
        out = await manager._try_login(
            reason="ut-fresh-session", preserve_healthy_runtime=False
        )

    assert out is False
    assert manager.mi_session is old_session
    assert manager._last_login_trace["login_result"] is True
    assert manager._last_login_trace["verify_attempted"] is True
    assert manager._last_login_trace["runtime_swap_attempted"] is True
    assert manager._last_login_trace["runtime_swap_applied"] is False
    assert mock_account.call_args is not None
    assert mock_account.call_args.args[0] is not old_session


@pytest.mark.asyncio
async def test_try_login_verify_failure_keeps_existing_runtime(auth_manager):
    manager, _ = auth_manager
    old_runtime = manager.mina_service
    with (
        patch("xiaomusic.auth.MiAccount") as mock_account,
        patch("xiaomusic.auth.MiNAService", return_value=_FailingRuntime()),
        patch("xiaomusic.auth.MiIOService", return_value=object()),
    ):
        mock_account.return_value = MagicMock()
        mock_account.return_value.token = {}

        async def _login(*args, **kwargs):  # noqa: ARG001
            mock_account.return_value.token["micoapi"] = ("ssecurity", "service-token")
            mock_account.return_value.token["serviceToken"] = "service-token"
            mock_account.return_value.token["yetAnotherServiceToken"] = "service-token"
            return True

        mock_account.return_value.login = AsyncMock(side_effect=_login)
        out = await manager._try_login(
            reason="ut-try-login", preserve_healthy_runtime=False
        )

    assert out is False
    assert manager.mina_service is old_runtime
    assert manager._state in {manager.STATE_DEGRADED, manager.STATE_LOCKED}
    assert manager._last_login_trace["login_result"] is True
    assert manager._last_login_trace["runtime_swap_attempted"] is True
    assert manager._last_login_trace["runtime_swap_applied"] is False
    assert manager._last_login_trace["verify_attempted"] is True
    assert manager._last_login_trace["verify_method"] == "device_list"
    assert manager._last_login_trace["candidate_runtime_account_ready"] is True


@pytest.mark.asyncio
async def test_try_login_login_failure_stops_before_verify(auth_manager):
    manager, _ = auth_manager
    with (
        patch("xiaomusic.auth.MiAccount") as mock_account,
        patch("xiaomusic.auth.MiNAService") as mock_mina,
        patch("xiaomusic.auth.MiIOService") as mock_miio,
    ):
        mock_account.return_value = MagicMock()
        mock_account.return_value.token = {}
        mock_account.return_value.login = AsyncMock(return_value=False)

        out = await manager._try_login(reason="ut-login-failed", preserve_healthy_runtime=False)

    assert out is False
    assert manager._last_recovery_stage == "login"
    assert manager._last_login_trace["login_result"] is False
    assert manager._last_login_trace["verify_attempted"] is False
    assert manager._last_login_trace["runtime_swap_attempted"] is False
    assert manager._last_login_trace["candidate_runtime_account_ready"] is False
    assert manager._last_login_trace["token_changed_after_login"] is False
    assert mock_mina.call_count == 0
    assert mock_miio.call_count == 0


@pytest.mark.asyncio
async def test_background_recovery_needs_threshold_before_lock(auth_manager):
    manager, _ = auth_manager
    manager._state = manager.STATE_DEGRADED
    manager._retry_count = 1
    manager._max_retries = 3

    async def _retry_fail(reason=""):
        manager._retry_count += 1
        manager._last_recovery_error_code = "auth_error"
        manager._last_recovery_error_message = "Login failed"
        return False

    manager._try_login = _retry_fail
    manager._schedule_background_recovery()
    await manager._recovery_task

    assert manager._state == manager.STATE_DEGRADED
    assert manager.is_auth_locked() is False
    assert manager._retry_count == 2


@pytest.mark.asyncio
async def test_auth_call_network_error_does_not_trigger_recovery(auth_manager):
    manager, _ = auth_manager
    calls = {"schedule": 0}

    async def _ensure_auth(*args, **kwargs):  # noqa: ARG001
        return False

    def _schedule_background_recovery():
        calls["schedule"] += 1

    async def _fn():
        raise RuntimeError("connection timeout")

    manager.ensure_auth = _ensure_auth
    manager._last_recovery_error_code = "network_error"
    manager._last_error = "connection timeout"
    manager._schedule_background_recovery = _schedule_background_recovery

    with pytest.raises(RuntimeError):
        await manager.auth_call(_fn, retry=1, ctx="ut-network")

    assert calls["schedule"] == 0


def test_generic_login_failed_is_not_long_term_expired(auth_manager):
    manager, _ = auth_manager
    out = manager._classify_auth_failure(
        "Error https://api2.mina.mi.com/admin/v2/device_list: Login failed",
        manager._get_auth_data(),
    )
    assert out["error_type"] == "auth_error"
    assert out["long_term_expired"] is False
    assert out["need_qr_scan"] is False
    assert out["user_action_required"] is False


def test_network_error_is_not_auth_expiration(auth_manager):
    manager, _ = auth_manager
    out = manager._classify_auth_failure("connection timeout", manager._get_auth_data())
    assert out["error_type"] == "network_error"
    assert out["long_term_expired"] is False
    assert out["need_qr_scan"] is False
    assert out["user_action_required"] is False


def test_auth_manager_alias_remains_available():
    from xiaomusic.auth import AuthManager, SimpleAuthManager

    assert AuthManager is SimpleAuthManager
