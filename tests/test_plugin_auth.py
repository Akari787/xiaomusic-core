from __future__ import annotations

import base64
import os
import subprocess
import sys
from types import SimpleNamespace

import bcrypt
import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.security import HTTPBasicCredentials
from fastapi.testclient import TestClient

from xiaomusic.api import dependencies
from xiaomusic.api.routers import plugin


class _PluginManager:
    def __init__(self, plugins_dir):
        self.plugins_dir = str(plugins_dir)
        self.updated = []
        self.reload_count = 0

    def refresh_plugin_list(self):
        return []

    def update_plugin_config(self, plugin_name, filename):
        self.updated.append((plugin_name, filename))

    def reload_plugins(self):
        self.reload_count += 1


def _basic(username: str, password: str) -> dict[str, str]:
    value = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {value}"}


def test_plugin_routes_require_basic_when_explicitly_enabled(monkeypatch, tmp_path):
    password = "plugin-test-password"
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    manager = _PluginManager(tmp_path / "plugins")
    monkeypatch.setattr(plugin, "xiaomusic", SimpleNamespace(js_plugin_manager=manager))
    monkeypatch.setattr(
        dependencies,
        "config",
        SimpleNamespace(disable_httpauth=False, httpauth_username="admin"),
    )
    monkeypatch.setattr(
        dependencies,
        "get_auth_settings",
        lambda: SimpleNamespace(HTTP_AUTH_HASH=hashed),
    )

    app = FastAPI()
    app.include_router(plugin.router)
    client = TestClient(app)

    assert client.get("/api/js-plugins").status_code == 401
    assert client.post(
        "/api/js-plugins/upload",
        files={"file": ("safe.js", b"module.exports = {};", "application/javascript")},
    ).status_code == 401
    assert client.get(
        "/api/js-plugins", headers=_basic("admin", "wrong")
    ).status_code == 401

    headers = _basic("admin", password)
    assert client.get("/api/js-plugins", headers=headers).status_code == 200
    uploaded = client.post(
        "/api/js-plugins/upload",
        headers=headers,
        files={"file": ("safe.js", b"module.exports = {};", "application/javascript")},
    )
    assert uploaded.status_code == 200
    assert manager.updated == [("safe", "safe.js")]
    assert manager.reload_count == 1

    for filename in (
        "",
        ".js",
        "/absolute.js",
        "C:\\absolute.js",
        "dir/foo.js",
        "dir\\foo.js",
        "../evil.js",
        "..\\evil.js",
    ):
        rejected = client.post(
            "/api/js-plugins/upload",
            headers=headers,
            files={"file": (filename, b"module.exports = {};", "application/javascript")},
        )
        assert rejected.status_code == 400, filename
        assert rejected.json()["success"] is False
    assert not (tmp_path / "evil.js").exists()
    assert sorted(path.name for path in (tmp_path / "plugins").iterdir()) == ["safe.js"]


def test_plugin_upload_accepts_js_content_that_mentions_filename_paths(
    monkeypatch, tmp_path
):
    """正文里出现 filename="a/b" 之类字面量时仍应视为合法上传。"""
    manager = _PluginManager(tmp_path / "plugins")
    monkeypatch.setattr(plugin, "xiaomusic", SimpleNamespace(js_plugin_manager=manager))
    monkeypatch.setattr(
        dependencies,
        "config",
        SimpleNamespace(disable_httpauth=True, httpauth_username=""),
    )
    app = FastAPI()
    app.include_router(plugin.router)
    app.dependency_overrides[dependencies.verification] = dependencies.no_verification
    client = TestClient(app)

    content = (
        b'// parses filename="a/b" and path="c\\d"\n'
        b"module.exports = { name: 'filename=\"nested/path.js\"' };\n"
    )
    uploaded = client.post(
        "/api/js-plugins/upload",
        files={"file": ("legit.js", content, "application/javascript")},
    )
    assert uploaded.status_code == 200, uploaded.text
    assert (tmp_path / "plugins" / "legit.js").read_bytes() == content
    assert manager.updated == [("legit", "legit.js")]


def test_multipart_header_scan_ignores_file_content():
    from xiaomusic.api.routers.plugin import _multipart_header_filename_is_unsafe

    benign = (
        b"--X\r\n"
        b'Content-Disposition: form-data; name="file"; filename="safe.js"\r\n'
        b"\r\n"
        b'const marker = \'filename="a/b"\';\r\n'
        b"--X--\r\n"
    )
    assert _multipart_header_filename_is_unsafe(benign) is False

    traversal = (
        b"--X\r\n"
        b'Content-Disposition: form-data; name="file"; filename="../evil.js"\r\n'
        b"\r\nok\r\n--X--\r\n"
    )
    assert _multipart_header_filename_is_unsafe(traversal) is True


