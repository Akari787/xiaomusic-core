import logging
import runpy
import sys
import types
from http.cookies import SimpleCookie
from pathlib import Path

import pytest


def test_qrcode_auth_response_errors_do_not_include_response_body():
    from types import SimpleNamespace

    from xiaomusic.qrcode_login import MiJiaAPI

    api = object.__new__(MiJiaAPI)
    secret = "pass-token-response-secret"
    response = SimpleNamespace(
        status_code=200,
        text=f'&&&START&&&{{"code":70016,"desc":"{secret}"}}',
    )

    with pytest.raises(ValueError) as exc_info:
        api._handle_ret(response)

    assert secret not in str(exc_info.value)
    assert "70016" in str(exc_info.value)


@pytest.mark.asyncio
async def test_miservice_debug_never_logs_sensitive_headers_or_body(monkeypatch, caplog):
    class _Account:
        async def _securityTokenService(self, *args):  # pragma: no cover
            raise AssertionError("patched method expected")

    fake_miservice = types.ModuleType("miservice")
    fake_miservice.MiAccount = _Account
    monkeypatch.setitem(sys.modules, "miservice", fake_miservice)
    monkeypatch.setenv("XIAOMUSIC_ENABLE_MISERVICE_DEBUG", "true")

    class _Response:
        status = 200
        headers = {
            "Set-Cookie": "passToken=pass-secret; Path=/",
            "Location": "https://account.xiaomi.com/callback?ticket=url-secret",
        }

        def __init__(self):
            self.cookies = SimpleCookie(
                "serviceToken=service-secret; passToken=pass-secret"
            )

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def text(self):
            return "body-secret"

    class _Session:
        def get(self, url):
            return _Response()

    monkeypatch.setattr(_Account, "session", _Session(), raising=False)
    caplog.set_level(logging.INFO, logger="miservice")
    runpy.run_path(str(Path(__file__).resolve().parents[1] / "sitecustomize.py"))

    account = _Account()
    assert await account._securityTokenService(
        "https://account.xiaomi.com/sts?ticket=url-secret", "nonce-secret", "ssecurity-secret"
    ) == "service-secret"
    logged = caplog.text
    for secret in (
        "pass-secret", "service-secret", "url-secret", "body-secret",
        "nonce-secret", "ssecurity-secret",
    ):
        assert secret not in logged
    assert "passToken" in logged


@pytest.mark.asyncio
async def test_cookie_observation_logs_no_token_fingerprint_or_length(monkeypatch, caplog):
    import hashlib
    from types import SimpleNamespace

    secret = "synthetic-private-pass-token"

    class Session:
        async def _request(self, *args, **kwargs):
            return SimpleNamespace(
                cookies=SimpleCookie(f"passToken={secret}"), status=200,
                url=SimpleNamespace(host="account.xiaomi.com"),
            )

    fake = types.ModuleType("aiohttp")
    fake.ClientSession = Session
    monkeypatch.setitem(sys.modules, "aiohttp", fake)
    monkeypatch.setenv("XIAOMUSIC_ENABLE_MICOOKIE_OBSERVE", "true")
    monkeypatch.delenv("XIAOMUSIC_ENABLE_MISERVICE_DEBUG", raising=False)
    caplog.set_level(logging.INFO, logger="xiaomusic.cookie_obs")
    runpy.run_path(str(Path(__file__).resolve().parents[1] / "sitecustomize.py"))
    await Session()._request("GET", "https://account.xiaomi.com/?private=value")
    text = caplog.text
    assert "host_class=account" in text
    assert "rotated=False" in text
    assert secret not in text
    assert hashlib.sha256(secret.encode()).hexdigest()[:8] not in text
    assert "fp=" not in text and "len=" not in text
    assert "private=value" not in text
