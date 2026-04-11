from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from xiaomusic.api.routers import v1


def _v1_client() -> TestClient:
    app = FastAPI()
    app.include_router(v1.router)
    return TestClient(app)


def test_api_v1_sources_returns_registry_version_and_sources(monkeypatch):
    class _Manager:
        registry_version = 3

        @staticmethod
        def describe_plugins():
            return [
                {
                    "name": "direct_url",
                    "origin": "builtin",
                    "status": "active",
                    "version": None,
                    "error": "",
                },
                {
                    "name": "broken_external",
                    "origin": "external",
                    "status": "failed",
                    "version": None,
                    "error": "syntax error",
                },
            ]

    monkeypatch.setattr(v1, "_get_source_plugin_manager", lambda: _Manager())
    client = _v1_client()

    response = client.get("/api/v1/sources")
    assert response.status_code == 200
    body = response.json()

    assert body["code"] == 0
    assert body["message"] == "ok"
    assert body["data"] == {
        "registry_version": 3,
        "sources": [
            {
                "name": "direct_url",
                "origin": "builtin",
                "status": "active",
                "version": None,
                "error": "",
            },
            {
                "name": "broken_external",
                "origin": "external",
                "status": "failed",
                "version": None,
                "error": "syntax error",
            },
        ],
    }
    assert isinstance(body["request_id"], str)
    assert body["request_id"]


def test_api_v1_sources_reload_returns_summary(monkeypatch):
    class _Manager:
        registry_version = 4

        @staticmethod
        def reload_summary():
            return {
                "reloaded": True,
                "registry_version": 4,
                "loaded_count": 5,
                "failed_count": 1,
            }

    monkeypatch.setattr(v1, "_get_source_plugin_manager", lambda: _Manager())
    client = _v1_client()

    response = client.post("/api/v1/sources/reload")
    assert response.status_code == 200
    body = response.json()

    assert body["code"] == 0
    assert body["message"] == "ok"
    assert body["data"] == {
        "reloaded": True,
        "registry_version": 4,
        "loaded_count": 5,
        "failed_count": 1,
    }
    assert isinstance(body["request_id"], str)
    assert body["request_id"]


def test_api_v1_sources_upload_returns_uploaded_plugin(monkeypatch):
    class _Manager:
        @staticmethod
        def upload_plugin(filename: str, content: bytes):
            assert filename == "mock_external.py"
            assert content == b"plugin-bytes"
            return {
                "name": "mock_external",
                "origin": "external",
                "status": "active",
                "version": "1.0.0",
                "error": "",
            }

    monkeypatch.setattr(v1, "_get_source_plugin_manager", lambda: _Manager())
    client = _v1_client()

    response = client.post(
        "/api/v1/sources/upload",
        files={"file": ("mock_external.py", b"plugin-bytes", "text/x-python")},
    )
    assert response.status_code == 200
    body = response.json()

    assert body["code"] == 0
    assert body["data"] == {
        "name": "mock_external",
        "origin": "external",
        "status": "active",
        "version": "1.0.0",
        "error": "",
    }


def test_api_v1_sources_delete_rejects_builtin(monkeypatch):
    class _Manager:
        @staticmethod
        def uninstall_plugin(name: str):
            raise PermissionError(f"builtin source plugin cannot be deleted: {name}")

    monkeypatch.setattr(v1, "_get_source_plugin_manager", lambda: _Manager())
    client = _v1_client()

    response = client.delete("/api/v1/sources/direct_url")
    assert response.status_code == 200
    body = response.json()

    assert body["code"] == 40301
    assert body["message"] == "builtin source plugin cannot be deleted: direct_url"
    assert body["data"]["error_code"] == "E_FORBIDDEN"


def test_api_v1_sources_enable_disable_returns_updated_item(monkeypatch):
    class _Manager:
        @staticmethod
        def disable_plugin(name: str):
            assert name == "direct_url"
            return {
                "name": "direct_url",
                "origin": "builtin",
                "status": "disabled",
                "version": None,
                "error": "",
            }

        @staticmethod
        def enable_plugin(name: str):
            assert name == "direct_url"
            return {
                "name": "direct_url",
                "origin": "builtin",
                "status": "active",
                "version": None,
                "error": "",
            }

    monkeypatch.setattr(v1, "_get_source_plugin_manager", lambda: _Manager())
    client = _v1_client()

    disable_response = client.put("/api/v1/sources/direct_url/disable")
    enable_response = client.put("/api/v1/sources/direct_url/enable")

    assert disable_response.status_code == 200
    assert disable_response.json()["data"]["status"] == "disabled"
    assert enable_response.status_code == 200
    assert enable_response.json()["data"]["status"] == "active"
