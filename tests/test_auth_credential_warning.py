import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xiaomusic.auth import SimpleAuthManager
from xiaomusic.qrcode_login import MiJiaAPI
from xiaomusic.security.token_store import TokenStore


@pytest.fixture
def manager(tmp_path):
    data = {"userId": "u", "passToken": "p", "deviceId": "d", "ssecurity": "s", "serviceToken": "short"}
    store = SimpleNamespace(get=lambda: dict(data), get_persisted=lambda: dict(data))
    config = SimpleNamespace(conf_path=str(tmp_path), auth_token_path=str(tmp_path / "auth.json"))
    with patch("xiaomusic.auth.ClientSession") as session:
        session.return_value.cookie_jar = MagicMock()
        auth = SimpleAuthManager(config, logging.getLogger(__name__), None, store)
    auth.mina_service = object()
    auth.login_signature = auth._get_login_signature()
    return auth, data


def test_legacy_age_unknown_and_60h_boundary(manager, monkeypatch):
    auth, data = manager
    now = 2_000_000_000
    monkeypatch.setattr("xiaomusic.auth.time.time", lambda: now)
    assert auth.map_auth_public_status(runtime_auth_ready=True)["credential_warning"] == "unknown_age"
    data["saveTime"] = int((now - 300_000) * 1000)
    assert auth.map_auth_public_status(runtime_auth_ready=True)["pass_token_age_seconds"] is None
    data["passTokenIssuedAt"] = int((now - 60 * 3600) * 1000) + 1000
    assert auth.map_auth_public_status(runtime_auth_ready=True)["credential_warning"] == ""
    data["passTokenIssuedAt"] -= 1000
    status = auth.map_auth_public_status(runtime_auth_ready=True)
    assert status["pass_token_age_seconds"] == 60 * 3600
    assert status["credential_warning"] == ""
    data["passTokenIssuedAt"] = int((now - (15 * 24 + 20) * 3600) * 1000)
    assert auth.map_auth_public_status(runtime_auth_ready=True)["credential_warning"] == ""
    for invalid in (float("nan"), "123", -1, int((now + 1) * 1000)):
        data["passTokenIssuedAt"] = invalid
        assert auth.map_auth_public_status(runtime_auth_ready=True)["credential_warning"] == "unknown_age"
    data["passTokenIssuedAt"] = int((now - 60 * 3600) * 1000)
    monkeypatch.setenv("AUTH_REFRESH_TOKEN", "different")
    assert auth.map_auth_public_status(runtime_auth_ready=True)["pass_token_issued_at"] is None


@pytest.mark.parametrize("sequence,recoveries", [([False, True] * 3, 0), ([False, False, False, True, True], 1)])
def test_keepalive_consecutive_failures(manager, sequence, recoveries):
    auth, _ = manager
    for ok in sequence:
        before = auth._health_probe_sequence
        auth._health_probe_sequence += 1
        auth._last_health_probe_result = "ok" if ok else "network_error"
        auth._record_keepalive_round(ok=ok, gate="none", probe_sequence=before)
        if ok:
            assert auth._keepalive_fail_streak == 0
    assert auth._keepalive_recovery_total == recoveries
    assert auth._keepalive_failure_total == 3
    assert auth._keepalive_probe_failure_total == 3
    assert auth._keepalive_degraded is False


@pytest.mark.parametrize("gate", ["cooldown", "manual_login_required", "locked"])
def test_keepalive_gate_does_not_reuse_previous_probe(manager, gate):
    auth, _ = manager
    auth._last_health_probe_result = "network_error"
    auth._record_keepalive_round(ok=False, gate=gate, probe_sequence=auth._health_probe_sequence)
    diagnostic = auth._keepalive_diagnostic
    assert diagnostic["probe_attempted"] is False
    assert diagnostic["probe_result"] == "not_attempted"
    assert diagnostic["probe_failure_total"] == 0
    assert diagnostic["gate_skip_total"] == 1


