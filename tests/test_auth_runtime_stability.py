import asyncio
import inspect
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

    def get_persisted(self):
        return dict(self._data)

    def update(self, data, reason=""):
        self.updated.append((dict(data), reason))
        self._data.update(data)

    def flush(self):
        return None

    def commit(self, data, reason=""):
        self._data = dict(data)

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
    manager._try_miaccount_persistent_auth_relogin = AsyncMock(return_value={
        "ok": True,
        "auth_data": {**manager._get_auth_data(), "serviceToken": "candidate-token"},
    })
    manager._build_verified_runtime_candidate = AsyncMock(return_value={
        "ok": False,
        "error": "candidate verify failed",
    })
    out = await manager.manual_reload_runtime(reason="ut-runtime-reload")

    assert out["refreshed"] is False
    assert out["runtime_auth_ready"] is True
    assert out["state_before"] == manager.STATE_HEALTHY
    assert out["state_after"] == manager.STATE_HEALTHY
    trace = manager._last_runtime_reload_state["last_reload_runtime"]
    assert trace["verify_attempted"] is True
    assert trace["runtime_swap_attempted"] is False
    assert trace["runtime_swap_applied"] is False
    assert manager.mina_service is old_runtime
    assert out["need_qr_scan"] is False
    assert out["user_action_required"] is False
    assert out["long_term_expired"] is False


@pytest.mark.asyncio
async def test_scheduled_refresh_uses_attempt_cooldown_not_stale_login_time(auth_manager):
    manager, token_store = auth_manager
    now = 10_000.0
    token_store._data["saveTime"] = int((now - 3500) * 1000)
    manager.config.auth_refresh_min_interval_minutes = 30
    manager._last_login_ts = now - 7200
    manager._last_runtime_verify_ts = now
    manager._last_refresh_attempt_ts = now - 60

    with patch("xiaomusic.auth.time.time", return_value=now), patch.object(
        manager, "ensure_auth", new=AsyncMock(return_value=False)
    ) as ensure:
        assert await manager._maybe_scheduled_refresh() is False

    ensure.assert_not_awaited()


@pytest.mark.asyncio
async def test_scheduled_refresh_failure_preserves_healthy_runtime(auth_manager):
    manager, token_store = auth_manager
    now = 20_000.0
    token_store._data["saveTime"] = int((now - 3500) * 1000)
    manager.config.auth_refresh_min_interval_minutes = 30
    old_runtime = manager.mina_service

    candidate = AsyncMock(return_value={
        "ok": False,
        "failed_reason": "verify failed",
        "error_code": "verify_failed",
    })

    with patch("xiaomusic.auth.time.time", return_value=now), patch.object(
        manager, "_atomic_persistent_auth_refresh", new=candidate
    ):
        assert await manager._maybe_scheduled_refresh() is False

    assert manager._state == manager.STATE_HEALTHY
    assert manager.mina_service is old_runtime
    assert manager._last_refresh_attempt_ts == now


@pytest.mark.asyncio
async def test_scheduled_refresh_failure_is_attempt_rate_limited(auth_manager):
    manager, token_store = auth_manager
    now = 30_000.0
    token_store._data["saveTime"] = int((now - 3500) * 1000)
    manager.config.auth_refresh_min_interval_minutes = 30
    calls = AsyncMock(return_value={
        "ok": False,
        "failed_reason": "verify failed",
        "error_code": "verify_failed",
    })

    with patch("xiaomusic.auth.time.time", return_value=now), patch.object(
        manager, "_atomic_persistent_auth_refresh", new=calls
    ):
        assert await manager._maybe_scheduled_refresh() is False
        assert await manager._maybe_scheduled_refresh() is False

    calls.assert_awaited_once()


@pytest.mark.asyncio
async def test_expired_service_token_probe_uses_atomic_rebuild_not_full_login(auth_manager):
    manager, _ = auth_manager
    manager.mina_service = _FailingRuntime()
    atomic = AsyncMock(return_value={
        "ok": True,
        "used_path": "miaccount_persistent_auth_login",
        "runtime_rebind_result": "ok",
        "verify_result": "ok",
    })
    manager._atomic_persistent_auth_refresh = atomic
    login_account = MagicMock()
    login_account.login = AsyncMock(side_effect=AssertionError("expired token used full login"))

    with patch("xiaomusic.auth.MiAccount", return_value=login_account):
        assert await manager.ensure_auth() is True

    atomic.assert_awaited_once()
    login_account.login.assert_not_awaited()


