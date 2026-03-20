from __future__ import annotations

from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.testclient import TestClient

import xiaomusic.api.dependencies as api_dependencies
from xiaomusic.api.routers import v1


def _v1_client() -> TestClient:
    app = FastAPI()
    app.include_router(v1.router)
    return TestClient(app)


@dataclass
class _Config:
    mi_did: str = "did-1,did-2"
    public_base_url: str = "http://127.0.0.1:58090"
    hostname: str = "127.0.0.1"
    public_port: int = 58090
    enable_pull_ask: bool = False
    httpauth_password: str = "secret"
    jellyfin_api_key: str = "jf-secret"

    @staticmethod
    def is_http_server_config(key: str) -> bool:
        return key in {"hostname", "public_port", "public_base_url"}

    def update_config(self, data: dict):
        for key, value in data.items():
            setattr(self, key, value)


def test_system_settings_query_success(monkeypatch):
    class _TokenStore:
        @staticmethod
        def get():
            return {"userId": "u", "passToken": "p", "ssecurity": "s", "serviceToken": "st"}

    class _Auth:
        @staticmethod
        async def need_login():
            return False

    class _XM:
        token_store = _TokenStore()
        auth_manager = _Auth()

        @staticmethod
        def getconfig():
            return _Config()

        @staticmethod
        async def getalldevices():
            return [{"miotDID": "did-1", "name": "XiaoAI", "hardware": "OH2P", "isOnline": True}]

    monkeypatch.setattr(v1, "_get_xiaomusic", lambda: _XM())
    client = _v1_client()
    resp = client.get("/api/v1/system/settings")
    body = resp.json()
    assert resp.status_code == 200
    assert body["code"] == 0
    assert body["data"]["device_ids"] == ["did-1", "did-2"]
    assert body["data"]["devices"][0]["device_id"] == "did-1"
    assert body["data"]["settings"]["httpauth_password"] == "******"
    assert body["data"]["settings"]["jellyfin_api_key"] == "******"


def test_system_settings_save_success(monkeypatch):
    saved: dict = {}

    class _XM:
        @staticmethod
        def getconfig():
            return _Config()

        @staticmethod
        async def saveconfig(data):
            saved.update(data)

    monkeypatch.setattr(v1, "_get_xiaomusic", lambda: _XM())
    monkeypatch.setattr(api_dependencies, "reset_http_server", lambda app: None)
    client = _v1_client()
    resp = client.post(
        "/api/v1/system/settings",
        json={"settings": {"enable_pull_ask": True, "httpauth_password": "******"}, "device_ids": ["did-1"]},
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["code"] == 0
    assert body["data"] == {"status": "ok", "saved": True}
    assert saved["mi_did"] == "did-1"
    assert saved["httpauth_password"] == "secret"


def test_system_setting_item_update_success(monkeypatch):
    config = _Config()
    saved = {"called": False}

    class _XM:
        @staticmethod
        def getconfig():
            return config

        @staticmethod
        def save_cur_config():
            saved["called"] = True

    monkeypatch.setattr(v1, "_get_xiaomusic", lambda: _XM())
    client = _v1_client()
    resp = client.post("/api/v1/system/settings/item", json={"key": "enable_pull_ask", "value": True})
    body = resp.json()
    assert resp.status_code == 200
    assert body["code"] == 0
    assert body["data"] == {"status": "ok", "updated": True, "key": "enable_pull_ask"}
    assert config.enable_pull_ask is True
    assert saved["called"] is True


def test_system_setting_item_validation_is_structured():
    client = _v1_client()
    resp = client.post("/api/v1/system/settings/item", json={"key": "", "value": True})
    body = resp.json()
    assert resp.status_code == 200
    assert body["code"] == 40001
    assert body["message"] == "key is required"
    assert body["data"]["error_code"] == "E_INVALID_REQUEST"
    assert body["data"]["stage"] == "request"
    assert body["data"]["field"] == "key"
