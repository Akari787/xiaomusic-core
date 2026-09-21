from __future__ import annotations

import base64
import os
import subprocess
import sys
from types import SimpleNamespace

import bcrypt
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


def test_sensitive_plugin_routes_ignore_legacy_no_auth_override(monkeypatch, tmp_path):
    password = "plugin-test-password"
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    manager = _PluginManager(tmp_path / "plugins")
    monkeypatch.setattr(plugin, "xiaomusic", SimpleNamespace(js_plugin_manager=manager))
    monkeypatch.setattr(
        dependencies,
        "config",
        SimpleNamespace(httpauth_username="admin"),
    )
    monkeypatch.setattr(
        dependencies,
        "get_auth_settings",
        lambda: SimpleNamespace(HTTP_AUTH_HASH=hashed),
    )

    app = FastAPI()
    app.include_router(plugin.router)
    # This is the production legacy override and must not affect plugin.router.
    app.dependency_overrides[dependencies.verification] = dependencies.no_verification
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

    for filename in ("../evil.js", "..\\\\evil.js"):
        rejected = client.post(
            "/api/js-plugins/upload",
            headers=headers,
            files={"file": (filename, b"module.exports = {};", "application/javascript")},
        )
        assert rejected.status_code == 400
        assert rejected.json()["success"] is False
    assert not (tmp_path / "evil.js").exists()
    assert sorted(path.name for path in (tmp_path / "plugins").iterdir()) == ["safe.js"]


def test_production_assembly_keeps_plugin_auth_strict(monkeypatch):
    from xiaomusic.api.dependencies import no_verification, verification
    from xiaomusic.api.routers import register_routers

    app = FastAPI()
    register_routers(app)
    app.dependency_overrides[verification] = no_verification
    assert TestClient(app).get("/api/js-plugins").status_code == 401


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


def test_new_config_defaults_are_auth_enabled_and_admin():
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
    assert result.stdout.strip().endswith("False admin")
