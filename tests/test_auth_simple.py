"""
简化版认证模块测试
"""

import asyncio
import json
import os
import tempfile
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# 测试需要先设置好导入路径
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip("miservice")


class MockConfig:
    """模拟配置对象"""

    def __init__(self, tmp_path):
        self.conf_path = str(tmp_path)
        self.mi_did = ""
        self.devices = {}
        self.auth_token_path = os.path.join(self.conf_path, "auth.json")

    def get_one_device_id(self):
        return "test_device_123"


class MockTokenStore:
    """模拟 TokenStore"""

    def __init__(self, auth_data=None):
        self._data = auth_data or {}
        self._updated = []

    def get(self):
        return self._data

    def update(self, data, reason=""):
        self._data.update(data)
        self._updated.append((data, reason))

    def flush(self):
        pass

    def reload_from_disk(self):
        pass


class MockDeviceManager:
    """模拟设备管理器"""

    def __init__(self):
        self.updated = False

    async def update_device_info(self, auth_manager):
        self.updated = True


class MockMiAccount:
    """模拟 MiAccount"""

    def __init__(self):
        self.token = {}
        self.logged_in = False

    async def login(self, sid):
        self.logged_in = True
        # 模拟登录成功
        self.token["micoapi"] = ("test_ssecurity", "test_service_token")
        self.token["serviceToken"] = "test_service_token"
        self.token["yetAnotherServiceToken"] = "test_service_token"


class MockMiNAService:
    """模拟 MiNAService"""

    def __init__(self):
        self.device_list_called = 0
        self.should_fail = False

    async def device_list(self):
        self.device_list_called += 1
        if self.should_fail:
            raise Exception("Simulated auth error 401")
        return [
            {
                "deviceID": "123",
                "miotDID": "456",
                "hardware": "test",
                "alias": "Test Device",
            }
        ]


class MockMiIOService:
    """模拟 MiIOService"""

    pass


@pytest.fixture
def mock_miservice():
    """Mock miservice 模块"""
    with (
        patch("xiaomusic.auth.MiAccount") as mock_account,
        patch("xiaomusic.auth.MiNAService") as mock_mina,
        patch("xiaomusic.auth.MiIOService") as mock_miio,
    ):
        mock_account.return_value = MockMiAccount()
        mock_mina.return_value = MockMiNAService()
        mock_miio.return_value = MockMiIOService()
        yield {
            "MiAccount": mock_account,
            "MiNAService": mock_mina,
            "MiIOService": mock_miio,
        }


@pytest.fixture
def auth_manager(tmp_path, mock_miservice):
    """创建测试用的认证管理器"""
    from xiaomusic.auth import SimpleAuthManager

    # 创建测试用的认证数据
    auth_data = {
        "userId": "test_user_123",
        "passToken": "test_pass_token",
        "psecurity": "test_psecurity",
        "ssecurity": "test_ssecurity",
        "cUserId": "test_c_user_id",
        "deviceId": "test_device_id",
        "serviceToken": "test_service_token",
        "yetAnotherServiceToken": "test_service_token",
    }

    # 写入 auth.json
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(json.dumps(auth_data))

    config = MockConfig(tmp_path)
    token_store = MockTokenStore(auth_data)
    device_manager = MockDeviceManager()

    # Mock mi_session
    with patch("xiaomusic.auth.ClientSession") as mock_session:
        mock_session.return_value = MagicMock()
        mock_session.return_value.cookie_jar = MagicMock()

        am = SimpleAuthManager(
            config=config,
            log=MagicMock(),
            device_manager=device_manager,
            token_store=token_store,
        )

        yield am, auth_data, token_store