@pytest.mark.asyncio
async def test_health_probe_error_does_not_expose_secret(manager, caplog):
    auth, _ = manager
    secret = "https://example.test/?serviceToken=synthetic-private-cookie"
    auth._state = auth.STATE_HEALTHY
    auth.mina_service = SimpleNamespace(device_list=AsyncMock(side_effect=TimeoutError(secret)))
    assert await auth.ensure_auth() is False
    auth._record_keepalive_round(ok=False, gate="none", probe_sequence=0)
    assert auth._last_health_probe_error == "network_error"
    assert secret not in str(auth._keepalive_diagnostic)
    assert "synthetic-private-cookie" not in caplog.text
    assert "synthetic-private-cookie" not in auth._last_error


@pytest.mark.asyncio
async def test_verified_recovery_clears_cooldown_and_leader_backoff(manager):
    auth, _ = manager
    auth._cooldown_until = 9999999999
    auth._recovery_backoff_until_ts = 9999999999
    auth._mark_verified_runtime_recovered()
    assert auth._cooldown_until == 0
    assert auth._recovery_backoff_until_ts == 0
    auth._recovery_backoff_until_ts = 9999999999
    auth._recovery_inflight = True
    await auth._release_recovery_leader(result="ok")
    assert auth._recovery_backoff_until_ts == 0
    assert auth._recovery_inflight is False


def test_deleted_canonical_pass_token_is_not_an_exchange_capability(manager):
    auth, data = manager
    data["passToken"] = " DeLeTeD "
    assert auth._get_auth_data() == {}
    assert auth._has_persistent_auth_fields(data) is False


def test_suspension_degraded_despite_ready_runtime_and_scan_resets(manager):
    auth, data = manager
    auth._scheduled_refresh_suspended = True
    auth._scheduled_refresh_suspend_code = "interactive_captcha_challenge"
    status = auth.map_auth_public_status(runtime_auth_ready=True)
    assert status["status"] == "degraded"
    assert status["status_reason"] == "persistent_auth_refresh_suspended"
    assert status["runtime_auth_ready"] is True
    assert status["credential_warning"] == "refresh_suspended"
    auth._mark_verified_runtime_recovered()
    assert auth.map_auth_public_status(runtime_auth_ready=True)["credential_warning"] == "refresh_suspended"


@pytest.mark.parametrize("entry", ["get_logint_status", "qr_login"])
@pytest.mark.parametrize("invalid", ["", " DeLeTeD "])
def test_real_qr_result_persists_issuance_but_invalid_candidate_does_not(entry, invalid, monkeypatch):
    api = object.__new__(MiJiaAPI)
    api.auth_data = {"ua": "saved-ua", "passToken": "old", "passTokenIssuedAt": 111}
    api.login_url = "https://account.example/login"
    api._get_location = lambda: {"_sign": "sign"}
    api._print_qr = lambda url: None
    api._init_session = lambda: None
    saved = []
    api._save_auth_data = lambda: saved.append(dict(api.auth_data))
    fake_session = SimpleNamespace(cookies=SimpleNamespace(get_dict=lambda: {}))
    monkeypatch.setattr("xiaomusic.qrcode_login.requests.Session", lambda: fake_session)
    monkeypatch.setattr("xiaomusic.qrcode_login.time.time", lambda: 500.0)
    api._http_request = lambda *args, **kwargs: SimpleNamespace(cookies={})
    result = {
        "psecurity": "ps", "nonce": "n", "ssecurity": "ss",
        "passToken": "new", "userId": "u", "cUserId": "cu",
        "location": "https://account.example/cb?nonce=n",
    }
    responses = iter(([{"loginUrl": "https://account.example/qr", "qr": "qr", "lp": "lp"}] if entry == "qr_login" else []) + [result])
    api._handle_ret = lambda *args, **kwargs: next(responses)
    getattr(api, entry)("status-url") if entry == "get_logint_status" else api.qr_login()
    assert saved[0]["passTokenIssuedAt"] == 500_000
    assert saved[0]["passToken"] == "new"

    api.auth_data["passTokenIssuedAt"] = 111
    saved.clear()
    result["passToken"] = invalid
    responses = iter(([{"loginUrl": "qr", "qr": "qr", "lp": "lp"}] if entry == "qr_login" else []) + [result])
    with pytest.raises(ValueError, match="invalid_pass_token_candidate"):
        getattr(api, entry)("status-url") if entry == "get_logint_status" else api.qr_login()
    assert saved == []


