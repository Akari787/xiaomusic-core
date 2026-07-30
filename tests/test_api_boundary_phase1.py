"""API boundary governance Phase 1 tests.

Covers:
- Route inventory (25 whitelist public APIs)
- Production assembly via register_routers(): auth gate, OpenAPI schema
- Admin API routes behavior
- Internal diagnostics routes behavior
- Old/compat path behavior equivalence
- Input model validation
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from xiaomusic.api.models import PlayRequest, ResolveRequest
from xiaomusic.api.models.play_request import PlayOptionsModel
from xiaomusic.api.routers import admin, diagnostics, v1
from xiaomusic.api.routers import v1_shared as _vs


def _patch_source_manager(monkeypatch, cls):
    """Helper: monkeypatch the shared source plugin manager."""
    monkeypatch.setattr(_vs, "_get_source_plugin_manager", lambda: cls())


def _patch_xiaomusic(monkeypatch, xm_obj):
    """Helper: monkeypatch the shared _get_xiaomusic."""
    monkeypatch.setattr(_vs, "_get_xiaomusic", lambda: xm_obj)


def _patch_facade(monkeypatch, facade_obj):
    """Helper: reset and monkeypatch the shared facade."""
    _vs._facade = None  # type: ignore[attr-defined]
    monkeypatch.setattr(_vs, "_get_facade", lambda: facade_obj)


def _patch_runtime_auth(monkeypatch):
    async def _ready():
        return True
    monkeypatch.setattr(_vs, "_runtime_auth_ready_v1", _ready)


# ── Production Assembly Tests ──────────────────────────────────────────

def test_register_routers_production_assembly(monkeypatch):
    """Call the real register_routers(app) and verify the resulting app.

    Proves that production wiring (auth deps, schema visibility, route
    counts) is correct — not just hand-crafted test setups.
    """
    from xiaomusic.api.dependencies import verification
    from xiaomusic.api.routers import register_routers

    _patch_source_manager(monkeypatch, type("_M", (), {
        "registry_version": 0,
        "describe_plugins": staticmethod(lambda: []),
        "reload_summary": staticmethod(lambda: {}),
        "upload_plugin": staticmethod(lambda *a, **kw: {}),
        "uninstall_plugin": staticmethod(lambda *a, **kw: {}),
        "enable_plugin": staticmethod(lambda *a, **kw: {}),
        "disable_plugin": staticmethod(lambda *a, **kw: {}),
    }))
    _patch_xiaomusic(monkeypatch, type("_XM", (), {
        "getconfig": lambda s: type("_C", (), {
            "httpauth_password": "x", "jellyfin_api_key": "",
            "cors_allow_origins": [], "disable_httpauth": True,
            "mi_did": "",
        })(),
        "auth_manager": None,
    })())

    app = FastAPI()
    register_routers(app)

    # ── 1. 401 without auth ─────────────────────────────────────────
    def _assert_401(path, method="get"):
        client = TestClient(app)
        resp = getattr(client, method)(path)
        assert resp.status_code == 401, f"{method.upper()} {path} should 401, got {resp.status_code}"

    _assert_401("/api/admin/v1/sources")
    _assert_401("/api/admin/v1/auth/status")
    _assert_401("/api/internal/diagnostics/auth_state")
    _assert_401("/api/v1/sources")  # deprecated compat

    # ── 2. Override auth → 200/OK ────────────────────────────────────
    app.dependency_overrides[verification] = lambda: True
    authed = TestClient(app)
    assert authed.get("/api/admin/v1/sources").status_code == 200
    assert authed.get("/api/v1/sources").status_code == 200

    # ── 3. OpenAPI schema: exact method+path whitelist match ──────────
    schema = app.openapi()
    paths = schema.get("paths", {})

    V1_WHITELIST = {
        "POST /api/v1/play",
        "POST /api/v1/resolve",
        "POST /api/v1/control/stop",
        "POST /api/v1/control/pause",
        "POST /api/v1/control/resume",
        "POST /api/v1/control/tts",
        "POST /api/v1/control/volume",
        "POST /api/v1/control/probe",
        "POST /api/v1/control/previous",
        "POST /api/v1/control/next",
        "POST /api/v1/control/play-mode",
        "POST /api/v1/control/shutdown-timer",
        "POST /api/v1/library/favorites/add",
        "POST /api/v1/library/favorites/remove",
        "POST /api/v1/library/refresh",
        "GET /api/v1/library/playlists",
        "GET /api/v1/library/music-info",
        "GET /api/v1/devices",
        "GET /api/v1/system/status",
        "GET /api/v1/system/settings",
        "POST /api/v1/system/settings",
        "POST /api/v1/system/settings/item",
        "GET /api/v1/search/online",
        "GET /api/v1/player/state",
        "GET /api/v1/player/stream",
    }

    ADMIN_WHITELIST = {
        "GET /api/admin/v1/sources",
        "POST /api/admin/v1/sources/reload",
        "POST /api/admin/v1/sources/upload",
        "DELETE /api/admin/v1/sources/{name}",
        "PUT /api/admin/v1/sources/{name}/enable",
        "PUT /api/admin/v1/sources/{name}/disable",
        "GET /api/admin/v1/auth/status",
    }

    def _method_path_set(paths_dict, prefix):
        result = set()
        for path_url, methods in paths_dict.items():
            if not path_url.startswith(prefix):
                continue
            for method in methods:
                result.add(f"{method.upper()} {path_url}")
        return result

    actual_v1 = _method_path_set(paths, "/api/v1/")
    actual_admin = _method_path_set(paths, "/api/admin/")
    actual_internal = {p for p in paths if p.startswith("/api/internal/")}

    assert actual_v1 == V1_WHITELIST, (
        f"v1 mismatch. Extra: {actual_v1 - V1_WHITELIST}, Missing: {V1_WHITELIST - actual_v1}"
    )
    assert actual_admin == ADMIN_WHITELIST, (
        f"admin mismatch. Extra: {actual_admin - ADMIN_WHITELIST}, Missing: {ADMIN_WHITELIST - actual_admin}"
    )
    assert not actual_internal, f"internal paths leaked: {sorted(actual_internal)}"


# ── Admin API Behavior Tests ───────────────────────────────────────────

def test_admin_sources_returns_registry_and_sources(monkeypatch):
    class _Manager:
        registry_version = 5

        @staticmethod
        def describe_plugins():
            return [{"name": "builtin_a", "origin": "builtin", "status": "active", "version": None, "error": ""}]

    _patch_source_manager(monkeypatch, _Manager)
    client = TestClient(FastAPI())
    client.app.include_router(admin.router)
    resp = client.get("/api/admin/v1/sources")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["registry_version"] == 5
    assert len(body["data"]["sources"]) == 1


def test_admin_sources_reload(monkeypatch):
    class _Manager:
        @staticmethod
        def reload_summary():
            return {"reloaded": True, "registry_version": 2, "loaded_count": 3, "failed_count": 0}

    _patch_source_manager(monkeypatch, _Manager)
    client = TestClient(FastAPI())
    client.app.include_router(admin.router)
    resp = client.post("/api/admin/v1/sources/reload")
    assert resp.status_code == 200
    assert resp.json()["data"]["reloaded"] is True


def test_admin_sources_enable_disable(monkeypatch):
    class _Manager:
        @staticmethod
        def enable_plugin(name):
            return {"name": name, "status": "active"}

        @staticmethod
        def disable_plugin(name):
            return {"name": name, "status": "disabled"}

    _patch_source_manager(monkeypatch, _Manager)
    client = TestClient(FastAPI())
    client.app.include_router(admin.router)
    assert client.put("/api/admin/v1/sources/t/enable").json()["data"]["status"] == "active"
    assert client.put("/api/admin/v1/sources/t/disable").json()["data"]["status"] == "disabled"


def test_admin_auth_status_returns_v1_envelope(monkeypatch):
    class _Auth:
        @staticmethod
        def map_auth_public_status(runtime_auth_ready=None):
            return {"status": "healthy", "auth_mode": "healthy", "status_reason": "healthy", "recovery_failure_count": 0}

    _patch_xiaomusic(monkeypatch, type("_XM", (), {"auth_manager": _Auth()})())
    _patch_runtime_auth(monkeypatch)
    client = TestClient(FastAPI())
    client.app.include_router(admin.router)
    resp = client.get("/api/admin/v1/auth/status")
    assert resp.status_code == 200
    assert "generated_at_ms" in resp.json()["data"]


# ── Internal Diagnostics Tests ─────────────────────────────────────────

def test_diag_endpoints_return_200(monkeypatch):
    """All 6 diagnostics endpoints return 200 with auth mock."""
    class _Auth:
        def __init__(self):
            pass
        auth_debug_state = staticmethod(lambda: {})
        auth_recovery_debug_state = staticmethod(lambda: {})
        miaccount_login_trace_debug_state = staticmethod(lambda: {})
        auth_rebuild_debug_state = staticmethod(lambda: {})
        auth_runtime_reload_debug_state = staticmethod(lambda: {})
        auth_short_session_rebuild_debug_state = staticmethod(lambda: {})

    _patch_xiaomusic(monkeypatch, type("_XM", (), {"auth_manager": _Auth()})())

    endpoints = [
        "/api/internal/diagnostics/auth_state",
        "/api/internal/diagnostics/auth_recovery_state",
        "/api/internal/diagnostics/miaccount_login_trace",
        "/api/internal/diagnostics/auth_rebuild_state",
        "/api/internal/diagnostics/auth_runtime_reload_state",
        "/api/internal/diagnostics/auth_short_session_rebuild_state",
    ]
    client = TestClient(FastAPI())
    client.app.include_router(diagnostics.router)
    for ep in endpoints:
        resp = client.get(ep)
        assert resp.status_code == 200, f"{ep}: {resp.status_code}"
        assert resp.json()["code"] == 0


# ── Old Path Compatibility (equivalence via shared services) ───────────

def test_old_sources_path_works(monkeypatch):
    class _Manager:
        registry_version = 7

        @staticmethod
        def describe_plugins():
            return [{"name": "compat", "origin": "builtin", "status": "active", "version": None, "error": ""}]

    _patch_source_manager(monkeypatch, _Manager)
    client = TestClient(FastAPI())
    client.app.include_router(v1.router)
    resp = client.get("/api/v1/sources")
    assert resp.status_code == 200
    assert resp.json()["code"] == 0


def test_old_debug_path_works(monkeypatch):
    class _Auth:
        auth_debug_state = staticmethod(lambda: {"auth_mode": "degraded", "last_auth_error": "test"})

    _patch_xiaomusic(monkeypatch, type("_XM", (), {"auth_manager": _Auth()})())
    client = TestClient(FastAPI())
    client.app.include_router(v1.router)
    resp = client.get("/api/v1/debug/auth_state")
    assert resp.status_code == 200
    assert resp.json()["data"]["auth_mode"] == "degraded"


def test_sources_old_new_equivalent(monkeypatch):
    data = {"registry_version": 42, "sources": [{"name": "x", "origin": "builtin", "status": "active", "version": "1.0", "error": ""}]}

    class _Manager:
        registry_version = data["registry_version"]

        @staticmethod
        def describe_plugins():
            return data["sources"]

    _patch_source_manager(monkeypatch, _Manager)
    old_c = TestClient(FastAPI())
    old_c.app.include_router(v1.router)
    new_c = TestClient(FastAPI())
    new_c.app.include_router(admin.router)
    assert old_c.get("/api/v1/sources").json()["data"] == new_c.get("/api/admin/v1/sources").json()["data"]


def test_debug_old_new_equivalent(monkeypatch):
    class _Auth:
        auth_debug_state = staticmethod(lambda: {"auth_mode": "degraded", "last_auth_error": "timeout"})

    _patch_xiaomusic(monkeypatch, type("_XM", (), {"auth_manager": _Auth()})())
    old_c = TestClient(FastAPI())
    old_c.app.include_router(v1.router)
    new_c = TestClient(FastAPI())
    new_c.app.include_router(diagnostics.router)
    assert old_c.get("/api/v1/debug/auth_state").json()["data"] == new_c.get("/api/internal/diagnostics/auth_state").json()["data"]


# ── Input Model Validation Tests ───────────────────────────────────────

def test_play_request_rejects_blank_query():
    with pytest.raises(ValidationError):
        PlayRequest(device_id="did-1", query="   ")
    with pytest.raises(ValidationError):
        PlayRequest(device_id="did-1", query="")
    req = PlayRequest(device_id="did-1", query="hello")
    assert req.query == "hello"


def test_resolve_request_rejects_blank_query():
    with pytest.raises(ValidationError):
        ResolveRequest(query="   ")
    with pytest.raises(ValidationError):
        ResolveRequest(query="")
    req = ResolveRequest(query="test")
    assert req.query == "test"


def test_play_request_rejects_invalid_source_hint():
    with pytest.raises(ValidationError):
        PlayRequest(device_id="did-1", query="song", source_hint="invalid_hint")
    for valid in ["auto", "direct_url", "site_media", "jellyfin", "local_library"]:
        req = PlayRequest(device_id="did-1", query="song", source_hint=valid)
        assert req.source_hint == valid


def test_play_request_rejects_wrong_shuffle_type():
    with pytest.raises(ValidationError):
        PlayRequest(device_id="did-1", query="song", options={"shuffle": "yes"})
    with pytest.raises(ValidationError):
        PlayRequest(device_id="did-1", query="song", options={"shuffle": 1})
    req = PlayRequest(device_id="did-1", query="song", options={"shuffle": True})
    assert req.options.shuffle is True


def test_play_request_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        PlayRequest(device_id="did-1", query="song", unknown_field="value")


def test_play_options_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        PlayOptionsModel(shuffle=True, made_up_field=123)


def test_play_request_accepts_valid_options():
    opts = PlayOptionsModel(shuffle=True, loop=False, volume=50, prefer_proxy=True, media_id="abc123")
    assert opts.shuffle is True
    assert opts.volume == 50


# ── HTTP-level 422 Structured Error Tests (production assembly) ──────

def _assemble_and_authed(monkeypatch):
    """Build a full app via register_routers with mocked deps, override auth.

    Uses the real xiaomusic.api.app.app instance so production exception
    handlers (including structured 422) are active.
    """
    from xiaomusic.api.dependencies import verification
    from xiaomusic.api.routers import register_routers

    _patch_source_manager(monkeypatch, type("_M", (), {
        "registry_version": 0,
        "describe_plugins": staticmethod(lambda: []),
        "reload_summary": staticmethod(lambda: {}),
        "upload_plugin": staticmethod(lambda *a, **kw: {}),
        "uninstall_plugin": staticmethod(lambda *a, **kw: {}),
        "enable_plugin": staticmethod(lambda *a, **kw: {}),
        "disable_plugin": staticmethod(lambda *a, **kw: {}),
    }))
    _patch_xiaomusic(monkeypatch, type("_XM", (), {
        "getconfig": lambda s: type("_C", (), {
            "httpauth_password": "x", "jellyfin_api_key": "",
            "cors_allow_origins": [], "disable_httpauth": True, "mi_did": "",
        })(),
        "auth_manager": None,
    })())

    from xiaomusic.api.dependencies import _state

    # reset stale state from previous test runs
    _state._xiaomusic = None
    _state._config = None
    _state._log = None

    from xiaomusic.api.app import app

    register_routers(app)
    app.dependency_overrides[verification] = lambda: True
    return TestClient(app)


def _assert_v1_422(resp, expected_stage="request"):
    """Assert v1 structured 422 envelope."""
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == 50001
    assert body["message"] == "invalid request"
    assert isinstance(body["request_id"], str) and body["request_id"]
    assert body["data"]["error_code"] == "E_INVALID_REQUEST"
    assert body["data"]["stage"] == expected_stage
    assert isinstance(body["data"]["detail"], list)
    return body


def test_v1_play_blank_query_422_structured(monkeypatch):
    client = _assemble_and_authed(monkeypatch)
    resp = client.post("/api/v1/play", json={"device_id": "did-1", "query": "   "})
    _assert_v1_422(resp)


def test_v1_play_invalid_source_hint_422_structured(monkeypatch):
    client = _assemble_and_authed(monkeypatch)
    resp = client.post("/api/v1/play", json={"device_id": "did-1", "query": "song", "source_hint": "bad_hint"})
    _assert_v1_422(resp)


def test_v1_play_wrong_shuffle_type_422_structured(monkeypatch):
    client = _assemble_and_authed(monkeypatch)
    resp = client.post("/api/v1/play", json={"device_id": "did-1", "query": "song", "options": {"shuffle": "yes"}})
    _assert_v1_422(resp)


def test_v1_play_unknown_field_422_structured(monkeypatch):
    client = _assemble_and_authed(monkeypatch)
    resp = client.post("/api/v1/play", json={"device_id": "did-1", "query": "song", "extra_stuff": 123})
    _assert_v1_422(resp)