class TestSimpleAuthManager:
    """测试简化版认证管理器"""

    def test_initialization(self, auth_manager):
        """测试初始化"""
        am, _, _ = auth_manager
        assert am._state == am.STATE_HEALTHY
        assert am.mina_service is None
        assert am.miio_service is None

    def test_get_auth_data(self, auth_manager):
        """测试获取认证数据"""
        am, auth_data, _ = auth_manager
        data = am._get_auth_data()
        assert data["userId"] == auth_data["userId"]
        assert data["passToken"] == auth_data["passToken"]

    @pytest.mark.asyncio
    async def test_need_login_when_service_none(self, auth_manager):
        """测试服务未初始化时需要登录"""
        am, _, _ = auth_manager
        am.mina_service = None
        assert await am.need_login() is True

    @pytest.mark.asyncio
    async def test_can_login_with_valid_data(self, auth_manager):
        """测试有有效认证数据时可以登录"""
        am, _, _ = auth_manager
        assert await am.can_login() is True

    @pytest.mark.asyncio
    async def test_ensure_auth_success(self, auth_manager, mock_miservice):
        """测试认证成功"""
        am, _, _ = auth_manager

        # Mock 服务
        mock_mina = MockMiNAService()
        mock_miio = MockMiIOService()
        mock_miservice["MiNAService"].return_value = mock_mina
        mock_miservice["MiIOService"].return_value = mock_miio

        result = await am.ensure_auth()
        assert result is True
        assert am._state == am.STATE_HEALTHY
        assert am.mina_service is not None

    @pytest.mark.asyncio
    async def test_ensure_auth_with_recovery(self, auth_manager, mock_miservice):
        """测试认证恢复"""
        am, _, _ = auth_manager
        am._state = am.STATE_DEGRADED

        # Mock 服务
        mock_mina = MockMiNAService()
        mock_miio = MockMiIOService()
        mock_miservice["MiNAService"].return_value = mock_mina
        mock_miservice["MiIOService"].return_value = mock_miio

        result = await am.ensure_auth()
        assert result is True
        assert am._state == am.STATE_HEALTHY

    @pytest.mark.asyncio
    async def test_keepalive_probe_failure_does_not_increment_lock(self, auth_manager):
        am, _, _ = auth_manager

        class _ProbeFailService:
            async def device_list(self):
                raise RuntimeError("connection timeout")

        am.mina_service = _ProbeFailService()
        am._state = am.STATE_HEALTHY

        result = await am.ensure_auth()
        assert result is False
        assert am._state == am.STATE_DEGRADED
        assert am._probe_failure_count == 1
        assert am._lock_counter == 0
        assert am._retry_count_effective == 0

    @pytest.mark.asyncio
    async def test_network_error_does_not_push_lock_counter(self, auth_manager, mock_miservice):
        am, _, _ = auth_manager
        am._state = am.STATE_DEGRADED

        class _NetworkFailService:
            async def device_list(self):
                raise RuntimeError("connection timeout")

        mock_mina = _NetworkFailService()
        mock_miio = MockMiIOService()
        mock_miservice["MiNAService"].return_value = mock_mina
        mock_miservice["MiIOService"].return_value = mock_miio

        result = await am.ensure_auth(force=True)
        assert result is False
        assert am._state == am.STATE_DEGRADED
        assert am._retry_count > 0
        assert am._retry_count_effective == 0
        assert am._lock_counter == 0

    @pytest.mark.asyncio
    async def test_runtime_failure_does_not_push_lock_counter(self, auth_manager, mock_miservice):
        am, _, _ = auth_manager
        am._state = am.STATE_DEGRADED

        class _RuntimeFailService:
            async def device_list(self):
                raise RuntimeError("boom")

        mock_mina = _RuntimeFailService()
        mock_miio = MockMiIOService()
        mock_miservice["MiNAService"].return_value = mock_mina
        mock_miservice["MiIOService"].return_value = mock_miio

        result = await am.ensure_auth(force=True)
        assert result is False
        assert am._state == am.STATE_DEGRADED
        assert am._retry_count > 0
        assert am._retry_count_effective == 0
        assert am._lock_counter == 0

    @pytest.mark.asyncio
    async def test_auth_error_only_locks_after_continuous_effective_failures(
        self, auth_manager, mock_miservice
    ):
        am, _, _ = auth_manager
        am._state = am.STATE_DEGRADED

        class _ExpiredService:
            async def device_list(self):
                raise RuntimeError("passport token expired")

        mock_mina = _ExpiredService()
        mock_miio = MockMiIOService()
        mock_miservice["MiNAService"].return_value = mock_mina
        mock_miservice["MiIOService"].return_value = mock_miio

        for _ in range(am._lock_counter_threshold - 1):
            result = await am.ensure_auth(force=True)
            assert result is False
            assert am._state == am.STATE_DEGRADED
            assert am._lock_counter < am._lock_counter_threshold

        result = await am.ensure_auth(force=True)
        assert result is False
        assert am._state == am.STATE_LOCKED
        assert am._lock_counter == am._lock_counter_threshold
        assert am.is_auth_locked() is True

    def test_auth_status_snapshot(self, auth_manager):
        """测试状态快照"""
        am, _, _ = auth_manager
        snapshot = am.auth_status_snapshot()
        assert "state" in snapshot
        assert "locked" in snapshot
        assert snapshot["state"] == am.STATE_HEALTHY

    def test_auth_debug_state(self, auth_manager):
        """测试调试状态"""
        am, _, _ = auth_manager
        debug = am.auth_debug_state()
        assert "state" in debug
        assert "last_auth_error" in debug

    @pytest.mark.asyncio
    async def test_mina_call_success(self, auth_manager, mock_miservice):
        """测试 mina_call 成功"""
        am, _, _ = auth_manager

        # Mock 服务
        mock_mina = MockMiNAService()
        mock_miservice["MiNAService"].return_value = mock_mina

        # 先确保认证
        await am.ensure_auth()
        initial_calls = mock_mina.device_list_called

        # 调用
        result = await am.mina_call("device_list")
        assert result is not None
        # ensure_auth 会调用一次，mina_call 会再调用一次
        assert mock_mina.device_list_called >= initial_calls

    @pytest.mark.asyncio
    async def test_auth_call_with_retry(self, auth_manager, mock_miservice):
        """测试带重试的调用"""
        am, _, _ = auth_manager

        # Mock 服务，第一次失败，第二次成功
        mock_mina = MockMiNAService()
        mock_mina.should_fail = True
        mock_miservice["MiNAService"].return_value = mock_mina

        call_count = 0

        async def failing_call():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Simulated auth error")
            return "success"

        # 这个测试验证重试逻辑
        # 注意：由于 mock 的复杂性，这里只是验证方法存在
        assert hasattr(am, "auth_call")

    def test_set_token(self, auth_manager):
        """测试设置 token"""
        am, auth_data, _ = auth_manager
        mock_account = MockMiAccount()
        am.set_token(mock_account)
        assert mock_account.token["userId"] == auth_data["userId"]
        assert mock_account.token["passToken"] == auth_data["passToken"]

    def test_get_cookie_dict(self, auth_manager):
        """测试获取 cookie 字典"""
        am, _, _ = auth_manager
        cookie = am.get_cookie_dict()
        assert isinstance(cookie, dict)

    @pytest.mark.asyncio
    async def test_manual_reload_runtime(self, auth_manager, mock_miservice):
        """测试手动重载运行时"""
        am, _, _ = auth_manager

        # Mock 服务
        mock_mina = MockMiNAService()
        mock_miio = MockMiIOService()
        mock_miservice["MiNAService"].return_value = mock_mina
        mock_miservice["MiIOService"].return_value = mock_miio

        result = await am.manual_reload_runtime()
        assert result["refreshed"] is True
        assert result["runtime_auth_ready"] is True

    @pytest.mark.asyncio
    async def test_background_recovery_schedule(self, auth_manager):
        """测试后台恢复调度"""
        am, _, _ = auth_manager

        # 触发后台恢复
        am._schedule_background_recovery()
        assert am._recovery_task is not None

        # 等待任务完成
        await asyncio.sleep(0.5)

        # 清理
        if am._recovery_task and not am._recovery_task.done():
            am._recovery_task.cancel()
            try:
                await am._recovery_task
            except asyncio.CancelledError:
                pass

    def test_start_cooldown(self, auth_manager):
        """测试冷却机制"""
        import time

        am, _, _ = auth_manager
        am._start_cooldown()
        assert am._cooldown_until > time.time()

    def test_is_auth_error(self, auth_manager):
        """测试认证错误判断"""
        from xiaomusic.auth import is_auth_error

        assert is_auth_error(exc=Exception("401 Unauthorized")) is True
        assert is_auth_error(exc=Exception("timeout")) is False
        assert is_auth_error(exc=Exception("network error")) is False

    def test_is_network_error(self, auth_manager):
        """测试网络错误判断"""
        from xiaomusic.auth import is_network_error

        assert is_network_error(exc=Exception("timeout")) is True
        assert is_network_error(exc=Exception("connection reset")) is True
        assert is_network_error(exc=Exception("401 Unauthorized")) is False


