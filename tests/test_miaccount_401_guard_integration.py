import os
import subprocess
import sys
from pathlib import Path


def test_installed_miaccount_401_does_not_enter_implicit_login():
    root = Path(__file__).resolve().parents[1]
    script = r'''
import asyncio
import tempfile
from pathlib import Path
from xiaomusic.auth import SimpleAuthManager
from xiaomusic.security.token_store import TokenStore

class Session:
    def __init__(self): self.requests = 0
    def request(self, *args, **kwargs): self.requests += 1; raise AssertionError("unexpected HTTP")

async def main():
    manager = object.__new__(SimpleAuthManager)
    canonical = {"userId": "u", "passToken": "old-pass", "deviceId": "d"}
    with tempfile.TemporaryDirectory() as tmp:
        store = TokenStore(Path(tmp) / "auth.json")
        store.save(canonical)
        session = Session()
        account = manager._new_isolated_mi_account(session, "u", token=canonical)
        assert "ssecurity" not in account.token
        assert "micoapi" not in account.token
        login_calls = []
        async def service_login(*args): login_calls.append("serviceLogin")
        async def service_login_auth2(*args): login_calls.append("serviceLoginAuth2")
        account._serviceLogin = service_login
        account._serviceLoginAuth2 = service_login_auth2
        try:
            # No micoapi sid: real mi_request enters login() before HTTP.
            await account.mi_request("micoapi", "https://api.example/device", {}, {})
        except RuntimeError as exc:
            assert str(exc) == "implicit_miaccount_login_disabled"
        else:
            raise AssertionError("missing sid must fail closed")
        assert login_calls == [], login_calls
        assert session.requests == 0
        assert store.get_persisted() == canonical

asyncio.run(main())
'''
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root)
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=root, env=env,
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_installed_miaccount_outbound_identity_is_per_account():
    root = Path(__file__).resolve().parents[1]
    script = r'''
import asyncio
import json
from http.cookies import SimpleCookie
from types import SimpleNamespace
from yarl import URL
from xiaomusic.auth import SimpleAuthManager, _LEGACY_AUTH_UA

class Response:
    def __init__(self, service=False):
        self.cookies = SimpleCookie()
        if service: self.cookies["serviceToken"] = "short"
        self.status = 200
        self.headers = {}
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def read(self): return ("&&&START&&&" + json.dumps({"code": 0})).encode()

class Session:
    def __init__(self):
        self.outbound = []
        self.cookie_jar = SimpleNamespace(filter_cookies=lambda url: {})
    def request(self, method, url, **kwargs):
        self.outbound.append((kwargs["headers"]["User-Agent"], kwargs.get("cookies", {})))
        return Response("clientSign=" in str(url))

async def main():
    manager = object.__new__(SimpleAuthManager)
    outputs = []
    for saved_ua in ("qr-agent-a", "qr-agent-b", None):
        session = Session()
        token = {"userId": "u", "passToken": "p", "deviceId": "saved-device"}
        if saved_ua: token["ua"] = saved_ua
        account = manager._new_isolated_mi_account(session, "u", token=token)
        assert await account._serviceLogin("serviceLogin?sid=micoapi&_json=true") == {"code": 0}
        assert await account._securityTokenService("https://account.xiaomi.com/cb?nonce=n", "n", "s") == "short"
        assert session.outbound[0][1]["deviceId"] == "saved-device"
        expected = saved_ua or _LEGACY_AUTH_UA
        assert [ua for ua, _ in session.outbound] == [expected, expected]
        outputs.append(expected)
    assert len(set(outputs)) == 3
asyncio.run(main())
'''
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root)
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=root, env=env,
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
