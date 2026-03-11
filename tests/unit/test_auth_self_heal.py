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
        self.oauth2_token_path = str(base / "auth.json")
        self.mi_did = "981257654"
        self.devices = {}
        self.oauth2_refresh_interval_hours = 12
        self.oauth2_refresh_min_interval_minutes = 30
        self.mina_high_freq_min_interval_seconds = 8
        self.mina_auth_fail_threshold = 3
        self.mina_auth_cooldown_seconds = 600

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

    async def _ensure(force=False, reason="", prefer_refresh=False):  # noqa: ARG001
        calls["ensure"] += 1
        assert prefer_refresh is True
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
    relogin_calls = {"rebuild": 0, "refresh": 0}

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

    auth_manager.need_login = _need_login
    auth_manager.refresh_oauth2_token_if_needed = _refresh
    auth_manager.rebuild_services = _rebuild
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
    assert relogin_calls["refresh"] == 1
    assert relogin_calls["rebuild"] == 1


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
    token_path = Path(auth_manager.oauth2_token_path)
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
    token_path = Path(auth_manager.oauth2_token_path)
    before = json.loads(token_path.read_text(encoding="utf-8"))
    changed = auth_manager._clear_short_lived_session(
        clear_reason="keepalive",
        err="network timeout",
    )
    assert changed is False
    after = json.loads(token_path.read_text(encoding="utf-8"))
    assert after == before


@pytest.mark.asyncio
async def test_auth_call_triggers_short_session_clear_before_relogin(auth_manager, monkeypatch):
    called = {"clear": 0, "ensure": 0}

    def _clear(clear_reason: str, err=None):  # noqa: ARG001
        called["clear"] += 1
        return True

    async def _ensure(force=False, reason="", prefer_refresh=False):  # noqa: ARG001
        called["ensure"] += 1
        return True

    monkeypatch.setattr(auth_manager, "_clear_short_lived_session", _clear)
    auth_manager.ensure_logged_in = _ensure

    state = {"n": 0}

    async def _fn():
        if state["n"] == 0:
            state["n"] += 1
            raise RuntimeError("401 unauthorized")
        return "ok"

    out = await auth_manager.auth_call(_fn, retry=1, ctx="mina:player_get_status")
    assert out == "ok"
    assert called["clear"] == 1
    assert called["ensure"] == 1


@pytest.mark.asyncio
async def test_ensure_logged_in_refresh_failed_path_clears_short_session(auth_manager, monkeypatch):
    events = []

    async def _need_login():
        return True

    async def _refresh(reason, force=False):  # noqa: ARG001
        events.append("refresh")
        return {
            "refreshed": False,
            "token_saved": False,
            "last_error": "refresh failed",
            "fallback_allowed": True,
        }

    async def _rebuild(reason, allow_login_fallback=False):  # noqa: ARG001
        events.append(f"rebuild:{allow_login_fallback}")
        return True

    def _clear(clear_reason: str, err=None):  # noqa: ARG001
        events.append("clear")
        return True

    auth_manager.need_login = _need_login
    auth_manager.refresh_oauth2_token_if_needed = _refresh
    auth_manager.rebuild_services = _rebuild
    monkeypatch.setattr(auth_manager, "_clear_short_lived_session", _clear)

    out = await auth_manager.ensure_logged_in(force=True, reason="ut-refresh-failed", prefer_refresh=True)
    assert out is True
    assert events == ["refresh", "clear", "rebuild:True"]


def test_persist_rebuild_writes_short_tokens_again(auth_manager):
    from xiaomusic.security.token_store import TokenStore

    auth_manager.token_store = TokenStore(auth_manager.config, _DummyLog())
    auth_manager.token_store.reload_from_disk()
    auth_data = auth_manager._get_oauth2_auth_data()
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

    auth_manager._persist_oauth2_token(auth_manager._get_oauth2_auth_data(), _Account(), reason="ut-rebuild")
    data = auth_manager._get_oauth2_auth_data()
    assert data.get("serviceToken") == "new-short-token"
    assert data.get("yetAnotherServiceToken") == "new-short-token"


@pytest.mark.asyncio
async def test_ensure_logged_in_prefers_refresh_then_rebuild(auth_manager):
    events = []

    async def _need_login():
        return True

    async def _refresh(reason, force=False):  # noqa: ARG001
        events.append("refresh")
        return {
            "refreshed": True,
            "token_saved": True,
            "last_error": None,
            "fallback_allowed": False,
        }

    async def _rebuild(reason, allow_login_fallback=False):  # noqa: ARG001
        events.append(f"rebuild:{allow_login_fallback}")
        return True

    auth_manager.need_login = _need_login
    auth_manager.refresh_oauth2_token_if_needed = _refresh
    auth_manager.rebuild_services = _rebuild

    out = await auth_manager.ensure_logged_in(force=True, reason="ut-auth", prefer_refresh=True)
    assert out is True
    assert events == ["refresh", "rebuild:False"]


