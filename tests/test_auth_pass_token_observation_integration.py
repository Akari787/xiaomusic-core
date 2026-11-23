import os
import subprocess
import sys
from pathlib import Path

_CHILD = r'''
import asyncio
import logging
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch
from aiohttp.cookiejar import CookieJar
from http.cookies import SimpleCookie
from yarl import URL
import xiaomusic.auth as auth_module
from xiaomusic.auth import SimpleAuthManager

secret = "synthetic-cookie-secret"
original_pass = "synthetic-old-pass"
location = __import__("os").environ["SYNTHETIC_LOCATION"]
json_echo = __import__("os").environ.get("SYNTHETIC_JSON_ECHO") == "1"
service_cookie = __import__("os").environ.get("SYNTHETIC_SERVICE_COOKIE") == "1"
service_cookie_equals_json = __import__("os").environ.get("SYNTHETIC_SERVICE_COOKIE_EQUALS_JSON") == "1"
path_cookie = __import__("os").environ.get("SYNTHETIC_PATH_COOKIE") == "1"
service_cookie_value = original_pass if service_cookie_equals_json else "synthetic-service-host-pass"
observation_error = __import__("os").environ.get("SYNTHETIC_OBSERVATION_ERROR") == "1"
domain_cookie = __import__("os").environ.get("SYNTHETIC_DOMAIN_COOKIE") == "1"
stream = StringIO()
handler = logging.StreamHandler(stream)
logger = logging.getLogger("pass-token-observation-test")
logger.handlers[:] = [handler]
logger.setLevel(logging.INFO)
logger.propagate = False

async def service_login(*args):
    response = {"code": 0, "location": location, "nonce": "synthetic-nonce", "ssecurity": "synthetic-ssecurity"}
    if json_echo:
        response["passToken"] = original_pass
    return response
async def sts(*args): return "synthetic-service-token"
class LoginSession:
    async def close(self): pass

manager = object.__new__(SimpleAuthManager)
manager.log = logger
manager.set_token = lambda *args, **kwargs: None

async def main():
    jar = CookieJar()
    jar.update_cookies({"passToken": secret}, response_url=URL("https://account.xiaomi.com/"))
    if service_cookie:
        jar.update_cookies({"passToken": service_cookie_value}, response_url=URL(location))
    if domain_cookie:
        domain_cookie_jar = SimpleCookie()
        domain_cookie_jar.load("passToken=synthetic-domain-pass; Domain=.mi.com; Path=/")
        jar.update_cookies(domain_cookie_jar, response_url=URL("https://api2.mina.mi.com/"))
    if path_cookie:
        path_cookie_jar = SimpleCookie()
        path_cookie_jar.load("passToken=synthetic-path-pass; Path=/sts")
        jar.update_cookies(
            path_cookie_jar,
            response_url=URL("https://api2.mina.mi.com/sts"),
        )
    if observation_error:
        def fail_on_redirect(url):
            if str(url) == location:
                raise ValueError("synthetic observation failure")
            return original_filter_cookies(url)
        original_filter_cookies = jar.filter_cookies
        jar.filter_cookies = fail_on_redirect
    account = SimpleNamespace(token={}, session=SimpleNamespace(cookie_jar=jar))
    account._serviceLogin = service_login
    account._securityTokenService = sts
    manager._new_isolated_mi_account = lambda *args, **kwargs: account
    with patch.object(auth_module, "ClientSession", LoginSession):
        result = await manager._try_miaccount_persistent_auth_relogin(
            before={"userId": "synthetic-user", "passToken": original_pass, "deviceId": "d"}
        )
    assert result["ok"] is True
    is_account_redirect = "account.xiaomi.com" in location
    expected_pass = secret if is_account_redirect and not json_echo else original_pass
    assert result["auth_data"]["passToken"] == expected_pass
    assert jar.filter_cookies(URL("https://account.xiaomi.com/")).get("passToken").value == secret
    text = stream.getvalue()
    assert f"json_present={json_echo}" in text
    assert "account_jar_present=True" in text
    assert "account_cookie_differs=True" in text
    redirect_cookie_visible = service_cookie or is_account_redirect or domain_cookie
    assert f"redirect_cookie_present={redirect_cookie_visible}" in text
    assert f"redirect_cookie_differs={(service_cookie and not service_cookie_equals_json) or is_account_redirect or domain_cookie}" in text
    assert f"redirect_cookie_equals_json={service_cookie_equals_json and json_echo}" in text
    assert f"redirect_cookie_observation_error={observation_error}" in text
    assert f"json_cookie_same={'False' if json_echo else 'None'}" in text
    assert f"redirect_host_class={'account_domain' if is_account_redirect else 'service_domain'}" in text
    assert f"candidate_differs={str(is_account_redirect and not json_echo)}" in text
    expected_source = "service_login_json" if json_echo else ("account_domain_cookie" if is_account_redirect else "none")
    assert f"selected_source={expected_source}" in text
    for sensitive in (
        secret, original_pass, "synthetic-user", location, "synthetic-nonce",
        "synthetic-ssecurity", "synthetic-service-token",
        "synthetic-service-host-pass", "synthetic-domain-pass", "synthetic-path-pass",
    ):
        assert sensitive not in text

asyncio.run(main())
'''