@pytest.mark.asyncio
async def test_probe_failure_env_override_uses_runtime_rebind_only(auth_manager, monkeypatch):
    manager, _ = auth_manager
    monkeypatch.setenv("AUTH_ACCESS_TOKEN", "runtime-access")
    manager.mina_service = _FailingRuntime()
    rebind = AsyncMock(return_value={
        "ok": True,
        "runtime_rebind_result": "ok",
        "verify_result": "ok",
    })
    manager._atomic_runtime_rebind_current_auth = rebind
    manager._try_miaccount_persistent_auth_relogin = AsyncMock()
    account = MagicMock()
    account.login = AsyncMock(side_effect=AssertionError("env path called login"))

    with patch("xiaomusic.auth.MiAccount", return_value=account):
        assert await manager.ensure_auth() is True

    rebind.assert_awaited_once()
    manager._try_miaccount_persistent_auth_relogin.assert_not_awaited()
    account.login.assert_not_awaited()


@pytest.mark.asyncio
async def test_probe_auth_failure_enters_recovery(auth_manager):
    manager, _ = auth_manager
    manager.mina_service = _FailingRuntime()
    manager._try_login = AsyncMock(return_value=False)

    assert await manager.ensure_auth() is False
    assert manager._state == manager.STATE_DEGRADED
    manager._try_login.assert_awaited_once()


@pytest.mark.asyncio
async def test_scheduled_refresh_skips_without_persistent_login_capability(auth_manager):
    manager, token_store = auth_manager
    now = 40_000.0
    token_store._data.pop("psecurity")
    token_store._data.pop("ssecurity")
    token_store._data["saveTime"] = int((now - 3500) * 1000)
    manager.config.auth_refresh_min_interval_minutes = 30
    manager.ensure_auth = AsyncMock(return_value=False)

    with patch("xiaomusic.auth.time.time", return_value=now):
        assert await manager._maybe_scheduled_refresh() is False

    manager.ensure_auth.assert_not_awaited()
    assert manager._state == manager.STATE_HEALTHY
    assert manager._last_refresh_trigger == "scheduled_capability_skip"


def test_short_session_rebuild_defaults_to_atomic(auth_manager):
    manager, _ = auth_manager
    assert inspect.signature(
        manager.rebuild_short_session_from_persistent_auth
    ).parameters["atomic"].default is True


@pytest.mark.asyncio
async def test_force_auth_does_not_implicitly_preserve_healthy_runtime(auth_manager):
    manager, _ = auth_manager
    attempt = AsyncMock(return_value=False)
    manager._try_login = attempt

    assert await manager.ensure_auth(force=True, reason="ut-explicit-force") is False
    attempt.assert_awaited_once_with(
        reason="ut-explicit-force", preserve_healthy_runtime=False
    )


@pytest.mark.asyncio
async def test_scheduled_refresh_full_chain_never_calls_account_login(auth_manager):
    manager, token_store = auth_manager
    now = 50_000.0
    token_store._data["saveTime"] = int((now - 3500) * 1000)
    manager.config.auth_refresh_min_interval_minutes = 30

    account = MagicMock()
    account.token = {}
    account.login = AsyncMock(side_effect=AssertionError("scheduled refresh called login"))
    account._serviceLogin = AsyncMock(return_value={
        "code": 0,
        "location": "https://account.example/redirect?nonce=n1",
        "nonce": "n1",
        "ssecurity": "new-ssecurity",
    })
    account._securityTokenService = AsyncMock(return_value="new-service-token")
    candidate_mina = _HealthyRuntime()

    with patch("xiaomusic.auth.time.time", return_value=now), patch(
        "xiaomusic.auth.MiAccount", return_value=account
    ) as account_factory, patch(
        "xiaomusic.auth.MiNAService", return_value=candidate_mina
    ), patch("xiaomusic.auth.MiIOService", return_value=object()):
        assert await manager._maybe_scheduled_refresh() is True

    account_factory.assert_called()
    account.login.assert_not_awaited()
    assert manager.mina_service is candidate_mina
    assert token_store.get()["serviceToken"] == "new-service-token"
    assert int(token_store.get()["saveTime"]) == now * 1000
    flow = manager.auth_short_session_rebuild_debug_state()["last_auth_recovery_flow"]
    assert flow["started_at"] <= flow["finished_at"]
    assert flow["primary_attempt"]["result"] == "ok"
    assert flow["verify"]["result"] == "ok"
    assert manager._last_recovery_result == "ok"
    assert manager._last_recovery_error_code == ""


