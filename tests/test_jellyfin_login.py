"""Jellyfin username/password login (replaces the retired API key).

Background (2026-11-22): Jellyfin 12.4 rejected the legacy X-Emby-Token header,
so playlist sync failed with 401 and new songs never appeared. Login now uses
/Users/AuthenticateByName; only the issued session token is persisted and the
password is never stored, logged, or returned.
"""

from __future__ import annotations

import json
import logging
import os
import stat
import types

import pytest

import xiaomusic.jellyfin_client as jc
from xiaomusic.jellyfin_client import JellyfinAuthError, JellyfinClient

PASSWORD = "s3cret-pw-XYZ"


class _Resp:
    def __init__(self, status, payload=None):
        self.status = status
        self._payload = payload or {}

    async def json(self):
        return self._payload

    async def text(self):
        return json.dumps(self._payload)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeSession:
    """Records requests; responses come from the class-level route table."""

    routes: dict = {}
    calls: list = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def _reply(self, method, url, **kw):
        _FakeSession.calls.append({"method": method, "url": url, **kw})
        for suffix, resp in _FakeSession.routes.items():
            if url.endswith(suffix):
                return resp
        return _Resp(404)

    def get(self, url, **kw):
        return self._reply("GET", url, **kw)

    def post(self, url, **kw):
        return self._reply("POST", url, **kw)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(jc.aiohttp, "ClientSession", _FakeSession)
    _FakeSession.routes = {
        "/Users/AuthenticateByName": _Resp(
            200, {"AccessToken": "tok-123", "User": {"Id": "uid-1", "Name": "akari"}}
        ),
        "/Sessions/Logout": _Resp(204),
    }
    _FakeSession.calls = []
    cfg = types.SimpleNamespace(
        jellyfin_enabled=True,
        jellyfin_base_url="http://jf:8096/",
        conf_path=str(tmp_path),
    )
    return JellyfinClient(cfg, logging.getLogger("test-jf"))


@pytest.mark.asyncio
async def test_login_persists_token_but_never_password(client, tmp_path):
    status = await client.login("akari", PASSWORD)

    assert status["logged_in"] is True
    assert status["username"] == "akari"
    assert client.enabled() is True

    req = _FakeSession.calls[0]
    assert req["url"] == "http://jf:8096/Users/AuthenticateByName"
    assert req["json"] == {"Username": "akari", "Pw": PASSWORD}
    assert "Token=" not in req["headers"]["Authorization"]

    auth_file = tmp_path / "jellyfin_auth.json"
    raw = auth_file.read_text(encoding="utf-8")
    assert PASSWORD not in raw
    data = json.loads(raw)
    assert data["access_token"] == "tok-123"
    assert data["user_id"] == "uid-1"
    assert stat.S_IMODE(os.stat(auth_file).st_mode) == 0o600
    assert PASSWORD not in json.dumps(status)


@pytest.mark.asyncio
async def test_api_requests_use_session_token_header(client):
    await client.login("akari", PASSWORD)
    headers = client._headers()
    assert headers["Authorization"].startswith("MediaBrowser ")
    assert 'Token="tok-123"' in headers["Authorization"]
    assert "X-Emby-Token" not in headers
    assert await client._resolve_user_id() == "uid-1"


@pytest.mark.asyncio
async def test_stream_url_carries_session_token(client):
    await client.login("akari", PASSWORD)
    assert client._stream_url("item1") == "http://jf:8096/Audio/item1/stream.mp3?api_key=tok-123"


@pytest.mark.asyncio
async def test_wrong_password_is_auth_error_and_stores_nothing(client, tmp_path):
    _FakeSession.routes["/Users/AuthenticateByName"] = _Resp(401)
    with pytest.raises(JellyfinAuthError):
        await client.login("akari", "wrong")
    assert not (tmp_path / "jellyfin_auth.json").exists()
    assert client.enabled() is False


def test_not_logged_in_requests_raise(client):
    assert client.enabled() is False
    with pytest.raises(JellyfinAuthError):
        client._headers()


@pytest.mark.asyncio
async def test_logout_drops_token_keeps_device_id(client, tmp_path):
    await client.login("akari", PASSWORD)
    device_id = json.loads((tmp_path / "jellyfin_auth.json").read_text())["device_id"]

    await client.logout()

    assert client.login_status()["logged_in"] is False
    assert json.loads((tmp_path / "jellyfin_auth.json").read_text()) == {"device_id": device_id}
    _FakeSession.calls.clear()
    await client.login("akari", PASSWORD)
    assert f'DeviceId="{device_id}"' in _FakeSession.calls[0]["headers"]["Authorization"]


@pytest.mark.asyncio
async def test_session_ignored_after_server_change(client):
    await client.login("akari", PASSWORD)
    client.config.jellyfin_base_url = "http://other:8096"
    assert client.login_status()["logged_in"] is False


@pytest.mark.asyncio
async def test_expired_token_during_sync_raises_instead_of_dropping_lists(client):
    await client.login("akari", PASSWORD)
    _FakeSession.routes["/Users/uid-1/Items"] = _Resp(401)
    with pytest.raises(JellyfinAuthError):
        await client.export_music_lists()


@pytest.mark.asyncio
async def test_failed_playlist_aborts_export(client):
    await client.login("akari", PASSWORD)
    _FakeSession.routes["/Users/uid-1/Items"] = _Resp(
        200, {"Items": [{"Id": "pl1", "Name": "日语"}]}
    )
    _FakeSession.routes["/Playlists/pl1/Items"] = _Resp(500)
    with pytest.raises(Exception, match="日语"):
        await client.export_music_lists()


def test_stream_url_keeps_single_query_param():
    url = JellyfinClient.build_stream_url("http://jf:8096/", "item1", "abc123")
    assert url == "http://jf:8096/Audio/item1/stream.mp3?api_key=abc123"