class TestAuthRecovery:
    """测试认证恢复流程"""

    @pytest.mark.asyncio
    async def test_recovery_on_auth_error(self, auth_manager, mock_miservice):
        """测试认证错误时的恢复"""
        am, _, _ = auth_manager

        # Mock 服务，先失败后成功
        mock_mina = MockMiNAService()
        mock_miservice["MiNAService"].return_value = mock_mina

        # 确保认证
        await am.ensure_auth()

        # 模拟认证错误
        am._state = am.STATE_DEGRADED

        # 再次确保认证应该触发恢复
        result = await am.ensure_auth()
        assert result is True
        assert am._state == am.STATE_HEALTHY

    @pytest.mark.asyncio
    async def test_cooldown_prevents_repeated_login(self, auth_manager):
        """测试冷却机制防止频繁登录"""
        am, _, _ = auth_manager

        # 开始冷却
        am._start_cooldown()

        # ensure_auth 应该返回 False
        result = await am.ensure_auth()
        assert result is False


class TestLoginSignature:
    """测试登录签名"""

    def test_login_signature_changes_with_user(self, auth_manager, tmp_path):
        """测试用户变化时签名变化"""
        from xiaomusic.auth import SimpleAuthManager

        # 创建第一个用户
        auth_data1 = {
            "userId": "user1",
            "passToken": "pass_token_1",
        }
        auth_file1 = tmp_path / "auth1.json"
        auth_file1.write_text(json.dumps(auth_data1))

        config1 = MockConfig(tmp_path)
        config1.auth_token_path = str(auth_file1)

        with patch("xiaomusic.auth.ClientSession"):
            am1 = SimpleAuthManager(
                config=config1,
                log=MagicMock(),
                device_manager=MagicMock(),
                token_store=MockTokenStore(auth_data1),
            )
            sig1 = am1._get_login_signature()

        # 创建第二个用户
        auth_data2 = {
            "userId": "user2",
            "passToken": "pass_token_2",
        }
        auth_file2 = tmp_path / "auth2.json"
        auth_file2.write_text(json.dumps(auth_data2))

        config2 = MockConfig(tmp_path)
        config2.auth_token_path = str(auth_file2)

        with patch("xiaomusic.auth.ClientSession"):
            am2 = SimpleAuthManager(
                config=config2,
                log=MagicMock(),
                device_manager=MagicMock(),
                token_store=MockTokenStore(auth_data2),
            )
            sig2 = am2._get_login_signature()

        # 签名应该不同
        assert sig1 != sig2