@pytest.mark.asyncio
async def test_scheduled_refresh_skips_environment_credentials(auth_manager, monkeypatch):
    manager, token_store = auth_manager
    now = 60_000.0
    token_store._data["saveTime"] = int((now - 3500) * 1000)
    monkeypatch.setenv("AUTH_ACCESS_TOKEN", "runtime-only-access")
    manager._atomic_persistent_auth_refresh = AsyncMock()

    with patch("xiaomusic.auth.time.time", return_value=now):
        assert await manager._maybe_scheduled_refresh() is False

    manager._atomic_persistent_auth_refresh.assert_not_awaited()
    assert manager._last_refresh_trigger == "scheduled_env_override_skip"


@pytest.mark.asyncio
async def test_manual_reload_full_chain_never_calls_account_login(auth_manager):
    manager, token_store = auth_manager
    account = MagicMock()
    account.token = {}
    account.login = AsyncMock(side_effect=AssertionError("manual reload called login"))
    account._serviceLogin = AsyncMock(return_value={
        "code": 0,
        "location": "https://account.example/redirect?nonce=n1",
        "nonce": "n1",
        "ssecurity": "new-ssecurity",
    })
    account._securityTokenService = AsyncMock(return_value="manual-service-token")

    with patch("xiaomusic.auth.MiAccount", return_value=account), patch(
        "xiaomusic.auth.MiNAService", return_value=_HealthyRuntime()
    ), patch("xiaomusic.auth.MiIOService", return_value=object()):
        out = await manager.manual_reload_runtime(reason="ut-manual-atomic")

    assert out["refreshed"] is True
    account.login.assert_not_awaited()
    assert token_store.get()["serviceToken"] == "manual-service-token"


@pytest.mark.asyncio
async def test_manual_reload_long_term_failure_maps_to_manual_login_required(auth_manager):
    manager, _ = auth_manager
    manager._state = manager.STATE_HEALTHY
    account = MagicMock()
    account.token = {}
    account._serviceLogin = AsyncMock(return_value={"code": 70016})
    account.login = AsyncMock(side_effect=AssertionError("manual expired path called login"))

    with patch("xiaomusic.auth.MiAccount", return_value=account):
        out = await manager.manual_reload_runtime(reason="ut-manual-expired")

    assert out["state_after"] == manager.STATE_LOCKED
    assert out["runtime_auth_ready"] is False
    assert out["need_qr_scan"] is True
    public = manager.map_auth_public_status(runtime_auth_ready=False)
    assert public["status_reason"] == "manual_login_required"


@pytest.mark.asyncio
@pytest.mark.parametrize("service_code", [70016, 87001])
async def test_service_login_codes_propagate_manual_auth_classification(
    auth_manager, service_code
):
    manager, _ = auth_manager
    account = MagicMock()
    account.token = {}
    account._serviceLogin = AsyncMock(return_value={"code": service_code})

    with patch("xiaomusic.auth.MiAccount", return_value=account):
        out = await manager._try_miaccount_persistent_auth_relogin(
            before=manager._get_auth_data(), reason="ut-code"
        )

    assert out["error_code"] == "service_login_failed"
    assert out["long_term_expired"] is True
    assert out["need_qr_scan"] is True
    assert out["user_action_required"] is True


@pytest.mark.asyncio
async def test_manual_env_rebind_reloads_disk_without_rotating_token(auth_manager, monkeypatch):
    manager, token_store = auth_manager
    old_token = token_store.get()
    monkeypatch.setenv("AUTH_ACCESS_TOKEN", "runtime-only-access")
    manager._try_miaccount_persistent_auth_relogin = AsyncMock()
    manager._build_verified_runtime_candidate = AsyncMock(return_value={
        "ok": True,
        "account": object(),
        "mina_service": _HealthyRuntime(),
        "miio_service": object(),
        "session": None,
        "device_id": old_token["deviceId"],
    })

    out = await manager.manual_reload_runtime(reason="ut-manual-env")

    assert out["refreshed"] is True
    assert out["token_store_reloaded"] is True
    manager._try_miaccount_persistent_auth_relogin.assert_not_awaited()
    assert token_store.get_persisted()["serviceToken"] == old_token["serviceToken"]