@pytest.mark.asyncio
async def test_ensure_logged_in_fallback_login_only_when_refresh_failed(auth_manager):
    events = []

    async def _need_login():
        return True

    async def _refresh(reason, force=False):  # noqa: ARG001
        events.append("refresh")
        return {
            "refreshed": False,
            "token_saved": False,
            "last_error": "刷新Token失败，请重新登录",
            "fallback_allowed": True,
        }

    async def _rebuild(reason, allow_login_fallback=False):  # noqa: ARG001
        events.append(f"rebuild:{allow_login_fallback}")
        return True

    auth_manager.need_login = _need_login
    auth_manager.refresh_oauth2_token_if_needed = _refresh
    auth_manager.rebuild_services = _rebuild

    out = await auth_manager.ensure_logged_in(force=True, reason="ut-fallback", prefer_refresh=True)
    assert out is True
    assert events == ["refresh", "rebuild:True"]


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
    auth_manager.config.oauth2_refresh_interval_hours = 0.01
    auth_manager.config.oauth2_refresh_min_interval_minutes = 1
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
    auth_manager.refresh_oauth2_token_if_needed = _refresh
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
        "login_at",
        "expires_at",
        "ttl_remaining_seconds",
        "last_refresh_trigger",
        "last_auth_error",
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
async def test_login_exchange_stage_success_after_clear(auth_manager):
    token_path = Path(auth_manager.oauth2_token_path)
    data = json.loads(token_path.read_text(encoding="utf-8"))
    data.pop("serviceToken", None)
    data.pop("yetAnotherServiceToken", None)
    token_path.write_text(json.dumps(data), encoding="utf-8")

    auth_manager._clear_short_lived_session(
        clear_reason="mina:player_get_status",
        err="401 unauthorized",
    )
    await auth_manager.login_miboy(allow_login_fallback=True, reason="mina:player_get_status")
    state = auth_manager.auth_recovery_debug_state()
    login_stage = state["last_login_exchange"]
    assert login_stage["stage"] == "login_exchange"
    assert login_stage["result"] in {"ok", "skipped"}
    assert login_stage["provider"] == "micoapi"


@pytest.mark.asyncio
async def test_login_exchange_stage_failed_records_reason(auth_manager, monkeypatch):
    from xiaomusic import auth as auth_module

    class _FailAccount(auth_module.MiAccount):
        async def login(self, *args, **kwargs):  # noqa: ARG002
            raise RuntimeError("70016 登录验证失败")

    monkeypatch.setattr(auth_module, "MiAccount", _FailAccount)
    token_path = Path(auth_manager.oauth2_token_path)
    data = json.loads(token_path.read_text(encoding="utf-8"))
    data.pop("serviceToken", None)
    data.pop("yetAnotherServiceToken", None)
    token_path.write_text(json.dumps(data), encoding="utf-8")

    auth_manager._clear_short_lived_session(
        clear_reason="mina:player_get_status",
        err="401 unauthorized",
    )
    with pytest.raises(RuntimeError):
        await auth_manager.login_miboy(allow_login_fallback=True, reason="mina:player_get_status")
    state = auth_manager.auth_recovery_debug_state()
    login_stage = state["last_login_exchange"]
    assert login_stage["stage"] == "login_exchange"
    assert login_stage["result"] == "failed"
    assert login_stage["error_code"] in {"70016", "refresh_failed"}


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

    out = await auth_manager.rebuild_services(reason="mina:player_get_status", allow_login_fallback=True)
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
    token_path = Path(auth_manager.oauth2_token_path)
    data = json.loads(token_path.read_text(encoding="utf-8"))
    data.pop("serviceToken", None)
    data.pop("yetAnotherServiceToken", None)
    token_path.write_text(json.dumps(data), encoding="utf-8")

    await auth_manager.login_miboy(allow_login_fallback=True, reason="ut-mi-trace")
    trace = auth_manager.miaccount_login_trace_debug_state()["login_input_snapshot"]
    assert trace["event"] == "miaccount_login_trace"
    assert trace["stage"] == "login_input_snapshot"
    assert trace["sid"] == "micoapi"
    assert trace["token_dict_is_none"] is False
    assert "keep-pass" not in json.dumps(trace, ensure_ascii=False)


@pytest.mark.asyncio
async def test_mi_login_70016_records_http_exchange_and_parse_failed(auth_manager, monkeypatch):
    from xiaomusic import auth as auth_module

    class _FailAccount(auth_module.MiAccount):
        async def login(self, *args, **kwargs):  # noqa: ARG002
            raise RuntimeError("code: 70016, description: 登录验证失败")

    monkeypatch.setattr(auth_module, "MiAccount", _FailAccount)

    token_path = Path(auth_manager.oauth2_token_path)
    data = json.loads(token_path.read_text(encoding="utf-8"))
    data.pop("serviceToken", None)
    data.pop("yetAnotherServiceToken", None)
    token_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(RuntimeError):
        await auth_manager.login_miboy(allow_login_fallback=True, reason="ut-mi-70016")
    trace = auth_manager.miaccount_login_trace_debug_state()
    assert trace["login_http_exchange"]["result"] == "failed"
    assert trace["login_http_exchange"]["resp_code"] == "70016"
    assert trace["post_login_runtime_seed"]["result"] == "failed"


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

    auth_manager._persist_oauth2_token(auth_manager._get_oauth2_auth_data(), _Account(), reason="ut-writeback")
    stage = auth_manager.miaccount_login_trace_debug_state()["token_writeback"]
    assert stage["result"] == "ok"
    assert stage["wrote_serviceToken"] is True
    assert stage["wrote_target"] == "both"


@pytest.mark.asyncio
async def test_post_login_runtime_seed_source_recorded(auth_manager):
    token_path = Path(auth_manager.oauth2_token_path)
    data = json.loads(token_path.read_text(encoding="utf-8"))
    data.pop("serviceToken", None)
    data.pop("yetAnotherServiceToken", None)
    token_path.write_text(json.dumps(data), encoding="utf-8")

    await auth_manager.login_miboy(allow_login_fallback=True, reason="ut-runtime-seed")
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