@pytest.mark.parametrize("entry", ["get_logint_status", "qr_login"])
@pytest.mark.parametrize("token_value", ["new", "old"])
def test_real_qr_result_commits_new_timestamp_with_cas(entry, token_value, tmp_path, monkeypatch):
    store = TokenStore(tmp_path / "auth.json")
    original = {
        "ua": "saved-ua", "userId": "u", "deviceId": "d", "passToken": "old",
        "passTokenIssuedAt": 111, "serviceToken": "old-short",
    }
    store.save(original)
    api = object.__new__(MiJiaAPI)
    api.token_store = store
    api._initial_token_snapshot = store.get_persisted()
    api.auth_data = dict(original)
    api.login_url = "https://account.example/login"
    api._get_location = lambda: {"_sign": "sign"}
    api._print_qr = lambda url: None
    api._init_session = lambda: None
    fake_session = SimpleNamespace(cookies=SimpleNamespace(get_dict=lambda: {}))
    monkeypatch.setattr("xiaomusic.qrcode_login.requests.Session", lambda: fake_session)
    monkeypatch.setattr("xiaomusic.qrcode_login.time.time", lambda: 500.0)
    api._http_request = lambda *args, **kwargs: SimpleNamespace(cookies={})
    lp_result = {
        "psecurity": "ps", "nonce": "n", "ssecurity": "ss",
        "passToken": token_value, "userId": "u", "cUserId": "cu",
        "location": "https://account.example/cb?nonce=n",
    }
    qr_result = {"loginUrl": "https://account.example/qr", "qr": "qr", "lp": "lp"}
    responses = iter(([qr_result] if entry == "qr_login" else []) + [lp_result])
    api._handle_ret = lambda *args, **kwargs: next(responses)
    getattr(api, entry)("status-url") if entry == "get_logint_status" else api.qr_login()
    assert store.get_persisted()["passTokenIssuedAt"] == 500_000
    assert store.get_persisted()["passToken"] == token_value
    assert store.path.exists()
    assert store.get_persisted()["saveTime"] == 500_000

    # A deletion cookie after the QR result cannot commit a new age or token.
    fake_session.cookies.get_dict = lambda: {"passToken": " DELETED "}
    responses = iter(([qr_result] if entry == "qr_login" else []) + [lp_result])
    with pytest.raises(ValueError, match="invalid_pass_token_candidate"):
        getattr(api, entry)("status-url") if entry == "get_logint_status" else api.qr_login()
    assert store.get_persisted()["passTokenIssuedAt"] == 500_000
    assert store.get_persisted()["passToken"] == token_value


def test_qr_login_no_scan_refresh_keeps_original_issuance():
    api = object.__new__(MiJiaAPI)
    api.auth_data = {"passToken": "old", "passTokenIssuedAt": 111}
    api._get_location = lambda: {"code": 0, "message": "刷新Token成功"}
    api._init_session = lambda: None
    saved = []
    api._save_auth_data = lambda: saved.append(dict(api.auth_data))
    api.qr_login()
    assert saved[0]["passTokenIssuedAt"] == 111


def test_qr_issuance_and_short_refresh_preserves_age(monkeypatch):
    api = object.__new__(MiJiaAPI)
    api.auth_data = {"passToken": "old", "passTokenIssuedAt": 111}
    monkeypatch.setattr("xiaomusic.qrcode_login.time.time", lambda: 500.0)
    api._record_pass_token_issuance("old")
    assert api.auth_data["passTokenIssuedAt"] == 111
    api.auth_data["passToken"] = "new"
    api._record_pass_token_issuance("old")
    assert api.auth_data["passTokenIssuedAt"] == 500_000
    api.auth_data["passTokenIssuedAt"] = 111
    api._record_pass_token_issuance("new", scanned=True)
    assert api.auth_data["passTokenIssuedAt"] == 500_000
    for invalid in (" ", " DeLeTeD "):
        api.auth_data["passToken"] = invalid
        with pytest.raises(ValueError, match="invalid_pass_token_candidate"):
            api._record_pass_token_issuance("new", scanned=True)