@pytest.mark.asyncio
async def test_queued_try_login_reuses_new_generation_without_login(auth_manager):
    manager, token_store = auth_manager
    started = asyncio.Event()
    release = asyncio.Event()
    candidate_session = MagicMock()
    candidate_session.cookie_jar = MagicMock()
    candidate = {
        "ok": True,
        "account": object(),
        "mina_service": _HealthyRuntime(),
        "miio_service": object(),
        "session": candidate_session,
        "device_id": "candidate-device",
    }
    manager._try_miaccount_persistent_auth_relogin = AsyncMock(return_value={
        "ok": True,
        "auth_data": {**token_store.get(), "serviceToken": "candidate-token"},
    })

    async def build_candidate(_auth_data):
        started.set()
        await release.wait()
        return candidate

    manager._build_verified_runtime_candidate = build_candidate
    login_account = MagicMock()
    login_account.login = AsyncMock(side_effect=AssertionError("queued request logged in"))

    with patch("xiaomusic.auth.MiAccount", return_value=login_account):
        first = asyncio.create_task(manager._atomic_persistent_auth_refresh("first"))
        await started.wait()
        second = asyncio.create_task(manager._try_login(reason="queued-recovery"))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not second.done()
        release.set()
        first_out = await first
        second_out = await second
        assert first_out["ok"] is True
        assert second_out is True, (second_out, manager._runtime_generation, manager._state, manager._last_error)

    assert manager._state == manager.STATE_HEALTHY
    login_account.login.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancelled_old_session_close_keeps_committed_runtime(auth_manager):
    manager, token_store = auth_manager
    started = asyncio.Event()
    never = asyncio.Event()
    candidate_session = MagicMock()
    candidate_session.cookie_jar = MagicMock()

    async def blocked_close():
        started.set()
        await never.wait()

    manager.mi_session.close = blocked_close
    candidate_runtime = _HealthyRuntime()
    manager._try_miaccount_persistent_auth_relogin = AsyncMock(return_value={
        "ok": True,
        "auth_data": {**token_store.get(), "serviceToken": "candidate-token"},
    })
    manager._build_verified_runtime_candidate = AsyncMock(return_value={
        "ok": True,
        "account": object(),
        "mina_service": candidate_runtime,
        "miio_service": object(),
        "session": candidate_session,
        "device_id": "candidate-device",
    })

    task = asyncio.create_task(manager._atomic_persistent_auth_refresh("cancel-close"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert manager.mina_service is candidate_runtime
    assert manager.mi_session is candidate_session
    assert manager.login_account is not None
    assert manager.device_id == "candidate-device"


@pytest.mark.asyncio
async def test_atomic_refresh_verify_failure_does_not_write_or_swap(auth_manager):
    manager, token_store = auth_manager
    old_runtime = manager.mina_service
    old_device_id = manager.device_id
    old_token = token_store.get()
    manager._try_miaccount_persistent_auth_relogin = AsyncMock(return_value={
        "ok": True,
        "auth_data": {**old_token, "serviceToken": "candidate-token"},
    })
    manager._build_verified_runtime_candidate = AsyncMock(return_value={
        "ok": False,
        "error": "candidate verify failed",
    })

    out = await manager.rebuild_short_session_from_persistent_auth(
        reason="ut-atomic", atomic=True
    )

    assert out["ok"] is False
    assert token_store.get() == old_token
    assert manager.mina_service is old_runtime
    assert manager.device_id == old_device_id
    flow = manager.auth_short_session_rebuild_debug_state()["last_auth_recovery_flow"]
    assert flow["primary_attempt"]["result"] == "ok"
    assert flow["verify"]["result"] == "failed"


@pytest.mark.asyncio
async def test_atomic_commit_failure_keeps_token_save_time_and_runtime(auth_manager):
    manager, token_store = auth_manager
    old_runtime = manager.mina_service
    old_device_id = manager.device_id
    old_token = token_store.get()
    manager._try_miaccount_persistent_auth_relogin = AsyncMock(return_value={
        "ok": True,
        "auth_data": {**old_token, "serviceToken": "candidate-token"},
    })
    manager._build_verified_runtime_candidate = AsyncMock(return_value={
        "ok": True,
        "account": object(),
        "mina_service": object(),
        "miio_service": object(),
        "session": None,
        "device_id": "candidate-device",
    })

    def fail_commit(*_args, **_kwargs):
        raise OSError("flush failed")

    token_store.commit = fail_commit
    out = await manager._atomic_persistent_auth_refresh(reason="ut-commit-fail")

    assert out["ok"] is False
    assert token_store.get() == old_token
    assert manager.mina_service is old_runtime
    assert manager.device_id == old_device_id


@pytest.mark.asyncio
async def test_transition_lock_serializes_scheduled_candidates(auth_manager):
    manager, _ = auth_manager
    active = 0
    maximum = 0
    sequence = []

    async def candidate(_reason=""):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        sequence.append("start")
        await asyncio.sleep(0)
        sequence.append("end")
        active -= 1
        return {"ok": False, "failed_reason": "candidate failed"}

    async def primary(**_kwargs):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        sequence.append("start")
        await asyncio.sleep(0)
        sequence.append("end")
        active -= 1
        return {
            "ok": False,
            "error_code": "test",
            "failed_reason": "candidate failed",
        }

    manager._try_miaccount_persistent_auth_relogin = AsyncMock(side_effect=primary)
    first, second = await asyncio.gather(
        manager._atomic_persistent_auth_refresh("one"),
        manager._atomic_persistent_auth_refresh("two"),
    )

    assert first["ok"] is False and second["ok"] is False
    assert maximum == 1
    assert sequence == ["start", "end", "start", "end"]
    assert manager._auth_transition_lock is not manager._recovery_lock
    assert manager._try_miaccount_persistent_auth_relogin.await_count == 2


@pytest.mark.asyncio
async def test_persistent_relogin_typeerror_session_is_closed(auth_manager):
    manager, _ = auth_manager
    session = MagicMock()
    session.close = AsyncMock()
    account = MagicMock()
    account.token = {}
    account._serviceLogin = AsyncMock(return_value={"code": 1})

    with patch("xiaomusic.auth.ClientSession", return_value=session), patch(
        "xiaomusic.auth.MiAccount", side_effect=[TypeError("legacy ctor"), account]
    ):
        out = await manager._try_miaccount_persistent_auth_relogin(
            before=manager._get_auth_data(), reason="ut-close"
        )

    assert out["ok"] is False
    session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_legacy_rebind_verify_failure_does_not_clobber_runtime(auth_manager):
    manager, _ = auth_manager
    old_runtime = manager.mina_service
    old_device_id = manager.device_id
    manager._build_verified_runtime_candidate = AsyncMock(return_value={
        "ok": False,
        "error": "verify failed",
    })

    out = await manager._rebind_runtime_from_auth_data(manager._get_auth_data())

    assert out["ok"] is False
    assert manager.mina_service is old_runtime
    assert manager.device_id == old_device_id


@pytest.mark.asyncio
async def test_preserved_candidate_with_expired_long_term_auth_degrades(auth_manager):
    manager, token_store = auth_manager
    for key in ("psecurity", "ssecurity", "cUserId", "deviceId", "serviceToken", "yetAnotherServiceToken"):
        token_store._data.pop(key, None)
    manager._state = manager.STATE_HEALTHY
    account = MagicMock()
    account.token = {}
    account.login = AsyncMock(side_effect=RuntimeError("passport token expired"))

    with patch("xiaomusic.auth.MiAccount", return_value=account):
        result = await manager.ensure_auth(
            force=True, reason="ut-expired", preserve_healthy_runtime=True
        )

    assert result is False
    assert manager._state == manager.STATE_DEGRADED
    assert manager._last_login_trace["long_term_expired"] is True


@pytest.mark.asyncio
async def test_try_login_uses_fresh_login_session(auth_manager):
    manager, token_store = auth_manager
    for key in ("psecurity", "ssecurity", "cUserId", "deviceId", "serviceToken", "yetAnotherServiceToken"):
        token_store._data.pop(key, None)
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
    manager, token_store = auth_manager
    for key in ("psecurity", "ssecurity", "cUserId", "deviceId", "serviceToken", "yetAnotherServiceToken"):
        token_store._data.pop(key, None)
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
    manager, token_store = auth_manager
    for key in ("psecurity", "ssecurity", "cUserId", "deviceId", "serviceToken", "yetAnotherServiceToken"):
        token_store._data.pop(key, None)
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


class _TokenAccount:
    def __init__(self):
        self.token = {}


def test_set_token_rebuilds_micoapi_tuple(auth_manager):
    """set_token 必须把持久化的 ssecurity + serviceToken 拼回 token["micoapi"]。

    miservice 的 MiAccount.mi_request(sid) 只有在 token 中已存在该 sid 时才**不**触发
    login；缺了它，运行时重建就必然退到注定失败的 login(sid)。
    """
    manager, _ = auth_manager
    acct = _TokenAccount()
    manager.set_token(acct)
    assert acct.token.get("micoapi") == ("ssec", "short-token")


def test_set_token_without_service_token_does_not_fake_micoapi(auth_manager):
    """没有 serviceToken 时不得伪造 micoapi，否则会把失效会话伪装成可用。"""
    manager, token_store = auth_manager
    data = dict(token_store.get())
    data.pop("serviceToken", None)
    data.pop("yetAnotherServiceToken", None)
    token_store._data = data

    acct = _TokenAccount()
    manager.set_token(acct)
    assert "micoapi" not in acct.token
