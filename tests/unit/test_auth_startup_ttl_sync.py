import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio


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
        self.auth_token_path = str(base / "auth.json")
        self.mi_did = "981257654"
        self.devices = {}
        self.auth_refresh_interval_hours = 12
        self.auth_refresh_min_interval_minutes = 30
        self.auth_refresh_threshold = 0.3
        self.mina_high_freq_min_interval_seconds = 8
        self.mina_auth_fail_threshold = 3
        self.mina_auth_cooldown_seconds = 600

    def get_one_device_id(self):
        return "dev0001"


@pytest.fixture(autouse=True)
def _clear_auth_env(monkeypatch):
    monkeypatch.delenv("AUTH_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("AUTH_REFRESH_TOKEN", raising=False)


@pytest_asyncio.fixture
async def auth_setup(tmp_path):
    from xiaomusic.auth import AuthManager

    cfg = _DummyConfig(tmp_path)
    Path(cfg.auth_token_path).write_text(
        json.dumps(
            {
                "passToken": "refresh",
                "userId": "user",
                "cUserId": "cuser",
                "psecurity": "psecurity",
                "serviceToken": "service",
                "ssecurity": "ssecurity",
                "deviceId": "device",
            }
        ),
        encoding="utf-8",
    )
    manager = AuthManager(cfg, _DummyLog(), _DummyDeviceManager())
    yield cfg, manager
    await manager.mi_session.close()


async def _run_startup_without_auth_recovery(manager):
    manager.need_login = AsyncMock(return_value=False)
    manager.ensure_logged_in = AsyncMock(
        side_effect=AssertionError("startup must not login or exchange")
    )
    manager.device_manager.update_device_info = AsyncMock()
    await manager.init_all_data()
    manager.ensure_logged_in.assert_not_awaited()


@pytest.mark.asyncio
async def test_startup_syncs_save_time_from_direct_file_without_exchange(auth_setup):
    cfg, manager = auth_setup
    save_time_ms = 1789970602396
    data = json.loads(Path(cfg.auth_token_path).read_text(encoding="utf-8"))
    data["saveTime"] = save_time_ms
    Path(cfg.auth_token_path).write_text(json.dumps(data), encoding="utf-8")

    with patch("xiaomusic.auth.MiAccount") as account_ctor:
        await _run_startup_without_auth_recovery(manager)

    debug = manager.auth_debug_state()
    assert debug["login_at"] == pytest.approx(save_time_ms / 1000.0)
    assert debug["expires_at"] == 0.0
    assert debug["ttl_remaining_seconds"] == 0
    account_ctor.assert_not_called()


@pytest.mark.asyncio
async def test_startup_syncs_save_time_through_real_token_store_get(auth_setup):
    from xiaomusic.auth import AuthManager
    from xiaomusic.security.token_store import TokenStore

    cfg, original_manager = auth_setup
    await original_manager.mi_session.close()
    save_time_ms = 1789970602396
    data = json.loads(Path(cfg.auth_token_path).read_text(encoding="utf-8"))
    data["saveTime"] = save_time_ms
    Path(cfg.auth_token_path).write_text(json.dumps(data), encoding="utf-8")
    store = TokenStore(cfg)
    manager = AuthManager(cfg, _DummyLog(), _DummyDeviceManager(), token_store=store)
    try:
        await _run_startup_without_auth_recovery(manager)
        assert manager.auth_debug_state()["login_at"] == pytest.approx(save_time_ms / 1000.0)
    finally:
        await manager.mi_session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("save_time", [None, "not-a-timestamp", float("nan"), float("inf"), float("-inf")])
async def test_startup_invalid_or_nonfinite_save_time_is_zero(auth_setup, save_time):
    cfg, manager = auth_setup
    data = json.loads(Path(cfg.auth_token_path).read_text(encoding="utf-8"))
    if save_time is None:
        data.pop("saveTime", None)
    else:
        data["saveTime"] = save_time
    Path(cfg.auth_token_path).write_text(json.dumps(data), encoding="utf-8")

    await _run_startup_without_auth_recovery(manager)

    debug = manager.auth_debug_state()
    assert debug["login_at"] == 0.0
    assert debug["expires_at"] == 0.0
    assert debug["ttl_remaining_seconds"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("env_name", ["AUTH_ACCESS_TOKEN", "AUTH_REFRESH_TOKEN"])
async def test_startup_env_override_does_not_use_disk_save_time(
    auth_setup, monkeypatch, env_name
):
    from xiaomusic.auth import AuthManager
    from xiaomusic.security.token_store import TokenStore

    cfg, original_manager = auth_setup
    await original_manager.mi_session.close()
    data = json.loads(Path(cfg.auth_token_path).read_text(encoding="utf-8"))
    data["saveTime"] = 1789970602396
    Path(cfg.auth_token_path).write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv(env_name, "env-credential")
    manager = AuthManager(
        cfg, _DummyLog(), _DummyDeviceManager(), token_store=TokenStore(cfg)
    )
    try:
        await _run_startup_without_auth_recovery(manager)
        debug = manager.auth_debug_state()
        assert debug["login_at"] == 0.0
        assert debug["expires_at"] == 0.0
        assert debug["ttl_remaining_seconds"] == 0
        assert manager._auth_refresh_mode == "unknown"
    finally:
        await manager.mi_session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("login_at_ts", [float("nan"), float("inf"), float("-inf")])
async def test_sync_auth_ttl_nonfinite_login_at_is_zero(auth_setup, login_at_ts):
    _, manager = auth_setup
    valid_data = {"saveTime": 1789970602396}

    manager._sync_auth_ttl(valid_data, login_at_ts=login_at_ts)

    debug = manager.auth_debug_state()
    assert debug["login_at"] == 0.0
    assert debug["expires_at"] == 0.0
    assert debug["ttl_remaining_seconds"] == 0
    assert manager._auth_refresh_mode == "interval_fallback"


@pytest.mark.asyncio
async def test_verified_runtime_only_preserves_verified_time_without_probe(auth_setup):
    _, manager = auth_setup
    manager._login_at = 1234.5
    manager.need_login = AsyncMock(side_effect=AssertionError("unexpected auth probe"))
    manager.ensure_logged_in = AsyncMock(side_effect=AssertionError("unexpected login"))
    manager.device_manager.update_device_info = AsyncMock()

    await manager.init_all_data(verified_runtime_only=True, refresh_device_map=False)

    assert manager.auth_debug_state()["login_at"] == 1234.5
    manager.need_login.assert_not_awaited()
    manager.ensure_logged_in.assert_not_awaited()
