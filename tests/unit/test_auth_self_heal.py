import json
import asyncio
import sys
import types
import time
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
        self.auth_token_path = str(base / "auth.json")
        self.mi_did = "981257654"
        self.devices = {}
        self.auth_refresh_interval_hours = 12
        self.auth_refresh_min_interval_minutes = 30
        self.mina_high_freq_min_interval_seconds = 8
        self.mina_auth_fail_threshold = 3
        self.mina_auth_cooldown_seconds = 600

    def get_one_device_id(self):
        return "dev0001"


@pytest_asyncio.fixture
async def auth_manager(tmp_path):
    from xiaomusic.auth import AuthManager

    cfg = _DummyConfig(tmp_path)
    Path(cfg.auth_token_path).write_text(
        json.dumps(
            {
                "passToken": "x",
                "userId": "u",
                "cUserId": "cu",
                "psecurity": "ps",
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
    calls = {"fn": 0, "ensure": 0, "non_destructive": 0}

    async def _ensure(
        force=False, reason="", prefer_refresh=False, recovery_owner=False
    ):  # noqa: ARG001
        calls["ensure"] += 1
        assert prefer_refresh is True
        return True

    async def _non_destructive(ctx="", reason=""):
        calls["non_destructive"] += 1
        return True, "runtime_verified", False

    auth_manager.ensure_logged_in = _ensure
    auth_manager._attempt_non_destructive_auth_recovery = _non_destructive

    async def fn():
        calls["fn"] += 1
        if calls["fn"] == 1:
            raise RuntimeError("401 unauthorized")
        return "OK"

    out = await auth_manager.auth_call(fn, retry=1, ctx="ut-auth-call")
    assert out == "OK"
    assert calls["ensure"] == 0, "First auth error should NOT call ensure_logged_in"
    assert calls["non_destructive"] == 1, (
        "First auth error should use non-destructive recovery"
    )
    assert calls["fn"] == 2


@pytest.mark.asyncio
async def test_concurrent_auth_call_only_one_relogin(auth_manager):
    relogin_calls = {"rebuild": 0, "refresh": 0, "non_destructive": 0, "clear": 0}

    async def _need_login():
        return False

    async def _refresh(reason, force=False):  # noqa: ARG001
        relogin_calls["refresh"] += 1
        return {
            "refreshed": True,
            "token_saved": True,
            "last_error": None,
            "fallback_allowed": False,
        }

    async def _rebuild(reason, allow_login_fallback=False):  # noqa: ARG001
        relogin_calls["rebuild"] += 1
        auth_manager.mina_service = object()
        auth_manager.login_signature = auth_manager._get_login_signature()
        return True

    async def _short_rebuild(reason):  # noqa: ARG001
        relogin_calls["refresh"] += 1
        return True

    async def _non_destructive(ctx="", reason=""):
        relogin_calls["non_destructive"] += 1
        return True, "runtime_verified", False

    def _clear(clear_reason: str, err=None):  # noqa: ARG001
        relogin_calls["clear"] += 1
        return True

    auth_manager.need_login = _need_login
    auth_manager._rebuild_short_session_from_persistent_auth = _short_rebuild
    auth_manager.rebuild_services = _rebuild
    auth_manager._attempt_non_destructive_auth_recovery = _non_destructive
    auth_manager._clear_short_lived_session = _clear
    auth_manager._last_ok_ts = 0
    # 重置 suspect 状态
    auth_manager._reset_auth_error_suspect()

    def make_fn(i):
        state = {"n": 0}

        async def fn():
            if state["n"] == 0:
                state["n"] += 1
                raise RuntimeError("Login failed: unauthorized")
            return i

        return fn

    tasks = [
        auth_manager.auth_call(make_fn(i), retry=1, ctx=f"concurrent-{i}")
        for i in range(10)
    ]
    results = await asyncio.gather(*tasks)
    assert results == list(range(10))
    # 并发调用会共享 suspect 状态，所以有些走非破坏性恢复，有些走 clear
    # 总调用次数应该等于请求数（因为每个请求都会调用其中一种恢复路径）
    total_recoveries = relogin_calls["non_destructive"] + relogin_calls["clear"]
    assert total_recoveries >= 10, (
        f"Expected at least 10 recoveries, got {total_recoveries}"
    )


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


@pytest.mark.parametrize(
    ("exc", "status", "expected"),
    [
        (RuntimeError("401 unauthorized"), None, True),
        (RuntimeError("Login failed"), None, True),
        (RuntimeError("connection timeout"), None, False),
        (RuntimeError("dns failed"), None, False),
        (RuntimeError("boom"), 403, True),
    ],
)
def test_is_auth_error_strict_matrix(exc, status, expected):
    from xiaomusic.auth import is_auth_error_strict

    resp = None
    if status is not None:
        resp = type("_Resp", (), {"status": status})()
    assert is_auth_error_strict(exc=exc, resp=resp) is expected


def test_is_network_error_matrix():
    from xiaomusic.auth import is_network_error

    assert is_network_error(exc=RuntimeError("connection timeout")) is True
    assert is_network_error(exc=RuntimeError("dns lookup failed")) is True
    assert is_network_error(exc=RuntimeError("401 unauthorized")) is False


@pytest.mark.asyncio
async def test_keepalive_degrades_after_three_failures(auth_manager, monkeypatch):
    delays = []

    async def _sleep(delay):
        delays.append(delay)
        if len(delays) >= 3:
            raise asyncio.CancelledError()

    async def _ensure(force=False, reason="", recovery_owner=False):  # noqa: ARG001
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

    async def _ensure(force=False, reason="", recovery_owner=False):  # noqa: ARG001
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


@pytest.mark.asyncio
async def test_auth_call_network_error_does_not_trigger_relogin(auth_manager):
    calls = {"ensure": 0}

    async def _ensure(*args, **kwargs):  # noqa: ARG001
        calls["ensure"] += 1
        return True

    auth_manager.ensure_logged_in = _ensure

    async def fn():
        raise RuntimeError("connection timeout")

    with pytest.raises(RuntimeError):
        await auth_manager.auth_call(fn, retry=1, ctx="ut-network")
    assert calls["ensure"] == 0


def test_clear_short_session_removes_only_short_fields(auth_manager):
    token_path = Path(auth_manager.auth_token_path)
    token_path.write_text(
        json.dumps(
            {
                "passToken": "keep-pass",
                "psecurity": "keep-psec",
                "ssecurity": "keep-ssec",
                "userId": "keep-user",
                "cUserId": "keep-cuser",
                "deviceId": "keep-device",
                "serviceToken": "drop-st",
                "yetAnotherServiceToken": "drop-yast",
            }
        ),
        encoding="utf-8",
    )
    changed = auth_manager._clear_short_lived_session(
        clear_reason="mina:player_get_status",
        err="401 unauthorized",
    )
    assert changed is True
    data = json.loads(token_path.read_text(encoding="utf-8"))
    assert "serviceToken" not in data
    assert "yetAnotherServiceToken" not in data
    assert data["passToken"] == "keep-pass"
    assert data["psecurity"] == "keep-psec"
    assert data["ssecurity"] == "keep-ssec"
    assert data["userId"] == "keep-user"
    assert data["cUserId"] == "keep-cuser"
    assert data["deviceId"] == "keep-device"


def test_clear_short_session_skipped_when_no_auth_failure_signal(auth_manager):
    token_path = Path(auth_manager.auth_token_path)
    before = json.loads(token_path.read_text(encoding="utf-8"))
    changed = auth_manager._clear_short_lived_session(
        clear_reason="keepalive",
        err="network timeout",
    )
    assert changed is False
    after = json.loads(token_path.read_text(encoding="utf-8"))
    assert after == before


@pytest.mark.asyncio
async def test_auth_call_triggers_short_session_clear_before_relogin(
    auth_manager, monkeypatch
):
    """auth_call 需要连续两次auth error才clear"""
    called = {"clear": 0, "ensure": 0, "non_destructive": 0}

    def _clear(clear_reason: str, err=None):  # noqa: ARG001
        called["clear"] += 1
        return True

    async def _ensure(
        force=False, reason="", prefer_refresh=False, recovery_owner=False
    ):  # noqa: ARG001
        called["ensure"] += 1
        return True

    async def _non_destructive(ctx="", reason=""):
        called["non_destructive"] += 1
        return True, "runtime_verified", False

    monkeypatch.setattr(auth_manager, "_clear_short_lived_session", _clear)
    auth_manager.ensure_logged_in = _ensure
    auth_manager._attempt_non_destructive_auth_recovery = _non_destructive

    state = {"n": 0}

    async def _fn():
        if state["n"] == 0:
            state["n"] += 1
            raise RuntimeError("401 unauthorized")
        return "ok"

    # 第一次auth error不会clear，走非破坏性恢复
    out = await auth_manager.auth_call(_fn, retry=1, ctx="mina:player_get_status")
    assert out == "ok"
    assert called["clear"] == 0, "First auth error should NOT clear"
    assert called["ensure"] == 0, "First auth error should NOT call ensure_logged_in"
    assert called["non_destructive"] == 1, (
        "First auth error should use non-destructive recovery"
    )

    # 重置状态，再次触发auth error
    state["n"] = 0
    auth_manager.need_login = lambda: asyncio.sleep(0) or True

    async def _fn2():
        if state["n"] == 0:
            state["n"] += 1
            raise RuntimeError("401 unauthorized")
        return "ok"

    # 第二次auth error会clear
    out = await auth_manager.auth_call(_fn2, retry=1, ctx="mina:player_get_status")
    assert out == "ok"
    assert called["clear"] == 1, "Second consecutive auth error SHOULD clear"
    assert called["ensure"] == 1, "Second auth error SHOULD call ensure_logged_in"


@pytest.mark.asyncio
async def test_ensure_logged_in_rebuild_path_clears_short_session(
    auth_manager, monkeypatch
):
    events = []

    async def _need_login():
        return True

    async def _short_rebuild(reason):  # noqa: ARG001
        events.append("short_rebuild")
        return True

    async def _rebuild(reason, allow_login_fallback=False):  # noqa: ARG001
        events.append(f"rebuild:{allow_login_fallback}")
        return True

    def _clear(clear_reason: str, err=None):  # noqa: ARG001
        events.append("clear")
        return True

    auth_manager.need_login = _need_login
    auth_manager._rebuild_short_session_from_persistent_auth = _short_rebuild
    auth_manager.rebuild_services = _rebuild
    monkeypatch.setattr(auth_manager, "_clear_short_lived_session", _clear)

    out = await auth_manager.ensure_logged_in(
        force=True, reason="ut-refresh-failed", prefer_refresh=True
    )
    assert out is True
    assert events == ["clear", "short_rebuild", "rebuild:False"]


def test_persist_rebuild_writes_short_tokens_again(auth_manager):
    from xiaomusic.security.token_store import TokenStore

    auth_manager.token_store = TokenStore(auth_manager.config, _DummyLog())
    auth_manager.token_store.reload_from_disk()
    auth_data = auth_manager._get_auth_data()
    auth_data.pop("serviceToken", None)
    auth_data.pop("yetAnotherServiceToken", None)
    auth_manager.token_store.update(auth_data, reason="ut-strip")
    auth_manager.token_store.flush()

    class _Account:
        token = {
            "passToken": "x",
            "userId": "u",
            "deviceId": "d1",
            "micoapi": ("ss", "new-short-token"),
        }

    auth_manager._persist_auth_data(
        auth_manager._get_auth_data(), _Account(), reason="ut-rebuild"
    )
    data = auth_manager._get_auth_data()
    assert data.get("serviceToken") == "new-short-token"
    assert data.get("yetAnotherServiceToken") == "new-short-token"


@pytest.mark.asyncio
async def test_ensure_logged_in_prefers_refresh_then_rebuild(auth_manager):
    events = []

    async def _need_login():
        return True

    async def _short_rebuild(reason):  # noqa: ARG001
        events.append("short_rebuild")
        return True

    async def _rebuild(reason, allow_login_fallback=False):  # noqa: ARG001
        events.append(f"rebuild:{allow_login_fallback}")
        return True

    auth_manager.need_login = _need_login
    auth_manager._rebuild_short_session_from_persistent_auth = _short_rebuild
    auth_manager.rebuild_services = _rebuild

    out = await auth_manager.ensure_logged_in(
        force=True, reason="ut-auth", prefer_refresh=True
    )
    assert out is True
    assert events == ["short_rebuild", "rebuild:False"]


@pytest.mark.asyncio
async def test_ensure_logged_in_never_uses_login_fallback_in_rebuild(auth_manager):
    events = []

    async def _need_login():
        return True

    async def _short_rebuild(reason):  # noqa: ARG001
        events.append("short_rebuild")
        return True

    async def _rebuild(reason, allow_login_fallback=False):  # noqa: ARG001
        events.append(f"rebuild:{allow_login_fallback}")
        return True

    auth_manager.need_login = _need_login
    auth_manager._rebuild_short_session_from_persistent_auth = _short_rebuild
    auth_manager.rebuild_services = _rebuild

    out = await auth_manager.ensure_logged_in(
        force=True, reason="ut-fallback", prefer_refresh=True
    )
    assert out is True
    assert events == ["short_rebuild", "rebuild:False"]


@pytest.mark.asyncio
async def test_high_freq_mina_call_rate_limit_and_circuit(auth_manager):
    auth_manager.config.mina_high_freq_min_interval_seconds = 1
    auth_manager.config.mina_auth_fail_threshold = 2
    auth_manager.config.mina_auth_cooldown_seconds = 600

    calls = {"auth_call": 0}

    async def _auth_call(*args, **kwargs):  # noqa: ARG001
        calls["auth_call"] += 1
        raise RuntimeError("401 unauthorized")

    auth_manager.auth_call = _auth_call

    r1 = await auth_manager.mina_call("device_list", ctx="keepalive")
    auth_manager._hf_last_call_ts["device_list"] = 0
    r2 = await auth_manager.mina_call("device_list", ctx="keepalive")
    auth_manager._hf_last_call_ts["device_list"] = 0
    r3 = await auth_manager.mina_call("device_list", ctx="keepalive")

    assert r1 == []
    assert r2 == []
    assert r3 == []
    # 2 次达到阈值后熔断，第三次不再进入 auth_call
    assert calls["auth_call"] == 2


@pytest.mark.asyncio
async def test_scheduled_refresh_trigger(auth_manager):
    auth_manager.config.auth_refresh_interval_hours = 0.01
    auth_manager.config.auth_refresh_min_interval_minutes = 1
    auth_manager._last_refresh_ts = 0

    calls = {"refresh": 0, "rebuild": 0}

    def _token_save_ts():
        return time.time() - 7200

    async def _refresh(reason, force=False):  # noqa: ARG001
        calls["refresh"] += 1
        return {
            "refreshed": True,
            "token_saved": True,
            "last_error": None,
            "fallback_allowed": False,
        }

    async def _rebuild(reason, allow_login_fallback=False):  # noqa: ARG001
        calls["rebuild"] += 1
        return True

    auth_manager._token_save_ts = _token_save_ts
    auth_manager.refresh_auth_if_needed = _refresh
    auth_manager.rebuild_services = _rebuild

    await auth_manager._maybe_scheduled_refresh()
    assert calls == {"refresh": 1, "rebuild": 1}


def test_auth_debug_state_has_required_fields(auth_manager):
    now = time.time()
    auth_manager._sync_auth_ttl(
        {
            "passToken": "x",
            "userId": "u",
            "serviceToken": "st",
            "saveTime": int(now * 1000),
            "expires_in": 3600,
        },
        login_at_ts=now,
    )
    auth_manager._last_refresh_trigger = "scheduled"
    auth_manager._last_auth_error = ""
    state = auth_manager.auth_debug_state()
    assert set(state.keys()) == {
        "auth_mode",
        "last_auth_mode_transition",
        "login_at",
        "expires_at",
        "ttl_remaining_seconds",
        "last_refresh_trigger",
        "last_auth_error",
        "persistent_auth_available",
        "short_session_available",
    }
    assert state["last_refresh_trigger"] == "scheduled"


def test_emit_auth_state_json_contains_required_fields(auth_manager):
    records = []

    class _Log:
        @staticmethod
        def info(msg, *args, **kwargs):  # noqa: ARG004
            records.append(str(msg))

        @staticmethod
        def warning(*args, **kwargs):  # noqa: ARG004
            return None

    auth_manager.log = _Log()
    auth_manager._sync_auth_ttl(
        {
            "passToken": "x",
            "userId": "u",
            "serviceToken": "st",
            "saveTime": int(time.time() * 1000),
            "expires_in": 3600,
        }
    )
    auth_manager._emit_auth_state(
        auth_step="refresh",
        auth_result="ok",
        refresh_trigger="scheduled",
        auth_mode_before="healthy",
        auth_mode_after="healthy",
    )
    line = next((x for x in records if '"event":"auth_state"' in x), "")
    assert '"auth_session_id":' in line
    assert '"login_at":' in line
    assert '"expires_at":' in line
    assert '"ttl_remaining_seconds":' in line
    assert '"estimated_ttl":' in line
    assert '"refresh_trigger":"scheduled"' in line
    assert '"auth_step":"refresh"' in line
    assert '"auth_result":"ok"' in line
    assert '"auth_mode_before":"healthy"' in line
    assert '"auth_mode_after":"healthy"' in line


def test_clear_short_session_records_recovery_stage(auth_manager):
    changed = auth_manager._clear_short_lived_session(
        clear_reason="mina:player_get_status",
        err="401 unauthorized",
    )
    assert changed is True
    state = auth_manager.auth_recovery_debug_state()
    clear_stage = state["last_clear_short_session"]
    assert clear_stage["event"] == "auth_recovery_stage"
    assert clear_stage["stage"] == "clear_short_session"
    assert clear_stage["result"] == "ok"
    assert "cleared_fields" in clear_stage
    assert "has_passToken" in clear_stage
    assert "has_serviceToken" in clear_stage
    assert "has_yetAnotherServiceToken" in clear_stage
    assert "auth_json_writeback" in clear_stage


@pytest.mark.asyncio
async def test_login_exchange_stage_disabled_when_short_missing(auth_manager):
    token_path = Path(auth_manager.auth_token_path)
    data = json.loads(token_path.read_text(encoding="utf-8"))
    data.pop("serviceToken", None)
    data.pop("yetAnotherServiceToken", None)
    token_path.write_text(json.dumps(data), encoding="utf-8")

    auth_manager._clear_short_lived_session(
        clear_reason="mina:player_get_status",
        err="401 unauthorized",
    )
    with pytest.raises(RuntimeError):
        await auth_manager.login_miboy(
            allow_login_fallback=True, reason="mina:player_get_status"
        )
    state = auth_manager.auth_recovery_debug_state()
    login_stage = state["last_login_exchange"]
    assert login_stage["stage"] == "login_exchange"
    assert login_stage["result"] == "failed"
    assert login_stage["provider"] == "micoapi"


@pytest.mark.asyncio
async def test_login_exchange_stage_failed_records_reason(auth_manager, monkeypatch):
    from xiaomusic import auth as auth_module

    class _FailAccount(auth_module.MiAccount):
        async def login(self, *args, **kwargs):  # noqa: ARG002
            raise AssertionError("auto login fallback should not call MiAccount.login")

    monkeypatch.setattr(auth_module, "MiAccount", _FailAccount)
    token_path = Path(auth_manager.auth_token_path)
    data = json.loads(token_path.read_text(encoding="utf-8"))
    data.pop("serviceToken", None)
    data.pop("yetAnotherServiceToken", None)
    token_path.write_text(json.dumps(data), encoding="utf-8")

    auth_manager._clear_short_lived_session(
        clear_reason="mina:player_get_status",
        err="401 unauthorized",
    )
    with pytest.raises(RuntimeError):
        await auth_manager.login_miboy(
            allow_login_fallback=True, reason="mina:player_get_status"
        )
    state = auth_manager.auth_recovery_debug_state()
    login_stage = state["last_login_exchange"]
    assert login_stage["stage"] == "login_exchange"
    assert login_stage["result"] == "failed"
    assert login_stage["error_code"] == "refresh_failed"


@pytest.mark.asyncio
async def test_runtime_rebind_stage_records_rebind_flags(auth_manager, monkeypatch):
    auth_manager._clear_short_lived_session(
        clear_reason="mina:player_get_status",
        err="401 unauthorized",
    )

    async def _fake_login(*args, **kwargs):  # noqa: ARG002
        auth_manager.mina_service = object()
        auth_manager.miio_service = object()
        return None

    async def _fake_verify():
        return True

    monkeypatch.setattr(auth_manager, "login_miboy", _fake_login)
    monkeypatch.setattr(auth_manager, "_verify_runtime_auth_ready", _fake_verify)

    out = await auth_manager.rebuild_services(
        reason="mina:player_get_status", allow_login_fallback=True
    )
    assert out is True
    stage = auth_manager.auth_recovery_debug_state()["last_runtime_rebind"]
    assert stage["stage"] == "runtime_rebind"
    assert stage["result"] == "ok"
    assert "runtime_instance_rebuilt" in stage
    assert "mina_service_rebound" in stage
    assert "token_source" in stage


def test_playback_capability_stage_no_secret_and_not_in_normal_path(auth_manager):
    state_before = auth_manager.auth_recovery_debug_state()
    assert state_before["last_playback_capability_verify"] == {}

    auth_manager.record_playback_capability_verify(
        result="failed",
        verify_method="playback_dispatch",
        playback_capability_level="actual_playback_path",
        transport="mina",
        error_code="E_TEST",
        error_message="no recovery active",
    )
    state_mid = auth_manager.auth_recovery_debug_state()
    assert state_mid["last_playback_capability_verify"] == {}

    auth_manager._clear_short_lived_session(
        clear_reason="mina:player_get_status",
        err="401 unauthorized",
    )
    auth_manager.record_playback_capability_verify(
        result="failed",
        verify_method="playback_dispatch",
        playback_capability_level="actual_playback_path",
        transport="mina",
        error_code="E_TEST",
        error_message="dispatch failed",
    )
    stage = auth_manager.auth_recovery_debug_state()["last_playback_capability_verify"]
    assert stage["stage"] == "playback_capability_verify"
    assert stage["verify_method"] == "playback_dispatch"
    assert stage["playback_capability_level"] == "actual_playback_path"
    assert "serviceToken" not in json.dumps(stage, ensure_ascii=False)
    assert "passToken" not in json.dumps(stage, ensure_ascii=False)


@pytest.mark.asyncio
async def test_mi_login_input_snapshot_is_masked(auth_manager):
    token_path = Path(auth_manager.auth_token_path)
    data = json.loads(token_path.read_text(encoding="utf-8"))
    data.pop("serviceToken", None)
    data.pop("yetAnotherServiceToken", None)
    token_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(RuntimeError):
        await auth_manager.login_miboy(allow_login_fallback=True, reason="ut-mi-trace")
    trace = auth_manager.miaccount_login_trace_debug_state()["login_input_snapshot"]
    assert trace["event"] == "miaccount_login_trace"
    assert trace["stage"] == "login_input_snapshot"
    assert trace["sid"] == "micoapi"
    assert trace["token_dict_is_none"] is False
    assert "keep-pass" not in json.dumps(trace, ensure_ascii=False)


@pytest.mark.asyncio
async def test_mi_login_fallback_disabled_by_policy(auth_manager, monkeypatch):
    from xiaomusic import auth as auth_module

    calls = {"login": 0}

    class _FailAccount(auth_module.MiAccount):
        async def login(self, *args, **kwargs):  # noqa: ARG002
            calls["login"] += 1
            raise RuntimeError("code: 70016, description: 登录验证失败")

    monkeypatch.setattr(auth_module, "MiAccount", _FailAccount)

    token_path = Path(auth_manager.auth_token_path)
    data = json.loads(token_path.read_text(encoding="utf-8"))
    data.pop("serviceToken", None)
    data.pop("yetAnotherServiceToken", None)
    token_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(RuntimeError):
        await auth_manager.login_miboy(
            allow_login_fallback=True, reason="ut-mi-disabled"
        )
    trace = auth_manager.miaccount_login_trace_debug_state()
    assert trace["login_http_exchange"]["result"] == "skipped"
    assert trace["login_http_exchange"]["disabled_by_policy"] is True
    assert trace["post_login_runtime_seed"]["result"] == "failed"
    assert calls["login"] == 0


def test_token_writeback_records_targets(auth_manager):
    from xiaomusic.security.token_store import TokenStore

    auth_manager.token_store = TokenStore(auth_manager.config, _DummyLog())
    auth_manager.token_store.reload_from_disk()

    class _Account:
        token = {
            "passToken": "x",
            "userId": "u",
            "deviceId": "d1",
            "micoapi": ("ss", "new-short-token"),
        }

    auth_manager._persist_auth_data(
        auth_manager._get_auth_data(), _Account(), reason="ut-writeback"
    )
    stage = auth_manager.miaccount_login_trace_debug_state()["token_writeback"]
    assert stage["result"] == "ok"
    assert stage["wrote_serviceToken"] is True
    assert stage["wrote_target"] == "both"


@pytest.mark.asyncio
async def test_post_login_runtime_seed_source_recorded(auth_manager):
    await auth_manager.login_miboy(allow_login_fallback=False, reason="ut-runtime-seed")
    stage = auth_manager.miaccount_login_trace_debug_state()["post_login_runtime_seed"]
    assert stage["result"] == "ok"
    assert stage["runtime_seed_source"] == "mi_account.token"


def test_non_login_paths_do_not_emit_mi_login_trace(auth_manager):
    state = auth_manager.miaccount_login_trace_debug_state()
    assert state["login_input_snapshot"] == {}
    auth_manager.record_playback_capability_verify(
        result="failed",
        verify_method="playback_dispatch",
        playback_capability_level="actual_playback_path",
        transport="mina",
        error_code="E_TEST",
        error_message="x",
    )
    state_after = auth_manager.miaccount_login_trace_debug_state()
    assert state_after["login_input_snapshot"] == {}


@pytest.mark.asyncio
async def test_rebuild_short_session_from_persistent_auth_success(
    auth_manager, monkeypatch
):
    token_path = Path(auth_manager.auth_token_path)
    data = json.loads(token_path.read_text(encoding="utf-8"))
    data.pop("serviceToken", None)
    data.pop("yetAnotherServiceToken", None)
    data["psecurity"] = "pp"
    data["cUserId"] = "cu"
    token_path.write_text(json.dumps(data), encoding="utf-8")

    class _FakeMiJiaAPI:
        def __init__(self, auth_data_path=None, token_store=None):  # noqa: ARG002
            self.token_store = token_store

        def rebuild_service_cookies_from_persistent_auth(self, sid="micoapi"):  # noqa: ARG002
            payload = json.loads(token_path.read_text(encoding="utf-8"))
            payload["serviceToken"] = "rebuilt-st"
            payload["yetAnotherServiceToken"] = "rebuilt-yast"
            if self.token_store is not None:
                self.token_store.update(payload, reason="ut-short-rebuild")
                self.token_store.flush()
            else:
                token_path.write_text(json.dumps(payload), encoding="utf-8")
            return {
                "ok": True,
                "sid": sid,
                "http_stage": "redirect",
                "writeback_target": "auth_json",
            }

    fake_module = types.ModuleType("xiaomusic.qrcode_login")
    fake_module.MiJiaAPI = _FakeMiJiaAPI
    monkeypatch.setitem(sys.modules, "xiaomusic.qrcode_login", fake_module)
    ok = await auth_manager._rebuild_short_session_from_persistent_auth(
        "ut-short-rebuild"
    )
    assert ok is True
    after = json.loads(token_path.read_text(encoding="utf-8"))
    assert after.get("serviceToken") == "rebuilt-st"
    assert after.get("yetAnotherServiceToken") == "rebuilt-yast"
    runtime_view = auth_manager._get_auth_data()
    assert runtime_view.get("serviceToken") == "rebuilt-st"
    assert runtime_view.get("yetAnotherServiceToken") == "rebuilt-yast"


@pytest.mark.asyncio
async def test_ensure_logged_in_locks_only_when_persistent_auth_missing(auth_manager):
    token_path = Path(auth_manager.auth_token_path)
    data = json.loads(token_path.read_text(encoding="utf-8"))
    data.pop("passToken", None)
    data.pop("serviceToken", None)
    data.pop("yetAnotherServiceToken", None)
    token_path.write_text(json.dumps(data), encoding="utf-8")

    async def _need_login():
        return True

    auth_manager.need_login = _need_login

    with pytest.raises(RuntimeError):
        await auth_manager.ensure_logged_in(
            force=True, reason="ut-long-missing", prefer_refresh=True
        )
    assert auth_manager.is_auth_locked() is True


@pytest.mark.asyncio
async def test_rebuild_short_session_records_missing_persistent_auth_fields(
    auth_manager,
):
    token_path = Path(auth_manager.auth_token_path)
    data = json.loads(token_path.read_text(encoding="utf-8"))
    data.pop("cUserId", None)
    data.pop("serviceToken", None)
    data.pop("yetAnotherServiceToken", None)
    token_path.write_text(json.dumps(data), encoding="utf-8")

    ok = await auth_manager._rebuild_short_session_from_persistent_auth(
        "ut-missing-long-auth"
    )
    assert ok is False
    stage = auth_manager.auth_rebuild_debug_state()["last_rebuild_short_session"]
    assert stage["result"] == "failed"
    assert stage["error_code"] == "missing_persistent_auth_fields"
    assert "missing_persistent_auth_fields:cUserId" in stage["failed_reason"]


@pytest.mark.asyncio
async def test_rebuild_short_session_does_not_call_login_on_long_missing(
    auth_manager, monkeypatch
):
    token_path = Path(auth_manager.auth_token_path)
    data = json.loads(token_path.read_text(encoding="utf-8"))
    data.pop("passToken", None)
    data.pop("serviceToken", None)
    data.pop("yetAnotherServiceToken", None)
    token_path.write_text(json.dumps(data), encoding="utf-8")

    calls = {"refresh": 0}

    class _FailMiJiaAPI:
        def __init__(self, auth_data_path=None, token_store=None):  # noqa: ARG002
            return None

        def rebuild_service_cookies_from_persistent_auth(self, sid="micoapi"):  # noqa: ARG002
            calls["refresh"] += 1
            raise AssertionError("should not refresh when long auth is missing")

    fake_module = types.ModuleType("xiaomusic.qrcode_login")
    fake_module.MiJiaAPI = _FailMiJiaAPI
    monkeypatch.setitem(sys.modules, "xiaomusic.qrcode_login", fake_module)
    ok = await auth_manager._rebuild_short_session_from_persistent_auth(
        "ut-no-long-auth"
    )
    assert ok is False
    assert calls["refresh"] == 0


@pytest.mark.asyncio
async def test_rebuild_short_session_primary_failed_then_refresh_fallback_success(
    auth_manager, monkeypatch
):
    token_path = Path(auth_manager.auth_token_path)
    data = json.loads(token_path.read_text(encoding="utf-8"))
    data.pop("serviceToken", None)
    data.pop("yetAnotherServiceToken", None)
    data["psecurity"] = "pp"
    data["cUserId"] = "cu"
    token_path.write_text(json.dumps(data), encoding="utf-8")

    calls = {"primary": 0, "fallback": 0}

    class _FakeMiJiaAPI:
        def __init__(self, auth_data_path=None, token_store=None):  # noqa: ARG002
            self.token_store = token_store

        def rebuild_service_cookies_from_persistent_auth(self, sid="micoapi"):  # noqa: ARG002
            calls["primary"] += 1
            return {
                "ok": False,
                "error_code": "persistent_auth_login_failed",
                "failed_reason": "service_login_not_authorized",
                "sid": sid,
                "http_stage": "serviceLogin",
                "writeback_target": "none",
            }

        def _refresh_token(self, force=False):  # noqa: ARG002
            calls["fallback"] += 1
            payload = json.loads(token_path.read_text(encoding="utf-8"))
            payload["serviceToken"] = "fallback-st"
            payload["yetAnotherServiceToken"] = "fallback-yast"
            if self.token_store is not None:
                self.token_store.update(payload, reason="ut-refresh-fallback")
                self.token_store.flush()
            else:
                token_path.write_text(json.dumps(payload), encoding="utf-8")
            return payload

    fake_module = types.ModuleType("xiaomusic.qrcode_login")
    fake_module.MiJiaAPI = _FakeMiJiaAPI
    monkeypatch.setitem(sys.modules, "xiaomusic.qrcode_login", fake_module)

    ok = await auth_manager._rebuild_short_session_from_persistent_auth("ut-fallback")
    assert ok is True
    assert calls == {"primary": 1, "fallback": 1}
    data_after = auth_manager._get_auth_data()
    assert data_after.get("serviceToken") == "fallback-st"
    stage = auth_manager.auth_short_session_rebuild_debug_state()[
        "last_auth_recovery_flow"
    ]
    # recovery flow is emitted by ensure_logged_in; direct helper call should keep it empty.
    assert isinstance(stage, dict)


@pytest.mark.asyncio
async def test_rebuild_short_session_uses_second_persistent_path_before_refresh_fallback(
    auth_manager, monkeypatch
):
    token_path = Path(auth_manager.auth_token_path)
    data = json.loads(token_path.read_text(encoding="utf-8"))
    data.pop("serviceToken", None)
    data.pop("yetAnotherServiceToken", None)
    token_path.write_text(json.dumps(data), encoding="utf-8")

    calls = {"miaccount": 0, "mijia": 0, "fallback": 0}

    async def _miaccount(*, before, reason, sid="micoapi"):  # noqa: ARG001
        calls["miaccount"] += 1
        return {
            "ok": False,
            "error_code": "redirect_failed",
            "failed_reason": "redirect_http_status",
            "used_path": "miaccount_persistent_auth_login",
            "sid": sid,
        }

    async def _mijia(*, auth_dir, sid="micoapi"):  # noqa: ARG001
        calls["mijia"] += 1
        payload = json.loads(token_path.read_text(encoding="utf-8"))
        payload["serviceToken"] = "second-path-st"
        payload["yetAnotherServiceToken"] = "second-path-yast"
        token_path.write_text(json.dumps(payload), encoding="utf-8")
        return {
            "ok": True,
            "used_path": "mijia_persistent_auth_login",
            "sid": sid,
            "writeback_target": "auth_json",
            "http_stage": "redirect",
        }

    async def _fallback(reason):  # noqa: ARG001
        calls["fallback"] += 1
        return {"ok": False, "used_path": "refresh_token_fallback"}

    auth_manager._try_miaccount_persistent_auth_relogin = _miaccount
    auth_manager._try_mijia_persistent_auth_relogin = _mijia
    auth_manager._rebuild_short_session_tokens_via_refresh_fallback = _fallback

    out = await auth_manager._rebuild_short_session_tokens_from_persistent_auth(
        "ut-second-path"
    )
    assert out["ok"] is True
    assert out["used_path"] == "mijia_persistent_auth_login"
    assert calls == {"miaccount": 1, "mijia": 1, "fallback": 0}


@pytest.mark.asyncio
async def test_ensure_logged_in_records_degraded_not_healthy_when_all_recovery_paths_fail(
    auth_manager,
):
    async def _need_login():
        return True

    async def _short_rebuild(reason):  # noqa: ARG001
        auth_manager._last_short_session_rebuild_detail = {
            "ok": False,
            "used_path": "refresh_token_fallback",
            "error_code": "short_session_rebuild_failed",
        }
        return False

    auth_manager.need_login = _need_login
    auth_manager._rebuild_short_session_from_persistent_auth = _short_rebuild

    with pytest.raises(RuntimeError):
        await auth_manager.ensure_logged_in(
            force=True, reason="ut-degraded-flow", prefer_refresh=True
        )
    flow = auth_manager.auth_short_session_rebuild_debug_state()[
        "last_auth_recovery_flow"
    ]
    assert flow["result"] == "failed"
    assert flow["final_auth_mode"] == "degraded"
    assert flow["runtime_rebind_result"] == "failed"
    assert flow["verify_result"] == "failed"


@pytest.mark.asyncio
async def test_init_all_data_records_recovery_flow_on_success(auth_manager):
    calls = {"login": 0, "device_update": 0}
    token_path = Path(auth_manager.auth_token_path)
    data = json.loads(token_path.read_text(encoding="utf-8"))
    data.pop("serviceToken", None)
    data.pop("yetAnotherServiceToken", None)
    token_path.write_text(json.dumps(data), encoding="utf-8")

    async def _need_login():
        return True

    async def _can_login():
        return True

    async def _login(allow_login_fallback=False, reason=""):  # noqa: ARG001
        calls["login"] += 1
        return None

    async def _rebuild(reason):  # noqa: ARG001
        auth_manager._last_short_session_rebuild_detail = {
            "ok": True,
            "used_path": "miaccount_persistent_auth_login",
        }
        return True

    async def _update(auth):  # noqa: ARG001
        calls["device_update"] += 1

    auth_manager.need_login = _need_login
    auth_manager.can_login = _can_login
    auth_manager.login_miboy = _login
    auth_manager._rebuild_short_session_from_persistent_auth = _rebuild
    auth_manager.device_manager.update_device_info = _update
    auth_manager._last_login_ts = 0
    auth_manager._login_cooldown_sec = 0

    await auth_manager.init_all_data()
    flow = auth_manager.auth_short_session_rebuild_debug_state()[
        "last_auth_recovery_flow"
    ]
    assert flow["result"] == "ok"
    assert flow["runtime_rebind_result"] == "ok"
    assert flow["verify_result"] == "ok"
    assert (
        auth_manager.auth_short_session_rebuild_debug_state()["last_verify"]["result"]
        == "ok"
    )
    assert calls == {"login": 1, "device_update": 1}


@pytest.mark.asyncio
async def test_init_all_data_sets_degraded_when_recovery_fails(auth_manager):
    token_path = Path(auth_manager.auth_token_path)
    data = json.loads(token_path.read_text(encoding="utf-8"))
    data.pop("serviceToken", None)
    data.pop("yetAnotherServiceToken", None)
    token_path.write_text(json.dumps(data), encoding="utf-8")

    async def _need_login():
        return True

    async def _can_login():
        return True

    async def _login(allow_login_fallback=False, reason=""):  # noqa: ARG001
        raise RuntimeError(
            "missing short session token; rebuild from long auth required"
        )

    async def _rebuild(reason):  # noqa: ARG001
        auth_manager._last_short_session_rebuild_detail = {
            "ok": False,
            "used_path": "refresh_token_fallback",
        }
        return False

    auth_manager.need_login = _need_login
    auth_manager.can_login = _can_login
    auth_manager.login_miboy = _login
    auth_manager._rebuild_short_session_from_persistent_auth = _rebuild
    auth_manager._last_login_ts = 0
    auth_manager._login_cooldown_sec = 0

    with pytest.raises(RuntimeError):
        await auth_manager.init_all_data()
    assert auth_manager.auth_debug_state()["auth_mode"] == "degraded"


@pytest.mark.asyncio
async def test_ensure_logged_in_short_rebuild_failed_without_lock_when_persistent_auth_exists(
    auth_manager, monkeypatch
):
    token_path = Path(auth_manager.auth_token_path)
    data = json.loads(token_path.read_text(encoding="utf-8"))
    data["psecurity"] = "pp"
    data["cUserId"] = "cu"
    token_path.write_text(json.dumps(data), encoding="utf-8")

    async def _need_login():
        return True

    async def _primary(reason, sid="micoapi"):  # noqa: ARG001
        return {
            "ok": False,
            "error_code": "persistent_auth_login_failed",
            "failed_reason": "service_login_not_authorized",
            "used_path": "relogin_with_persistent_auth",
            "sid": sid,
        }

    async def _fallback(reason):  # noqa: ARG001
        return {
            "ok": False,
            "error_code": "short_session_refresh_failed",
            "failed_reason": "refresh_failed",
            "used_path": "refresh_token_fallback",
        }

    auth_manager.need_login = _need_login
    auth_manager._rebuild_service_cookies_from_persistent_auth = _primary
    auth_manager._rebuild_short_session_tokens_via_refresh_fallback = _fallback

    with pytest.raises(RuntimeError):
        await auth_manager.ensure_logged_in(
            force=True, reason="ut-no-lock", prefer_refresh=True
        )
    assert auth_manager.is_auth_locked() is False
    flow = auth_manager.auth_short_session_rebuild_debug_state()[
        "last_auth_recovery_flow"
    ]
    assert flow["result"] == "failed"
    assert flow["used_rebuild_strategy"] == "refresh_token_fallback"
    assert flow["used_refresh_fallback"] is True


@pytest.mark.asyncio
async def test_init_all_data_rebuilds_when_existing_short_verify_failed(auth_manager):
    calls = {"login": 0, "rebuild": 0, "clear": 0, "device_update": 0}

    async def _need_login():
        return True

    async def _can_login():
        return True

    async def _login(allow_login_fallback=False, reason=""):  # noqa: ARG001
        calls["login"] += 1
        if calls["login"] == 1:
            raise RuntimeError(
                "service token verify failed and login fallback disabled"
            )
        return None

    async def _rebuild(reason):  # noqa: ARG001
        calls["rebuild"] += 1
        return True

    def _clear(clear_reason: str, err=None):  # noqa: ARG001
        calls["clear"] += 1
        return True

    async def _update(auth):  # noqa: ARG001
        calls["device_update"] += 1

    auth_manager.need_login = _need_login
    auth_manager.can_login = _can_login
    auth_manager.login_miboy = _login
    auth_manager._rebuild_short_session_from_persistent_auth = _rebuild
    auth_manager._clear_short_lived_session = _clear
    auth_manager.device_manager.update_device_info = _update
    auth_manager._last_login_ts = 0
    auth_manager._login_cooldown_sec = 0

    await auth_manager.init_all_data()
    assert calls == {"login": 2, "rebuild": 1, "clear": 1, "device_update": 1}


@pytest.mark.asyncio
async def test_ensure_logged_in_updates_cookie_rebuild_outcome_for_miaccount_strategy(
    auth_manager,
):
    calls = {"update": 0}

    async def _need_login():
        return True

    async def _short_rebuild(reason):  # noqa: ARG001
        auth_manager._last_short_session_rebuild_detail = {
            "ok": True,
            "used_path": "miaccount_persistent_auth_login",
        }
        return True

    async def _rebuild(reason, allow_login_fallback=False):  # noqa: ARG001
        return True

    def _update_outcome(runtime_rebind_result: str, verify_result: str):
        calls["update"] += 1
        assert runtime_rebind_result == "ok"
        assert verify_result == "ok"

    auth_manager.need_login = _need_login
    auth_manager._rebuild_short_session_from_persistent_auth = _short_rebuild
    auth_manager.rebuild_services = _rebuild
    auth_manager._update_last_cookie_rebuild_outcome = _update_outcome

    out = await auth_manager.ensure_logged_in(
        force=True, reason="ut-miaccount-strategy", prefer_refresh=True
    )
    assert out is True
    assert calls["update"] == 1


@pytest.mark.asyncio
async def test_ensure_logged_in_updates_cookie_rebuild_outcome_for_mijia_strategy(
    auth_manager,
):
    calls = {"update": 0}

    async def _need_login():
        return True

    async def _short_rebuild(reason):  # noqa: ARG001
        auth_manager._last_short_session_rebuild_detail = {
            "ok": True,
            "used_path": "mijia_persistent_auth_login",
        }
        return True

    async def _rebuild(reason, allow_login_fallback=False):  # noqa: ARG001
        return True

    def _update_outcome(runtime_rebind_result: str, verify_result: str):
        calls["update"] += 1
        assert runtime_rebind_result == "ok"
        assert verify_result == "ok"

    auth_manager.need_login = _need_login
    auth_manager._rebuild_short_session_from_persistent_auth = _short_rebuild
    auth_manager.rebuild_services = _rebuild
    auth_manager._update_last_cookie_rebuild_outcome = _update_outcome

    out = await auth_manager.ensure_logged_in(
        force=True, reason="ut-mijia-strategy", prefer_refresh=True
    )
    assert out is True
    assert calls["update"] == 1


@pytest.mark.asyncio
async def test_ensure_logged_in_does_not_update_cookie_outcome_for_refresh_fallback(
    auth_manager,
):
    calls = {"update": 0}

    async def _need_login():
        return True

    async def _short_rebuild(reason):  # noqa: ARG001
        auth_manager._last_short_session_rebuild_detail = {
            "ok": True,
            "used_path": "refresh_token_fallback",
        }
        return True

    async def _rebuild(reason, allow_login_fallback=False):  # noqa: ARG001
        return True

    def _update_outcome(runtime_rebind_result: str, verify_result: str):  # noqa: ARG001
        calls["update"] += 1

    auth_manager.need_login = _need_login
    auth_manager._rebuild_short_session_from_persistent_auth = _short_rebuild
    auth_manager.rebuild_services = _rebuild
    auth_manager._update_last_cookie_rebuild_outcome = _update_outcome

    out = await auth_manager.ensure_logged_in(
        force=True, reason="ut-refresh-strategy", prefer_refresh=True
    )
    assert out is True
    assert calls["update"] == 0


@pytest.mark.asyncio
async def test_manual_reload_runtime_uses_disk_path_without_refresh_api(auth_manager):
    calls = {"refresh": 0, "rebuild": 0, "device_update": 0}

    async def _refresh(reason, force=False):  # noqa: ARG001
        calls["refresh"] += 1
        raise AssertionError(
            "manual runtime reload should not call refresh_auth_if_needed"
        )

    async def _rebuild(reason, allow_login_fallback=False):  # noqa: ARG001
        calls["rebuild"] += 1
        auth_manager.mina_service = object()
        auth_manager.miio_service = object()
        return True

    async def _update_device_info(auth):  # noqa: ARG001
        calls["device_update"] += 1

    auth_manager._set_auth_mode("degraded", reason="ut-pre")
    auth_manager.refresh_auth_if_needed = _refresh
    auth_manager.rebuild_services = _rebuild
    auth_manager.device_manager.update_device_info = _update_device_info

    out = await auth_manager.manual_reload_runtime(reason="ut-runtime-reload")
    assert out["runtime_auth_ready"] is True
    assert out["runtime_rebound"] is True
    assert out["device_map_refreshed"] is True
    assert calls == {"refresh": 0, "rebuild": 1, "device_update": 1}
    assert auth_manager.auth_debug_state()["auth_mode"] == "healthy"


@pytest.mark.asyncio
async def test_manual_reload_runtime_reloads_token_store_before_rebuild(auth_manager):
    from xiaomusic.security.token_store import TokenStore

    token_store = TokenStore(auth_manager.config, _DummyLog())
    auth_manager.token_store = token_store
    token_store.reload_from_disk()

    token_path = Path(auth_manager.auth_token_path)
    payload = json.loads(token_path.read_text(encoding="utf-8"))
    payload["serviceToken"] = "disk-new-st"
    payload["yetAnotherServiceToken"] = "disk-new-yast"
    token_path.write_text(json.dumps(payload), encoding="utf-8")

    seen = {"serviceToken": "", "yetAnotherServiceToken": ""}

    async def _rebuild(reason, allow_login_fallback=False):  # noqa: ARG001
        data = auth_manager._get_auth_data()
        seen["serviceToken"] = str(data.get("serviceToken") or "")
        seen["yetAnotherServiceToken"] = str(data.get("yetAnotherServiceToken") or "")
        auth_manager.mina_service = object()
        auth_manager.miio_service = object()
        return True

    async def _update_device_info(auth):  # noqa: ARG001
        return None

    auth_manager.rebuild_services = _rebuild
    auth_manager.device_manager.update_device_info = _update_device_info

    out = await auth_manager.manual_reload_runtime(reason="ut-token-reload")
    assert out["runtime_auth_ready"] is True
    assert out["token_store_reloaded"] is True
    assert seen["serviceToken"] == "disk-new-st"
    assert seen["yetAnotherServiceToken"] == "disk-new-yast"


@pytest.mark.asyncio
async def test_manual_reload_runtime_fails_with_missing_short_tokens(auth_manager):
    token_path = Path(auth_manager.auth_token_path)
    data = json.loads(token_path.read_text(encoding="utf-8"))
    data.pop("serviceToken", None)
    data.pop("yetAnotherServiceToken", None)
    token_path.write_text(json.dumps(data), encoding="utf-8")

    out = await auth_manager.manual_reload_runtime(reason="ut-missing-short")
    assert out["runtime_auth_ready"] is False
    assert out["error_code"] == "short_session_missing_for_runtime_reload"
    assert "short session tokens missing" in str(out["last_error"])
    assert out["missing_long_lived_fields"] == []
    assert "serviceToken" in out["missing_short_session_fields"][0]


def test_auth_debug_state_zeroes_ttl_when_short_session_missing(auth_manager):
    token_path = Path(auth_manager.auth_token_path)
    data = json.loads(token_path.read_text(encoding="utf-8"))
    data.pop("serviceToken", None)
    data.pop("yetAnotherServiceToken", None)
    token_path.write_text(json.dumps(data), encoding="utf-8")

    state = auth_manager.auth_debug_state()
    assert state["short_session_available"] is False
    assert state["persistent_auth_available"] is True
    assert state["ttl_remaining_seconds"] == 0


@pytest.mark.asyncio
async def test_rebuild_short_session_produces_diagnostic_fields(
    auth_manager, monkeypatch
):
    token_path = Path(auth_manager.auth_token_path)
    data = json.loads(token_path.read_text(encoding="utf-8"))
    data.pop("serviceToken", None)
    data.pop("yetAnotherServiceToken", None)
    token_path.write_text(json.dumps(data), encoding="utf-8")

    calls: list[dict] = []

    class _FakeMiAccount:
        def __init__(self, *args, **kwargs):
            self.token = {}

        async def _serviceLogin(self, path):
            raise Exception("simulated network timeout")

    monkeypatch.setattr(auth_manager, "mi_session", None)
    monkeypatch.setattr("miservice.MiAccount", _FakeMiAccount)

    auth_manager.mi_token_home = str(token_path.parent / ".mi.token")

    out = await auth_manager._rebuild_service_cookies_from_persistent_auth(
        reason="test-diag", sid="micoapi"
    )
    assert out["ok"] is False
    assert "path_attempts" in out
    first_attempt = out["path_attempts"][0]
    assert first_attempt["diagnostic"] is not None
    diag = first_attempt["diagnostic"]
    assert "service_login_code" in diag
    assert "http_status" in diag
    assert "has_location" in diag
    assert "is_network_error" in diag


@pytest.mark.asyncio
async def test_path_attempts_include_diagnostic_in_rebuild_result(
    auth_manager, monkeypatch
):
    token_path = Path(auth_manager.auth_token_path)
    data = json.loads(token_path.read_text(encoding="utf-8"))
    data.pop("serviceToken", None)
    data.pop("yetAnotherServiceToken", None)
    token_path.write_text(json.dumps(data), encoding="utf-8")

    class _FakeMiAccount:
        def __init__(self, *args, **kwargs):
            self.token = {}

        async def _serviceLogin(self, path):
            raise Exception("timeout")

    monkeypatch.setattr("miservice.MiAccount", _FakeMiAccount)
    auth_manager.mi_token_home = str(token_path.parent / ".mi.token")

    out = await auth_manager._rebuild_service_cookies_from_persistent_auth(
        reason="test-path-attempts", sid="micoapi"
    )
    assert out["ok"] is False
    assert "path_attempts" in out
    assert len(out["path_attempts"]) >= 1
    first_attempt = out["path_attempts"][0]
    assert first_attempt["result"] == "failed"
    assert first_attempt["error_code"] != ""
    assert first_attempt["failed_reason"] != ""


def test_is_short_session_failure_signal_miio_login_failed():
    """Fix A: miio-class "Login failed" errors (no "mina" in text) should be recognized
    as short-session failure signals, triggering recovery flow."""
    from xiaomusic.auth import AuthManager

    am = AuthManager.__new__(AuthManager)

    assert (
        am._is_short_session_failure_signal(
            reason="miio:text_to_speech",
            err="Error https://api.io.mi.com/app/miotspec/action: Login failed",
        )
        is True
    )

    assert (
        am._is_short_session_failure_signal(
            reason="",
            err="https://api.io.mi.com/remote/ubus: Login failed",
        )
        is True
    )

    assert (
        am._is_short_session_failure_signal(
            reason="",
            err="Error https://api.io.mi.com/app/miotspec/action: Login failed (not mina service)",
        )
        is True
    )

    assert (
        am._is_short_session_failure_signal(
            reason="keepalive",
            err="network timeout",
        )
        is False
    )


def test_is_short_session_failure_signal_mina_still_recognized():
    """Fix A: mina-class "Login failed" errors still work as before."""
    from xiaomusic.auth import AuthManager

    am = AuthManager.__new__(AuthManager)

    assert (
        am._is_short_session_failure_signal(
            reason="mina:player_get_status",
            err="Error https://api2.mina.mi.com/remote/ubus: Login failed",
        )
        is True
    )


@pytest.mark.asyncio
async def test_auth_call_proceeds_recovery_when_clear_skipped_but_need_login(
    auth_manager, monkeypatch
):
    """Fix B: when _clear_short_lived_session returns False (e.g. signal not recognized)
    but need_login() is True, auth_call must still mark recovery as active and
    try non-destructive recovery — recovery should never be silently skipped."""

    call_order = []

    def _clear(clear_reason: str, err=None):
        call_order.append("clear")
        return False

    async def _need_login():
        call_order.append("need_login")
        return True

    async def _ensure(
        force=False, reason="", prefer_refresh=False, recovery_owner=False
    ):
        call_order.append("ensure_logged_in")
        assert force is True
        assert prefer_refresh is True
        return True

    async def _non_destructive(ctx="", reason=""):
        call_order.append("non_destructive")
        return True, "runtime_verified", False

    monkeypatch.setattr(auth_manager, "_clear_short_lived_session", _clear)
    auth_manager.need_login = _need_login
    auth_manager.ensure_logged_in = _ensure
    auth_manager._attempt_non_destructive_auth_recovery = _non_destructive

    state = {"n": 0}

    async def _fn():
        if state["n"] == 0:
            state["n"] += 1
            raise RuntimeError(
                "Error https://api.io.mi.com/app/miotspec/action: Login failed"
            )
        return "ok"

    out = await auth_manager.auth_call(_fn, retry=1, ctx="miio:text_to_speech")
    assert out == "ok"
    # 第一次auth error走非破坏性恢复
    assert "clear" not in call_order, "First auth error should NOT clear"
    assert "non_destructive" in call_order, (
        "First auth error should use non-destructive recovery"
    )
    # 非破坏性恢复成功后不会调用 ensure_logged_in
    assert "ensure_logged_in" not in call_order


@pytest.mark.asyncio
async def test_auth_call_marks_recovery_active_when_clear_skipped(
    auth_manager, monkeypatch
):
    """Fix B: even when _clear_short_lived_session returns False, _mark_recovery_active
    must be called so that subsequent stages in the recovery flow can observe that
    recovery is in progress."""

    def _clear(clear_reason: str, err=None):
        return False

    async def _need_login():
        return True

    async def _ensure(
        force=False, reason="", prefer_refresh=False, recovery_owner=False
    ):
        return True

    # 非破坏性恢复失败
    async def _non_destructive(ctx="", reason=""):
        return False, "all_attempts_failed", False

    monkeypatch.setattr(auth_manager, "_clear_short_lived_session", _clear)
    auth_manager.need_login = _need_login
    auth_manager.ensure_logged_in = _ensure
    auth_manager._attempt_non_destructive_auth_recovery = _non_destructive

    before_chain_id = auth_manager._auth_recovery_chain_id

    async def _fn():
        raise RuntimeError(
            "Error https://api.io.mi.com/app/miotspec/action: Login failed"
        )

    try:
        await auth_manager.auth_call(_fn, retry=0, ctx="miio:text_to_speech")
    except RuntimeError:
        pass

    # 非破坏性恢复失败时应该标记 recovery active
    assert auth_manager._recovery_is_active() is True


@pytest.mark.asyncio
async def test_ensure_logged_in_bypasses_backoff_when_need_login(
    auth_manager, monkeypatch
):
    """When need_login() is True and backoff is active, ensure_logged_in must
    bypass the backoff and proceed with recovery — not raise 'relogin backoff
    active Ns'."""

    auth_manager._next_relogin_allowed_ts = time.time() + 300

    events = []

    class _Log:
        @staticmethod
        def info(*args, **kwargs):
            pass

        @staticmethod
        def warning(msg, *args, **kwargs):
            events.append(str(msg))

    auth_manager.log = _Log()

    async def _need_login():
        return True

    async def _short_rebuild(reason):
        events.append("short_rebuild")
        return True

    async def _rebuild(reason, allow_login_fallback=False):
        events.append("rebuild")
        auth_manager.mina_service = object()
        auth_manager.miio_service = object()
        return True

    auth_manager.need_login = _need_login
    auth_manager._rebuild_short_session_from_persistent_auth = _short_rebuild
    auth_manager.rebuild_services = _rebuild
    auth_manager._last_ok_ts = 0

    out = await auth_manager.ensure_logged_in(
        force=True,
        reason="ut-bypass",
        prefer_refresh=True,
        recovery_owner=True,
    )
    assert out is True
    bypass_log = next((e for e in events if "bypass_backoff=true" in e), None)
    assert bypass_log is not None, f"bypass log not found in {events}"
    assert "reason=need_login" in bypass_log


@pytest.mark.asyncio
async def test_ensure_logged_in_respects_backoff_when_not_need_login(auth_manager):
    """When need_login() is False and backoff is active, ensure_logged_in must
    raise 'relogin backoff active Ns' — normal retry throttling is preserved."""

    auth_manager._next_relogin_allowed_ts = time.time() + 300

    async def _need_login():
        return False

    auth_manager.need_login = _need_login
    auth_manager._last_ok_ts = time.time() - 60

    with pytest.raises(RuntimeError) as exc_info:
        await auth_manager.ensure_logged_in(force=True, reason="ut-backoff-preserved")
    assert "relogin backoff active" in str(exc_info.value)


@pytest.mark.asyncio
async def test_keepalive_proactive_recovery_succeeds_and_resets_streak(
    auth_manager, monkeypatch
):
    """Fix D: when keepalive enters degraded mode, proactive recovery should fire,
    succeed, reset _keepalive_fail_streak to 0, and exit degraded.

    Starting from _keepalive_degraded=True, streak=3: proactive recovery fires,
    succeeds, resets streak to 0, exits degraded."""

    call_ctxs = []

    async def _sleep(delay):
        raise asyncio.CancelledError()

    async def _ensure(
        force=False, reason="", prefer_refresh=False, recovery_owner=False
    ):
        return True

    async def _mina_call(method, *args, **kwargs):
        ctx = kwargs.get("ctx", "")
        call_ctxs.append(ctx)
        if ctx == "keepalive-proactive-recover":
            return []
        raise RuntimeError("401 unauthorized")

    monkeypatch.setattr(asyncio, "sleep", _sleep)
    auth_manager.ensure_logged_in = _ensure
    auth_manager.mina_call = _mina_call
    auth_manager._keepalive_degraded = True
    auth_manager._keepalive_fail_streak = 3

    with pytest.raises(asyncio.CancelledError):
        await auth_manager.keepalive_loop(interval_sec=7)

    assert "keepalive-proactive-recover" in call_ctxs
    assert auth_manager._keepalive_degraded is False
    assert auth_manager._keepalive_fail_streak == 0


@pytest.mark.asyncio
async def test_keepalive_proactive_recovery_respects_cooldown(
    auth_manager, monkeypatch
):
    """Fix D: when _keepalive_recovery_cooldown_ts is in the future,
    proactive recovery must be skipped and the default interval sleep is used.

    Flow: already degraded + cooldown active → no proactive attempt this tick."""

    sleep_delays = []
    ensure_calls = []

    async def _sleep(delay):
        sleep_delays.append(delay)
        raise asyncio.CancelledError()

    async def _ensure(
        force=False, reason="", prefer_refresh=False, recovery_owner=False
    ):
        ensure_calls.append((force, reason))
        return True

    async def _mina_call(method, *args, **kwargs):
        return []

    monkeypatch.setattr(asyncio, "sleep", _sleep)
    auth_manager.ensure_logged_in = _ensure
    auth_manager.mina_call = _mina_call
    auth_manager._keepalive_degraded = True
    auth_manager._keepalive_fail_streak = 3
    auth_manager._keepalive_recovery_cooldown_ts = time.time() + 300

    with pytest.raises(asyncio.CancelledError):
        await auth_manager.keepalive_loop(interval_sec=7)

    assert all(force is False for force, _ in ensure_calls)
    assert sleep_delays == [7]


@pytest.mark.asyncio
async def test_keepalive_proactive_recovery_fails_and_sets_cooldown(
    auth_manager, monkeypatch
):
    """Fix D: when proactive recovery fails, _keepalive_recovery_cooldown_ts must
    be set to prevent immediate re-trigger."""

    ensure_calls = []
    mina_call_count = {"n": 0}

    async def _sleep(delay):
        raise asyncio.CancelledError()

    async def _ensure(
        force=False, reason="", prefer_refresh=False, recovery_owner=False
    ):
        ensure_calls.append((force, reason))
        return False

    async def _mina_call(method, *args, **kwargs):
        mina_call_count["n"] += 1
        raise RuntimeError("401 unauthorized")

    monkeypatch.setattr(asyncio, "sleep", _sleep)
    auth_manager.ensure_logged_in = _ensure
    auth_manager.mina_call = _mina_call
    auth_manager._keepalive_degraded = True
    auth_manager._keepalive_fail_streak = 3

    with pytest.raises(asyncio.CancelledError):
        await auth_manager.keepalive_loop(interval_sec=7)

    proactive_forces = [f for f, r in ensure_calls if "proactive" in r]
    assert proactive_forces == [True]
    assert auth_manager._keepalive_recovery_cooldown_ts > time.time()
    assert auth_manager._keepalive_degraded is True


@pytest.mark.asyncio
async def test_ensure_logged_in_locks_when_persistent_auth_missing(auth_manager):
    token_path = Path(auth_manager.auth_token_path)
    data = json.loads(token_path.read_text(encoding="utf-8"))
    data.pop("passToken", None)
    data.pop("serviceToken", None)
    data.pop("yetAnotherServiceToken", None)
    token_path.write_text(json.dumps(data), encoding="utf-8")

    async def _need_login():
        return True

    auth_manager.need_login = _need_login

    with pytest.raises(RuntimeError):
        await auth_manager.ensure_logged_in(
            force=True, reason="ut-no-persistent", prefer_refresh=True
        )
    assert auth_manager.is_auth_locked() is True


@pytest.mark.asyncio
async def test_keepalive_proactive_recovery_succeeds_after_initial_failure(
    auth_manager, monkeypatch
):
    proactive_attempts = []
    mina_ctxs = []
    sleep_count = {"n": 0}
    ensure_call_n = {"n": 0}

    async def _sleep(delay):
        sleep_count["n"] += 1
        if sleep_count["n"] >= 4:
            raise asyncio.CancelledError()

    async def _noop():
        pass

    async def _need_login():
        return False

    async def _ensure(
        force=False, reason="", prefer_refresh=False, recovery_owner=False
    ):
        ensure_call_n["n"] += 1
        if "proactive" in reason:
            proactive_attempts.append(ensure_call_n["n"])
        if ensure_call_n["n"] == 1:
            return False
        return True

    async def _mina_call(method, *args, **kwargs):
        ctx = kwargs.get("ctx", "")
        mina_ctxs.append(ctx)
        if ctx == "keepalive-proactive-recover":
            return []
        raise RuntimeError("401 unauthorized")

    monkeypatch.setattr(asyncio, "sleep", _sleep)
    monkeypatch.setattr(auth_manager, "_maybe_scheduled_refresh", _noop)
    monkeypatch.setattr(
        auth_manager, "_rebuild_short_session_from_persistent_auth", _noop
    )
    auth_manager.need_login = _need_login
    auth_manager.ensure_logged_in = _ensure
    auth_manager.mina_call = _mina_call
    auth_manager._keepalive_degraded = True
    auth_manager._keepalive_fail_streak = 3
    auth_manager._keepalive_recovery_cooldown_ts = 0.0

    with pytest.raises(asyncio.CancelledError):
        await auth_manager.keepalive_loop(interval_sec=7)

    assert len(proactive_attempts) >= 2, (
        f"Expected >=2 proactive attempts, got {proactive_attempts}"
    )
    assert "keepalive-proactive-recover" in mina_ctxs
    assert auth_manager._keepalive_degraded is False
    assert auth_manager._keepalive_fail_streak == 0


@pytest.mark.asyncio
async def test_backoff_bypass_allows_complete_recovery_flow(auth_manager, monkeypatch):
    auth_manager._next_relogin_allowed_ts = time.time() + 300

    events = []

    class _Log:
        @staticmethod
        def info(*args, **kwargs):
            pass

        @staticmethod
        def warning(msg, *args, **kwargs):
            events.append(str(msg))

    auth_manager.log = _Log()

    async def _need_login():
        return True

    async def _short_rebuild(reason):
        events.append("short_rebuild")
        auth_manager.mina_service = object()
        auth_manager.miio_service = object()
        return True

    async def _rebuild(reason, allow_login_fallback=False):
        events.append("rebuild")
        return True

    def _clear(clear_reason: str, err=None):
        events.append("clear")
        return False

    monkeypatch.setattr(auth_manager, "_clear_short_lived_session", _clear)
    auth_manager.need_login = _need_login
    auth_manager._rebuild_short_session_from_persistent_auth = _short_rebuild
    auth_manager.rebuild_services = _rebuild
    auth_manager._last_ok_ts = 0

    out = await auth_manager.ensure_logged_in(
        force=True,
        reason="ut-bypass-clear",
        prefer_refresh=True,
        recovery_owner=True,
    )
    assert out is True
    bypass_log = next((e for e in events if "bypass_backoff" in e), None)
    assert bypass_log is not None, f"bypass log not found in {events}"
    assert "clear" in events
    assert "short_rebuild" in events
    assert "rebuild" in events


@pytest.mark.asyncio
async def test_keepalive_proactive_recovery_success_resets_last_ok_ts_and_streak(
    auth_manager, monkeypatch
):
    async def _sleep(delay):
        raise asyncio.CancelledError()

    async def _noop():
        pass

    async def _need_login():
        return False

    async def _ensure(
        force=False, reason="", prefer_refresh=False, recovery_owner=False
    ):
        return True

    async def _mina_call(method, *args, **kwargs):
        ctx = kwargs.get("ctx", "")
        if ctx == "keepalive-proactive-recover":
            return []
        raise RuntimeError("401 unauthorized")

    monkeypatch.setattr(asyncio, "sleep", _sleep)
    monkeypatch.setattr(auth_manager, "_maybe_scheduled_refresh", _noop)
    auth_manager.need_login = _need_login
    auth_manager.ensure_logged_in = _ensure
    auth_manager.mina_call = _mina_call
    auth_manager._keepalive_degraded = True
    auth_manager._keepalive_fail_streak = 3

    with pytest.raises(asyncio.CancelledError):
        await auth_manager.keepalive_loop(interval_sec=7)

    assert auth_manager._keepalive_degraded is False
    assert auth_manager._keepalive_fail_streak == 0
    assert auth_manager._last_ok_ts > 0
    assert auth_manager._keepalive_recovery_cooldown_ts == 0.0


# ---------------------------------------------------------------------------
# State Machine Tests
# ---------------------------------------------------------------------------


def test_auth_mode_healthy_to_degraded_to_healthy(auth_manager):
    assert auth_manager._auth_mode == "healthy"

    class _LogCapture:
        def __getattr__(self, name):
            return lambda *a, **k: None

    auth_manager.log = _LogCapture()

    auth_manager._transition_auth_mode("degraded", reason="sm-test-degraded")
    assert auth_manager._auth_mode == "degraded"
    trans = auth_manager.auth_debug_state()["last_auth_mode_transition"]
    assert trans["from"] == "healthy"
    assert trans["to"] == "degraded"
    assert trans["reason"] == "sm-test-degraded"

    auth_manager._transition_auth_mode("healthy", reason="sm-test-recovered")
    assert auth_manager._auth_mode == "healthy"
    trans = auth_manager.auth_debug_state()["last_auth_mode_transition"]
    assert trans["from"] == "degraded"
    assert trans["to"] == "healthy"
    assert trans["reason"] == "sm-test-recovered"


def test_auth_mode_degraded_to_locked(auth_manager):
    auth_manager._transition_auth_mode("degraded", reason="sm-test")
    assert auth_manager._auth_mode == "degraded"

    auth_manager._transition_auth_mode("locked", reason="sm-test-lock")
    assert auth_manager._auth_mode == "locked"
    trans = auth_manager.auth_debug_state()["last_auth_mode_transition"]
    assert trans["from"] == "degraded"
    assert trans["to"] == "locked"


def test_auth_mode_clear_auth_lock_restores_state(auth_manager):
    auth_manager._transition_auth_mode("locked", reason="sm-test")
    assert auth_manager._auth_mode == "locked"

    auth_manager.clear_auth_lock(reason="sm-test-unlock", mode="degraded")
    assert auth_manager._auth_mode == "degraded"

    auth_manager._transition_auth_mode("healthy", reason="sm-test-final")
    assert auth_manager._auth_mode == "healthy"


def test_auth_mode_invalid_mode_rejected(auth_manager):
    auth_manager._transition_auth_mode("healthy", reason="sm-test-init")
    assert auth_manager._auth_mode == "healthy"

    warning_log = []

    class _LogCapture:
        def __getattr__(self, name):
            return lambda *a, **k: None

        def warning(self, msg, *args, **kwargs):
            warning_log.append(str(msg) % args)

    auth_manager.log = _LogCapture()

    auth_manager._transition_auth_mode("foobar", reason="sm-test-invalid")
    assert auth_manager._auth_mode == "healthy"
    assert len(warning_log) == 1
    assert "invalid mode=foobar" in warning_log[0]


@pytest.mark.asyncio
async def test_auth_call_first_auth_error_does_not_clear(auth_manager):
    """首次auth error不应clear short session"""
    clear_calls = []
    original_clear = auth_manager._clear_short_lived_session

    def _track_clear(*args, **kwargs):
        clear_calls.append((args, kwargs))
        return original_clear(*args, **kwargs)

    auth_manager._clear_short_lived_session = _track_clear
    auth_manager.mina_service = type(
        "S", (), {"account": type("A", (), {"token": {}})()}
    )()
    auth_manager.miio_service = None

    decision_logs = []
    original_log = auth_manager.log.info

    def _capture_log(msg, *args, **kwargs):
        if "auth_short_session_clear_decision" in str(msg):
            decision_logs.append(msg)
        return original_log(msg, *args, **kwargs)

    auth_manager.log.info = _capture_log

    call_count = 0

    async def _failing_fn():
        nonlocal call_count
        call_count += 1
        raise Exception("Login failed")

    auth_manager.need_login = lambda: asyncio.sleep(0) or True
    auth_manager.ensure_logged_in = lambda **kw: asyncio.sleep(0) or None

    with pytest.raises(Exception, match="Login failed"):
        await auth_manager.auth_call(_failing_fn, retry=0, ctx="test_ctx")

    assert len(clear_calls) == 0, "First auth error should NOT clear"
    assert len(decision_logs) >= 1, "Should have decision log"
    assert auth_manager._auth_error_suspect_streak == 1


@pytest.mark.asyncio
async def test_auth_call_consecutive_auth_error_clears(auth_manager):
    """连续auth error才clear"""
    clear_calls = []
    original_clear = auth_manager._clear_short_lived_session

    def _track_clear(*args, **kwargs):
        clear_calls.append((args, kwargs))
        return original_clear(*args, **kwargs)

    auth_manager._clear_short_lived_session = _track_clear
    auth_manager.mina_service = type(
        "S", (), {"account": type("A", (), {"token": {}})()}
    )()
    auth_manager.miio_service = None

    auth_manager.need_login = lambda: asyncio.sleep(0) or True
    auth_manager.ensure_logged_in = lambda **kw: asyncio.sleep(0) or None

    call_count = 0

    async def _failing_fn():
        nonlocal call_count
        call_count += 1
        raise Exception("Login failed")

    with pytest.raises(Exception, match="Login failed"):
        await auth_manager.auth_call(_failing_fn, retry=0, ctx="test_ctx")

    assert len(clear_calls) == 0, "First auth error should NOT clear"
    assert auth_manager._auth_error_suspect_streak == 1

    with pytest.raises(Exception, match="Login failed"):
        await auth_manager.auth_call(_failing_fn, retry=0, ctx="test_ctx")

    assert len(clear_calls) == 1, "Second consecutive auth error SHOULD clear"
    assert auth_manager._auth_error_suspect_streak == 0


@pytest.mark.asyncio
async def test_auth_call_non_auth_error_not_affected(auth_manager):
    """非auth error不受影响"""
    clear_calls = []
    original_clear = auth_manager._clear_short_lived_session

    def _track_clear(*args, **kwargs):
        clear_calls.append((args, kwargs))
        return original_clear(*args, **kwargs)

    auth_manager._clear_short_lived_session = _track_clear

    async def _failing_fn():
        raise Exception("Some other error")

    with pytest.raises(Exception, match="Some other error"):
        await auth_manager.auth_call(_failing_fn, retry=0, ctx="test_ctx")

    assert len(clear_calls) == 0, "Non-auth error should NOT trigger clear"
    assert auth_manager._auth_error_suspect_streak == 0


@pytest.mark.asyncio
async def test_auth_call_retry_behavior_preserved(auth_manager):
    """确保auth_call retry行为不被破坏"""
    auth_manager.mina_service = type(
        "S", (), {"account": type("A", (), {"token": {}})()}
    )()
    auth_manager.miio_service = None
    auth_manager.need_login = lambda: asyncio.sleep(0) or True
    auth_manager.ensure_logged_in = lambda **kw: asyncio.sleep(0) or None

    # 非破坏性恢复成功
    async def _successful_recovery(ctx="", reason=""):
        return True, "runtime_verified", False

    auth_manager._attempt_non_destructive_auth_recovery = _successful_recovery

    call_count = 0

    async def _failing_then_succeed():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("Login failed")
        return "success"

    result = await auth_manager.auth_call(
        _failing_then_succeed, retry=1, ctx="test_ctx"
    )
    assert result == "success"
    assert call_count == 2, "Should have retried once"


def test_should_clear_short_session_on_auth_error_logic(auth_manager):
    """测试_should_clear_short_session_on_auth_error的逻辑"""
    auth_manager._reset_auth_error_suspect()

    should_clear, reason = auth_manager._should_clear_short_session_on_auth_error(
        ctx="ctx1"
    )
    assert not should_clear
    assert reason == "first_suspect"

    auth_manager._record_auth_error_suspect(ctx="ctx1")

    should_clear, reason = auth_manager._should_clear_short_session_on_auth_error(
        ctx="ctx1"
    )
    assert should_clear
    assert reason == "consecutive_auth_error_same_ctx"

    auth_manager._reset_auth_error_suspect()

    auth_manager._record_auth_error_suspect(ctx="ctx1")
    auth_manager._record_auth_error_suspect(ctx="ctx2")

    should_clear, reason = auth_manager._should_clear_short_session_on_auth_error(
        ctx="ctx3"
    )
    assert should_clear
    assert reason == "consecutive_auth_error_multiple"


@pytest.mark.asyncio
async def test_auth_call_first_suspect_uses_non_destructive_recovery(auth_manager):
    """首次auth error应走非破坏性恢复路径，不调用ensure_logged_in(prefer_refresh=True)"""
    clear_calls = []
    ensure_calls = []
    non_destructive_calls = []

    original_clear = auth_manager._clear_short_lived_session

    def _track_clear(*args, **kwargs):
        clear_calls.append((args, kwargs))
        return original_clear(*args, **kwargs)

    async def _track_ensure(**kwargs):
        ensure_calls.append(kwargs)
        return True

    async def _track_non_destructive(ctx="", reason=""):
        non_destructive_calls.append({"ctx": ctx, "reason": reason})
        return True, "runtime_verified", False

    auth_manager._clear_short_lived_session = _track_clear
    auth_manager.ensure_logged_in = _track_ensure
    auth_manager._attempt_non_destructive_auth_recovery = _track_non_destructive
    auth_manager.mina_service = type(
        "S", (), {"account": type("A", (), {"token": {}})()}
    )()
    auth_manager.miio_service = None
    auth_manager.need_login = lambda: asyncio.sleep(0) or True

    call_count = 0

    async def _failing_then_succeed():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("Login failed")
        return "success"

    result = await auth_manager.auth_call(
        _failing_then_succeed, retry=1, ctx="test_ctx"
    )
    assert result == "success"
    assert len(clear_calls) == 0, "First auth error should NOT clear"
    assert len(ensure_calls) == 0, "First auth error should NOT call ensure_logged_in"
    assert len(non_destructive_calls) == 1, (
        "First auth error should use non-destructive recovery"
    )
    assert non_destructive_calls[0]["ctx"] == "test_ctx"


@pytest.mark.asyncio
async def test_auth_call_upgraded_clear_uses_ensure_logged_in(auth_manager):
    """升级后的auth error应走clear+rebuild路径"""
    clear_calls = []
    ensure_calls = []

    original_clear = auth_manager._clear_short_lived_session

    def _track_clear(*args, **kwargs):
        clear_calls.append((args, kwargs))
        return original_clear(*args, **kwargs)

    async def _track_ensure(**kwargs):
        ensure_calls.append(kwargs)
        return True

    auth_manager._clear_short_lived_session = _track_clear
    auth_manager.ensure_logged_in = _track_ensure
    auth_manager.mina_service = type(
        "S", (), {"account": type("A", (), {"token": {}})()}
    )()
    auth_manager.miio_service = None
    auth_manager.need_login = lambda: asyncio.sleep(0) or True

    call_count = 0

    async def _failing_fn():
        nonlocal call_count
        call_count += 1
        raise Exception("Login failed")

    # 第一次 - 不clear
    with pytest.raises(Exception):
        await auth_manager.auth_call(_failing_fn, retry=0, ctx="test_ctx")

    assert len(clear_calls) == 0, "First auth error should NOT clear"
    assert len(ensure_calls) == 0, "First auth error should NOT call ensure_logged_in"

    # 第二次 - 应该clear
    with pytest.raises(Exception):
        await auth_manager.auth_call(_failing_fn, retry=0, ctx="test_ctx")

    assert len(clear_calls) == 1, "Second auth error SHOULD clear"
    assert len(ensure_calls) == 1, "Second auth error SHOULD call ensure_logged_in"
    assert ensure_calls[0].get("prefer_refresh") is True


@pytest.mark.asyncio
async def test_auth_call_non_destructive_failure_preserves_short_session(auth_manager):
    """非破坏性恢复失败时不应丢short session"""
    # 设置auth data中有short session (使用fixture中的默认值)
    import json

    clear_calls = []
    original_clear = auth_manager._clear_short_lived_session

    def _track_clear(*args, **kwargs):
        clear_calls.append((args, kwargs))
        return original_clear(*args, **kwargs)

    auth_manager._clear_short_lived_session = _track_clear
    auth_manager.mina_service = type(
        "S", (), {"account": type("A", (), {"token": {}})()}
    )()
    auth_manager.miio_service = None
    auth_manager.need_login = lambda: asyncio.sleep(0) or True

    # 非破坏性恢复失败
    async def _failing_recovery(ctx="", reason=""):
        return False, "all_attempts_failed", False

    auth_manager._attempt_non_destructive_auth_recovery = _failing_recovery

    async def _failing_fn():
        raise Exception("Login failed")

    with pytest.raises(Exception):
        await auth_manager.auth_call(_failing_fn, retry=0, ctx="test_ctx")

    # 验证没有clear
    assert len(clear_calls) == 0, "Non-destructive failure should NOT clear"

    # 验证short session仍在文件中
    with open(auth_manager.auth_token_path) as f:
        saved_data = json.load(f)

    assert "serviceToken" in saved_data, "Short session should be preserved"
    assert saved_data["serviceToken"] == "st"


@pytest.mark.asyncio
async def test_auth_call_non_destructive_recovery_success_retries(auth_manager):
    """非破坏性恢复成功后应重试原请求"""
    auth_manager.mina_service = type(
        "S", (), {"account": type("A", (), {"token": {}})()}
    )()
    auth_manager.miio_service = None
    auth_manager.need_login = lambda: asyncio.sleep(0) or True

    # 非破坏性恢复成功
    async def _successful_recovery(ctx="", reason=""):
        return True, "runtime_verified", False

    auth_manager._attempt_non_destructive_auth_recovery = _successful_recovery

    call_count = 0

    async def _failing_then_succeed():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("Login failed")
        return "success"

    result = await auth_manager.auth_call(
        _failing_then_succeed, retry=1, ctx="test_ctx"
    )
    assert result == "success"
    assert call_count == 2, "Should have retried after successful recovery"


@pytest.mark.asyncio
async def test_runtime_rebuild_with_existing_short_session_success(auth_manager):
    """测试用已有 short session 重建 runtime 成功"""
    import json

    # 设置 auth.json 中有 short session
    auth_data = {
        "passToken": "test_pass",
        "userId": "test_user",
        "cUserId": "test_cuser",
        "psecurity": "test_psecurity",
        "serviceToken": "test_service_token",
        "ssecurity": "test_ssecurity",
        "deviceId": "test_device_id",
    }
    with open(auth_manager.auth_token_path, "w") as f:
        json.dump(auth_data, f)

    # 模拟 device_list 成功
    class MockMiAccount:
        def __init__(self, *args, **kwargs):
            self.token = {}

    class MockMiNAService:
        def __init__(self, account):
            self.account = account

        async def device_list(self):
            return [{"did": "test_device"}]

    # Mock MiAccount 和 MiNAService
    import xiaomusic.auth as auth_module

    original_MiAccount = auth_module.MiAccount
    original_MiNAService = auth_module.MiNAService

    auth_module.MiAccount = MockMiAccount
    auth_module.MiNAService = MockMiNAService

    try:
        (
            ok,
            detail,
            meta,
        ) = await auth_manager._attempt_runtime_rebuild_with_existing_short_session(
            ctx="test_ctx", reason="test_reason"
        )

        assert ok is True, f"Expected success but got: {detail}"
        assert "existing_short_session_verified" in detail
        assert isinstance(auth_manager.mina_service, MockMiNAService)
        assert meta["auth_failure_detected"] is False
    finally:
        auth_module.MiAccount = original_MiAccount
        auth_module.MiNAService = original_MiNAService


@pytest.mark.asyncio
async def test_runtime_rebuild_with_existing_short_session_verify_failed(auth_manager):
    """测试用已有 short session 重建 runtime，但 verify 失败"""
    import json

    # 设置 auth.json 中有 short session
    auth_data = {
        "passToken": "test_pass",
        "userId": "test_user",
        "cUserId": "test_cuser",
        "psecurity": "test_psecurity",
        "serviceToken": "test_service_token",
        "ssecurity": "test_ssecurity",
        "deviceId": "test_device_id",
    }
    with open(auth_manager.auth_token_path, "w") as f:
        json.dump(auth_data, f)

    # 模拟 device_list 失败
    class MockMiAccount:
        def __init__(self, *args, **kwargs):
            self.token = {}

    class MockMiNAService:
        def __init__(self, account):
            self.account = account

        async def device_list(self):
            raise RuntimeError("Verify failed: 401")

    import xiaomusic.auth as auth_module

    original_MiAccount = auth_module.MiAccount
    original_MiNAService = auth_module.MiNAService

    auth_module.MiAccount = MockMiAccount
    auth_module.MiNAService = MockMiNAService

    try:
        (
            ok,
            detail,
            meta,
        ) = await auth_manager._attempt_runtime_rebuild_with_existing_short_session(
            ctx="test_ctx", reason="test_reason"
        )

        assert ok is False, f"Expected failure but got: {detail}"
        assert "verify failed" in detail.lower()
        # 验证没有清空 short session
        with open(auth_manager.auth_token_path) as f:
            saved_data = json.load(f)
        assert "serviceToken" in saved_data
        # 验证结构化元数据
        assert meta["auth_failure_detected"] is True
        assert meta["reason"] == "verify_failed"
    finally:
        auth_module.MiAccount = original_MiAccount
        auth_module.MiNAService = original_MiNAService


@pytest.mark.asyncio
async def test_runtime_rebuild_no_existing_short_session(auth_manager):
    """测试没有已有 short session 时，helper 直接失败"""
    import json

    # 设置 auth.json 中没有 short session
    auth_data = {
        "passToken": "test_pass",
        "userId": "test_user",
        "cUserId": "test_cuser",
        "psecurity": "test_psecurity",
        "ssecurity": "test_ssecurity",
        "deviceId": "test_device_id",
    }
    with open(auth_manager.auth_token_path, "w") as f:
        json.dump(auth_data, f)

    (
        ok,
        detail,
        meta,
    ) = await auth_manager._attempt_runtime_rebuild_with_existing_short_session(
        ctx="test_ctx", reason="test_reason"
    )

    assert ok is False
    assert "no_existing_short_session" in detail
    assert meta["auth_failure_detected"] is False


@pytest.mark.asyncio
async def test_non_destructive_recovery_prioritizes_existing_short_session(
    auth_manager,
):
    """测试非破坏性恢复优先尝试已有 short session"""
    import json

    # 设置 auth.json 中有 short session
    auth_data = {
        "passToken": "test_pass",
        "userId": "test_user",
        "cUserId": "test_cuser",
        "psecurity": "test_psecurity",
        "serviceToken": "test_service_token",
        "ssecurity": "test_ssecurity",
        "deviceId": "test_device_id",
    }
    with open(auth_manager.auth_token_path, "w") as f:
        json.dump(auth_data, f)

    # 模拟 device_list 成功
    class MockMiAccount:
        def __init__(self, *args, **kwargs):
            self.token = {}

    class MockMiNAService:
        def __init__(self, account):
            self.account = account

        async def device_list(self):
            return [{"did": "test_device"}]

    import xiaomusic.auth as auth_module

    original_MiAccount = auth_module.MiAccount
    original_MiNAService = auth_module.MiNAService

    auth_module.MiAccount = MockMiAccount
    auth_module.MiNAService = MockMiNAService

    try:
        (
            ok,
            detail,
            escalate,
        ) = await auth_manager._attempt_non_destructive_auth_recovery(
            ctx="test_ctx", reason="test_reason"
        )

        assert ok is True, f"Expected success but got: {detail}"
        assert (
            "existing_short_session_verified" in detail
            or "existing_short_session_runtime_rebuild" in detail
        )
        assert escalate is False
    finally:
        auth_module.MiAccount = original_MiAccount
        auth_module.MiNAService = original_MiNAService


def test_is_strong_short_session_invalidation_evidence():
    """测试强证据判定逻辑"""
    from xiaomusic.auth import AuthManager

    # 强证据场景：verify_failed + Login failed
    assert (
        AuthManager._is_strong_short_session_invalidation_evidence(
            reason="verify_failed", detail="Exception: Login failed"
        )
        is True
    )

    assert (
        AuthManager._is_strong_short_session_invalidation_evidence(
            reason="verify_failed", detail="401 unauthorized"
        )
        is True
    )

    assert (
        AuthManager._is_strong_short_session_invalidation_evidence(
            reason="verify_failed", detail="Error 70016"
        )
        is True
    )

    # 非强证据场景
    assert (
        AuthManager._is_strong_short_session_invalidation_evidence(
            reason="no_existing_short_session", detail="auth.json missing serviceToken"
        )
        is False
    )

    assert (
        AuthManager._is_strong_short_session_invalidation_evidence(
            reason="missing_userId", detail="auth.json missing userId"
        )
        is False
    )

    assert (
        AuthManager._is_strong_short_session_invalidation_evidence(
            reason="exception", detail="NetworkError: connection timeout"
        )
        is False
    )


@pytest.mark.asyncio
async def test_auth_call_escalates_on_strong_evidence(auth_manager):
    """Phase A verify_failed + Login failed 应直接升级到 clear+rebuild"""
    import json

    # 设置 auth.json 中有 short session
    auth_data = {
        "passToken": "test_pass",
        "userId": "test_user",
        "cUserId": "test_cuser",
        "psecurity": "test_psecurity",
        "serviceToken": "test_service_token",
        "ssecurity": "test_ssecurity",
        "deviceId": "test_device_id",
    }
    with open(auth_manager.auth_token_path, "w") as f:
        json.dump(auth_data, f)

    clear_calls = []
    ensure_calls = []
    original_clear = auth_manager._clear_short_lived_session

    def _track_clear(*args, **kwargs):
        clear_calls.append((args, kwargs))
        return original_clear(*args, **kwargs)

    async def _track_ensure(**kwargs):
        ensure_calls.append(kwargs)
        return True

    auth_manager._clear_short_lived_session = _track_clear
    auth_manager.ensure_logged_in = _track_ensure
    auth_manager.need_login = lambda: asyncio.sleep(0) or True

    # 模拟 Phase A 返回 verify_failed + Login failed（强证据）
    async def _mock_recovery(ctx="", reason=""):
        return False, "verify_failed:Exception: Login failed", True  # 强证据

    auth_manager._attempt_non_destructive_auth_recovery = _mock_recovery

    call_count = 0

    async def _failing_then_succeed():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("Login failed")
        return "success"

    result = await auth_manager.auth_call(
        _failing_then_succeed, retry=1, ctx="test_ctx"
    )
    assert result == "success"
    assert len(clear_calls) == 1, "Strong evidence should trigger clear"
    assert len(ensure_calls) == 1, "Strong evidence should call ensure_logged_in"
    assert ensure_calls[0].get("prefer_refresh") is True


@pytest.mark.asyncio
async def test_auth_call_no_escalate_on_weak_evidence(auth_manager):
    """非强证据不应升级到 clear+rebuild"""
    import json

    # 设置 auth.json 中有 short session
    auth_data = {
        "passToken": "test_pass",
        "userId": "test_user",
        "cUserId": "test_cuser",
        "psecurity": "test_psecurity",
        "serviceToken": "test_service_token",
        "ssecurity": "test_ssecurity",
        "deviceId": "test_device_id",
    }
    with open(auth_manager.auth_token_path, "w") as f:
        json.dump(auth_data, f)

    clear_calls = []
    original_clear = auth_manager._clear_short_lived_session

    def _track_clear(*args, **kwargs):
        clear_calls.append((args, kwargs))
        return original_clear(*args, **kwargs)

    auth_manager._clear_short_lived_session = _track_clear
    auth_manager.need_login = lambda: asyncio.sleep(0) or True

    # 模拟 Phase A 返回非强证据失败
    async def _mock_recovery(ctx="", reason=""):
        return False, "no_existing_short_session", False  # 非强证据

    auth_manager._attempt_non_destructive_auth_recovery = _mock_recovery

    async def _failing_fn():
        raise Exception("Login failed")

    with pytest.raises(Exception):
        await auth_manager.auth_call(_failing_fn, retry=0, ctx="test_ctx")

    assert len(clear_calls) == 0, "Weak evidence should NOT trigger clear"


def test_is_auth_failure_error():
    """测试 _is_auth_failure_error 函数"""
    from xiaomusic.auth import AuthManager

    # 认证失败场景
    assert AuthManager._is_auth_failure_error("Login failed") is True
    assert AuthManager._is_auth_failure_error("Error: Login failed") is True
    assert AuthManager._is_auth_failure_error("401 Unauthorized") is True
    assert AuthManager._is_auth_failure_error("HTTP 401") is True
    assert AuthManager._is_auth_failure_error("Error 70016") is True

    # 非认证失败场景
    assert AuthManager._is_auth_failure_error("Network timeout") is False
    assert AuthManager._is_auth_failure_error("Connection refused") is False
    assert AuthManager._is_auth_failure_error("") is False
    assert AuthManager._is_auth_failure_error(None) is False


def test_is_strong_evidence_with_structured_field():
    """测试强证据判定优先使用结构化字段"""
    from xiaomusic.auth import AuthManager

    # 结构化字段 auth_failure_detected=True
    assert (
        AuthManager._is_strong_short_session_invalidation_evidence(
            reason="verify_failed",
            detail="",  # detail 为空
            auth_failure_detected=True,
        )
        is True
    )

    # 结构化字段 auth_failure_detected=False
    assert (
        AuthManager._is_strong_short_session_invalidation_evidence(
            reason="verify_failed",
            detail="Login failed",  # detail 匹配但结构化字段明确为 False
            auth_failure_detected=False,
        )
        is False
    )

    # 无结构化字段时回退到 detail 匹配
    assert (
        AuthManager._is_strong_short_session_invalidation_evidence(
            reason="verify_failed",
            detail="Login failed",
            auth_failure_detected=None,
        )
        is True
    )

    # 非 verify_failed 不是强证据
    assert (
        AuthManager._is_strong_short_session_invalidation_evidence(
            reason="no_existing_short_session",
            detail="",
            auth_failure_detected=True,
        )
        is False
    )


@pytest.mark.asyncio
async def test_phase_a_returns_structured_meta(auth_manager):
    """测试 Phase A 返回结构化元数据"""
    import json

    # 设置 auth.json 中有 short session
    auth_data = {
        "passToken": "test_pass",
        "userId": "test_user",
        "cUserId": "test_cuser",
        "psecurity": "test_psecurity",
        "serviceToken": "test_service_token",
        "ssecurity": "test_ssecurity",
        "deviceId": "test_device_id",
    }
    with open(auth_manager.auth_token_path, "w") as f:
        json.dump(auth_data, f)

    # 模拟 device_list 失败并返回 Login failed
    class MockMiAccount:
        def __init__(self, *args, **kwargs):
            self.token = {}

    class MockMiNAService:
        def __init__(self, account):
            self.account = account

        async def device_list(self):
            raise RuntimeError(
                "Error https://api2.mina.mi.com/remote/ubus: Login failed"
            )

    import xiaomusic.auth as auth_module

    original_MiAccount = auth_module.MiAccount
    original_MiNAService = auth_module.MiNAService

    auth_module.MiAccount = MockMiAccount
    auth_module.MiNAService = MockMiNAService

    try:
        (
            ok,
            detail,
            meta,
        ) = await auth_manager._attempt_runtime_rebuild_with_existing_short_session(
            ctx="test_ctx", reason="test_reason"
        )

        assert ok is False
        assert "verify failed" in detail.lower()  # detail 包含 "verify failed"
        assert meta["reason"] == "verify_failed"
        assert (
            meta["auth_failure_detected"] is True
        )  # 关键：auth_failure_detected 应为 True
        assert meta["used_existing_short_session"] is True
        assert meta["runtime_objects_recreated"] is True
        assert "Login failed" in meta["verify_error_text"]
    finally:
        auth_module.MiAccount = original_MiAccount
        auth_module.MiNAService = original_MiNAService


@pytest.mark.asyncio
async def test_recovery_singleflight_only_one_leader(auth_manager):
    """并发 auth error 只有一个 leader 执行 clear+rebuild"""
    clear_calls = []
    original_clear = auth_manager._clear_short_lived_session

    def _track_clear(*args, **kwargs):
        clear_calls.append((args, kwargs))
        return original_clear(*args, **kwargs)

    auth_manager._clear_short_lived_session = _track_clear
    auth_manager.need_login = lambda: asyncio.sleep(0) or True
    auth_manager.ensure_logged_in = lambda **kw: asyncio.sleep(0) or None

    call_count = 0

    async def _failing_fn():
        nonlocal call_count
        call_count += 1
        raise Exception("Login failed")

    # 模拟并发的 auth error 请求
    tasks = [
        auth_manager.auth_call(_failing_fn, retry=0, ctx=f"concurrent_{i}")
        for i in range(5)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # 验证只有一次 clear（只有一个 leader）
    assert len(clear_calls) == 1, f"Expected 1 clear, got {len(clear_calls)}"


@pytest.mark.asyncio
async def test_recovery_singleflight_follower_does_not_clear(auth_manager):
    """follower 不会再次 clear"""
    clear_calls = []
    original_clear = auth_manager._clear_short_lived_session

    def _track_clear(*args, **kwargs):
        clear_calls.append((args, kwargs))
        return original_clear(*args, **kwargs)

    auth_manager._clear_short_lived_session = _track_clear
    auth_manager.need_login = lambda: asyncio.sleep(0) or True
    auth_manager.ensure_logged_in = lambda **kw: asyncio.sleep(0) or None

    # 手动设置 recovery_inflight 模拟已有 leader
    auth_manager._recovery_inflight = True
    auth_manager._recovery_leader_ctx = "existing_leader"

    async def _failing_fn():
        raise Exception("Login failed")

    # follower 请求
    with pytest.raises(Exception):
        await auth_manager.auth_call(_failing_fn, retry=0, ctx="follower")

    # 验证 follower 没有 clear
    assert len(clear_calls) == 0, "Follower should NOT clear"


@pytest.mark.asyncio
async def test_recovery_backoff_after_failure(auth_manager):
    """恢复失败后 backoff 生效"""
    import time

    # 直接设置 backoff 状态
    auth_manager._recovery_backoff_until_ts = time.time() + 10

    # 验证 backoff 生效
    assert auth_manager._is_recovery_backoff_active() is True

    async def _failing_fn():
        raise Exception("Login failed")

    # 验证后续请求被 backoff 拦截
    with pytest.raises(Exception):
        await auth_manager.auth_call(_failing_fn, retry=0, ctx="test_ctx")


def test_recovery_singleflight_state_management(auth_manager):
    """测试 singleflight 状态管理"""
    import asyncio

    # 初始状态
    assert auth_manager._recovery_inflight is False
    assert auth_manager._is_recovery_backoff_active() is False

    # 获取 leader
    is_leader, status = asyncio.get_event_loop().run_until_complete(
        auth_manager._try_acquire_recovery_leader(ctx="test")
    )
    assert is_leader is True
    assert status == "leader"
    assert auth_manager._recovery_inflight is True

    # 再次尝试获取，应该成为 follower
    is_leader2, status2 = asyncio.get_event_loop().run_until_complete(
        auth_manager._try_acquire_recovery_leader(ctx="test2")
    )
    assert is_leader2 is False
    assert status2 == "follower"

    # 释放 leader
    auth_manager._release_recovery_leader(ctx="test", result="ok")
    assert auth_manager._recovery_inflight is False

    # 现在可以再次获取 leader
    is_leader3, status3 = asyncio.get_event_loop().run_until_complete(
        auth_manager._try_acquire_recovery_leader(ctx="test3")
    )
    assert is_leader3 is True
    assert status3 == "leader"