def _run_child(
    location: str, *, json_echo: bool = False, service_cookie: bool = False,
    observation_error: bool = False, domain_cookie: bool = False,
    service_cookie_equals_json: bool = False, path_cookie: bool = False,
) -> None:
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root)
    env["SYNTHETIC_LOCATION"] = location
    env["SYNTHETIC_JSON_ECHO"] = "1" if json_echo else "0"
    env["SYNTHETIC_SERVICE_COOKIE"] = "1" if service_cookie else "0"
    env["SYNTHETIC_SERVICE_COOKIE_EQUALS_JSON"] = "1" if service_cookie_equals_json else "0"
    env["SYNTHETIC_PATH_COOKIE"] = "1" if path_cookie else "0"
    env["SYNTHETIC_OBSERVATION_ERROR"] = "1" if observation_error else "0"
    env["SYNTHETIC_DOMAIN_COOKIE"] = "1" if domain_cookie else "0"
    result = subprocess.run(
        [sys.executable, "-c", _CHILD], cwd=root, env=env,
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_real_cookiejar_service_domain_redirect_is_observed_without_secrets():
    _run_child("https://api2.mina.mi.com/callback?nonce=synthetic-nonce")


def test_real_cookiejar_account_domain_redirect_selects_cookie_without_secrets():
    _run_child("https://account.xiaomi.com/callback?nonce=synthetic-nonce")


def test_real_cookiejar_json_echo_masks_different_account_cookie_without_secrets():
    _run_child("https://api2.mina.mi.com/callback?nonce=synthetic-nonce", json_echo=True)


def test_service_host_cookie_is_observed_but_never_selected_over_json_or_account_scope():
    _run_child(
        "https://api2.mina.mi.com/callback?nonce=synthetic-nonce",
        json_echo=True, service_cookie=True,
    )


def test_missing_json_and_service_domain_cookie_does_not_select_service_cookie():
    _run_child(
        "https://api2.mina.mi.com/callback?nonce=synthetic-nonce",
        service_cookie=True,
    )


def test_redirect_cookie_observation_error_is_boolean_and_redacted():
    _run_child(
        "https://api2.mina.mi.com/callback?nonce=synthetic-nonce",
        observation_error=True,
    )


def test_domain_cookie_boundary_is_observed_at_matching_subdomain():
    _run_child(
        "https://api2.mina.mi.com/callback?nonce=synthetic-nonce",
        domain_cookie=True,
    )


def test_service_cookie_equal_to_json_is_reported_as_equal():
    _run_child(
        "https://api2.mina.mi.com/callback?nonce=synthetic-nonce",
        json_echo=True, service_cookie=True, service_cookie_equals_json=True,
    )


def test_path_sts_cookie_is_not_visible_on_callback_path():
    _run_child(
        "https://api2.mina.mi.com/callback?nonce=synthetic-nonce",
        path_cookie=True,
    )