@pytest.mark.asyncio
async def test_plugin_upload_rejects_nul_filename_before_multipart_normalization():
    class _Request:
        async def body(self):
            return b'Content-Disposition: form-data; name="file"; filename="bad\x00.js"'

        async def form(self):
            raise AssertionError("multipart parsing must not normalize a NUL filename")

    response = await plugin.upload_js_plugin(_Request())
    assert response.status_code == 400


def test_plugin_routes_follow_legacy_no_auth_override(monkeypatch, tmp_path):
    manager = _PluginManager(tmp_path / "plugins")
    monkeypatch.setattr(plugin, "xiaomusic", SimpleNamespace(js_plugin_manager=manager))
    monkeypatch.setattr(
        dependencies,
        "config",
        SimpleNamespace(disable_httpauth=True, httpauth_username=""),
    )
    app = FastAPI()
    app.include_router(plugin.router)
    app.dependency_overrides[dependencies.verification] = dependencies.no_verification
    response = TestClient(app).get("/api/js-plugins")
    assert response.status_code == 200


def test_production_assembly_uses_real_auth_reset_for_plugin_routes(
    monkeypatch, tmp_path
):
    import importlib

    current_dependencies = importlib.import_module("xiaomusic.api.dependencies")
    current_plugin = importlib.import_module("xiaomusic.api.routers.plugin")
    from xiaomusic.api.routers import register_routers

    reset_http_server = current_dependencies.reset_http_server

    password = "production-plugin-password"
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    manager = _PluginManager(tmp_path / "plugins")
    monkeypatch.setattr(
        current_plugin, "xiaomusic", SimpleNamespace(js_plugin_manager=manager)
    )
    config = SimpleNamespace(disable_httpauth=True, httpauth_username="admin")
    monkeypatch.setattr(current_dependencies._state, "_config", config)
    monkeypatch.setattr(
        current_dependencies._state, "_log", SimpleNamespace(info=lambda *args: None)
    )
    monkeypatch.setattr(
        current_dependencies,
        "get_auth_settings",
        lambda: SimpleNamespace(HTTP_AUTH_HASH=hashed),
    )

    app = FastAPI()
    register_routers(app)
    reset_http_server(app)
    client = TestClient(app)
    assert client.get("/api/js-plugins").status_code == 200

    config.disable_httpauth = False
    reset_http_server(app)
    assert client.get("/api/js-plugins").status_code == 401
    assert client.get(
        "/api/js-plugins", headers=_basic("admin", password)
    ).status_code == 200


def test_auth_static_files_calls_verification_without_assert(monkeypatch, tmp_path):
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.txt").write_text("ok", encoding="utf-8")
    password = "static-test-password"
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    monkeypatch.setattr(
        dependencies,
        "config",
        SimpleNamespace(disable_httpauth=False, httpauth_username="admin"),
    )
    monkeypatch.setattr(
        dependencies,
        "get_auth_settings",
        lambda: SimpleNamespace(HTTP_AUTH_HASH=hashed),
    )
    app = FastAPI()
    app.mount("/static", dependencies.AuthStaticFiles(directory=static_dir), name="static")
    client = TestClient(app)
    assert client.get("/static/index.txt").status_code == 401
    assert client.get(
        "/static/index.txt", headers=_basic("admin", password)
    ).text == "ok"


def test_strict_auth_keeps_explicit_legacy_username(monkeypatch):
    password = "legacy-password"
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    monkeypatch.setattr(
        dependencies,
        "config",
        SimpleNamespace(httpauth_username="existing-user"),
    )
    monkeypatch.setattr(
        dependencies,
        "get_auth_settings",
        lambda: SimpleNamespace(HTTP_AUTH_HASH=hashed),
    )
    credentials = HTTPBasicCredentials(username="existing-user", password=password)
    assert dependencies.strict_verification(credentials) is True


def test_legacy_route_still_accepts_no_auth_override():
    app = FastAPI()
    router = APIRouter()

    @router.get("/legacy", dependencies=[Depends(dependencies.verification)])
    def legacy():
        return {"ok": True}

    app.include_router(router)
    app.dependency_overrides[dependencies.verification] = dependencies.no_verification
    assert TestClient(app).get("/legacy").status_code == 200


def test_config_defaults_are_noauth_and_empty_username():
    env = os.environ.copy()
    env.pop("XIAOMUSIC_DISABLE_HTTPAUTH", None)
    env.pop("XIAOMUSIC_HTTPAUTH_USERNAME", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from xiaomusic.config import Config; c=Config(); print(c.disable_httpauth, c.httpauth_username)",
        ],
        cwd=os.fspath(os.path.dirname(os.path.dirname(__file__))),
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip().split() == ["True"]
