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


# --- 快速路径（有会话 token、运行时未绑定）回归测试 ---------------------------------
# 背景：重启后 auth.json 已有 serviceToken 但 mina_service 未绑定。旧实现在这种
# 情况下会跳过重建、直接发起 MiAccount.login("micoapi")，被小米风控拦下。


def _patch_client_session():
    """把 ClientSession patch 成可 await close() 的假对象。"""
    mock_session = MagicMock()
    mock_session.return_value = MagicMock()
    mock_session.return_value.cookie_jar = MagicMock()
    mock_session.return_value.close = AsyncMock()
    return mock_session


def _patch_mi_account():
    """兼容无参/有参两种 MiAccount 构造，并暴露可断言的 login()。"""
    mock_account = MagicMock()
    mock_account.return_value = MagicMock()
    mock_account.return_value.token = {}
    mock_account.return_value.login = AsyncMock()

    def _factory(*args, **kwargs):  # noqa: ARG001
        if not args and not kwargs:
            raise TypeError()
        return mock_account.return_value

    mock_account.side_effect = _factory
    return mock_account


@pytest.mark.asyncio
async def test_fast_path_binds_runtime_without_login(auth_manager):
    """有 serviceToken 且运行时未绑定时：直接用持久 token 重绑，完全不发起 login。"""
    manager, _ = auth_manager
    manager.mina_service = None
    manager.miio_service = None
    manager.login_signature = None
    manager._state = manager.STATE_DEGRADED

    healthy = _HealthyRuntime()
    with (
        patch("xiaomusic.auth.ClientSession", _patch_client_session()),
        patch("xiaomusic.auth.MiAccount", _patch_mi_account()) as mock_account,
        patch("xiaomusic.auth.MiNAService", return_value=healthy),
        patch("xiaomusic.auth.MiIOService", return_value=object()),
    ):
        ok = await manager._try_login(reason="test_fast_path")

    assert ok is True
    assert manager.mina_service is healthy
    assert manager._state == manager.STATE_HEALTHY
    mock_account.return_value.login.assert_not_awaited()
    assert manager._last_fast_rebind_state.get("result") == "ok"


@pytest.mark.asyncio
async def test_fast_path_verify_failure_leaves_no_residue(auth_manager):
    """校验失败时 self 不得被污染（尤其不得把运行时置空），并落回原有登录流程。"""
    manager, _ = auth_manager
    manager.mina_service = None
    manager.miio_service = None
    manager.login_signature = None
    manager._state = manager.STATE_DEGRADED
    keep_account = manager.login_account
    keep_signature = manager.login_signature

    with (
        patch("xiaomusic.auth.ClientSession", _patch_client_session()),
        patch("xiaomusic.auth.MiAccount", _patch_mi_account()),
        patch("xiaomusic.auth.MiNAService", return_value=_FailingRuntime()),
        patch("xiaomusic.auth.MiIOService", return_value=object()),
        patch.object(
            manager,
            "_try_miaccount_persistent_auth_relogin",
            AsyncMock(return_value={"ok": False, "error_code": "test"}),
        ),
        patch.object(
            manager,
            "_try_mijia_persistent_auth_relogin",
            AsyncMock(return_value={"ok": False, "error_code": "test"}),
        ),
    ):
        try:
            await manager._try_login(reason="test_fast_path_verify_fail")
        except Exception:
            pass  # 完整登录同样失败，允许抛出

    assert manager.mina_service is None  # 没有留下半成品运行时
    assert manager.login_account is keep_account  # 没被候选替换
    assert manager.login_signature is keep_signature
    assert manager._last_fast_rebind_state.get("result") == "failed"
    assert manager._last_fast_rebind_state.get("error")


class _FailingRuntimeWithHook:
    """校验必定失败，但在失败前先执行一个钩子——用于模拟"并发的成功认证"。"""

    def __init__(self, hook):
        self._hook = hook

    async def device_list(self):
        if self._hook is not None:
            self._hook()
        raise RuntimeError(
            "Error https://api2.mina.mi.com/admin/v2/device_list: Login failed"
        )


@pytest.mark.asyncio
async def test_fast_path_failure_does_not_clobber_concurrent_runtime(auth_manager):
    """失败路径不得写回 self，否则会抹掉并发成功建立的运行时。

    这是 4cf0abd → eb1e56e 的**行为差异门禁**（不同于靠新属性是否存在）：
    旧实现在候选校验失败时会执行 `self.mina_service = None`，把这里由钩子预置的
    "另一个协程刚装上的运行时"抹掉；新实现不再写回，该运行时必须存活。
    """
    manager, _ = auth_manager
    manager.mina_service = None
    manager.miio_service = None
    manager.login_signature = None
    manager._state = manager.STATE_DEGRADED

    concurrent_runtime = _HealthyRuntime()

    def _concurrent_success():
        # 模拟"另一个协程在候选校验期间完成了认证并装入运行时"
        manager.mina_service = concurrent_runtime
        manager.login_signature = manager._get_login_signature()

    mock_account = _patch_mi_account()
    mock_account.return_value.login = AsyncMock(return_value=False)

    with (
        patch("xiaomusic.auth.ClientSession", _patch_client_session()),
        patch("xiaomusic.auth.MiAccount", mock_account),
        patch(
            "xiaomusic.auth.MiNAService",
            return_value=_FailingRuntimeWithHook(_concurrent_success),
        ),
        patch("xiaomusic.auth.MiIOService", return_value=object()),
        patch.object(
            manager,
            "_try_miaccount_persistent_auth_relogin",
            AsyncMock(return_value={"ok": False, "error_code": "test"}),
        ),
        patch.object(
            manager,
            "_try_mijia_persistent_auth_relogin",
            AsyncMock(return_value={"ok": False, "error_code": "test"}),
        ),
    ):
        try:
            await manager._try_login(reason="test_fast_path_concurrent")
        except Exception:
            pass

    assert manager.mina_service is concurrent_runtime
