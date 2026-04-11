import importlib
import sys
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from xiaomusic.api.dependencies import no_verification, verification
from xiaomusic.diagnostics import StartupDiagnostics


@pytest.fixture
def client(monkeypatch):
    if "qrcode" not in sys.modules:
        qrcode_module = ModuleType("qrcode")
        qrcode_main_module = ModuleType("qrcode.main")

        class _FakeQRCode:
            def __init__(self, *args, **kwargs):
                pass

            def add_data(self, *args, **kwargs):
                return None

            def make(self, *args, **kwargs):
                return None

            def make_image(self, *args, **kwargs):
                class _Image:
                    def save(self, buf, fmt):
                        buf.write(b"fake-png")

                return _Image()

        qrcode_main_module.QRCode = _FakeQRCode
        qrcode_module.QRCode = _FakeQRCode
        qrcode_module.main = qrcode_main_module
        monkeypatch.setitem(sys.modules, "qrcode", qrcode_module)
        monkeypatch.setitem(sys.modules, "qrcode.main", qrcode_main_module)

    qrcode_login_module = ModuleType("xiaomusic.qrcode_login")

    class _FakeMiJiaAPI:
        def __init__(self, *args, **kwargs):
            pass

    qrcode_login_module.MiJiaAPI = _FakeMiJiaAPI
    monkeypatch.setitem(sys.modules, "xiaomusic.qrcode_login", qrcode_login_module)

    sys.modules.pop("xiaomusic.api.routers.system", None)
    system = importlib.import_module("xiaomusic.api.routers.system")
    app = FastAPI()
    app.include_router(system.router)
    app.dependency_overrides[verification] = no_verification
    return TestClient(app)


class _FakeTokenStore:
    path = None

    def __init__(self, payload):
        self._payload = payload

    def get(self):
        return dict(self._payload)


class _Reachability:
    def __init__(self, ip: str, local_reachable: bool, cloud_reachable: bool, last_probe_ts: int):
        self.ip = ip
        self.local_reachable = local_reachable
        self.cloud_reachable = cloud_reachable
        self.last_probe_ts = last_probe_ts


def test_diagnostics_route_returns_unified_view(monkeypatch, client):
    from xiaomusic.api.routers import system
    import xiaomusic.diagnostics as diagnostics_module

    startup = StartupDiagnostics(ok=True, checked_at=1710000000.0, notes=["startup ok"])

    class _Auth:
        @staticmethod
        def auth_status_snapshot():
            return {"mode": "healthy", "locked": False, "lock_reason": ""}

        @staticmethod
        def auth_debug_state():
            return {"last_auth_error": ""}

        @staticmethod
        def auth_short_session_rebuild_debug_state():
            return {
                "last_short_session_rebuild": {"result": "ok"},
                "last_auth_recovery_flow": {"result": "ok"},
            }

    class _Config:
        auth_token_path = "conf/auth.json"
        qrcode_timeout = 120
        keyword_override_mode = "override"
        keyword_conflicts = ["play"]
        music_path = "."
        download_path = "."
        temp_path = "."
        cache_dir = "."
        conf_path = "."
        ffmpeg_location = ""
        jellyfin_enabled = False
        jellyfin_base_url = ""
        jellyfin_api_key = ""

    music_library = SimpleNamespace(all_music={"song1": "a.mp3"})
    device_manager = SimpleNamespace(
        devices={
            "did-ok": SimpleNamespace(device=SimpleNamespace(name="Living Room")),
            "did-unknown": SimpleNamespace(device=SimpleNamespace(name="Bedroom")),
        }
    )
    xiaomusic = SimpleNamespace(
        startup_diagnostics=startup,
        auth_manager=_Auth(),
        token_store=_FakeTokenStore(
            {
                "userId": "u",
                "passToken": "p",
                "psecurity": "ps",
                "ssecurity": "ss",
                "cUserId": "cu",
                "deviceId": "d",
                "serviceToken": "st",
            }
        ),
        last_download_result={"ok": True},
        music_library=music_library,
        online_music_service=object(),
        js_plugin_manager=object(),
        device_manager=device_manager,
    )

    async def _runtime_ready():
        return True

    monkeypatch.setattr(system, "config", _Config())
    monkeypatch.setattr(system, "xiaomusic", xiaomusic)
    monkeypatch.setattr(system, "_runtime_auth_ready", _runtime_ready)
    monkeypatch.setattr(system, "qrcode_login_task", None)
    monkeypatch.setattr(system, "qrcode_login_started_at", 0.0)
    monkeypatch.setattr(system, "qrcode_login_error", "")
    monkeypatch.setattr(
        diagnostics_module,
        "_get_device_reachability_cache",
        lambda: {
            "did-ok": _Reachability(
                ip="192.168.1.10",
                local_reachable=True,
                cloud_reachable=False,
                last_probe_ts=1710000100,
            )
        },
    )

    response = client.get("/diagnostics")
    assert response.status_code == 200
    payload = response.json()

    assert payload["generated_at_ms"] > 0
    assert payload["overall_status"] == "unknown"
    assert set(payload["areas"].keys()) == {
        "startup",
        "auth",
        "sources",
        "devices",
        "playback_readiness",
    }

    startup_area = payload["areas"]["startup"]
    assert startup_area["status"] == "ok"
    assert startup_area["data"]["ok"] is True
    assert startup_area["data"]["checked_at"] == 1710000000.0
    assert startup_area["data"]["keyword_conflicts"] == ["play"]

    auth_area = payload["areas"]["auth"]
    assert auth_area["status"] == "ok"
    assert auth_area["data"]["runtime_auth_ready"] is True
    assert auth_area["data"]["status_reason"] == "healthy"

    sources_area = payload["areas"]["sources"]
    assert sources_area["data"]["ready_count"] == 3
    assert sources_area["data"]["unknown_count"] == 1

    devices_area = payload["areas"]["devices"]
    assert devices_area["status"] == "unknown"
    assert devices_area["data"]["total"] == 2
    assert devices_area["data"]["reachable"] == 1
    assert devices_area["data"]["unknown"] == 1

    playback_area = payload["areas"]["playback_readiness"]
    assert playback_area["status"] == "unknown"
    assert playback_area["data"]["can_dispatch_transport"] is None


def test_diagnostics_route_marks_overall_failed_when_auth_failed(monkeypatch, client):
    from xiaomusic.api.routers import system
    import xiaomusic.diagnostics as diagnostics_module

    startup = StartupDiagnostics(ok=True, checked_at=1710000000.0, notes=[])

    class _Auth:
        @staticmethod
        def auth_status_snapshot():
            return {
                "mode": "locked",
                "locked": True,
                "lock_reason": "qrcode required",
                "lock_transition_reason": "manual auth required",
                "need_qr_scan": True,
                "long_term_expired": True,
                "user_action_required": True,
            }

        @staticmethod
        def auth_debug_state():
            return {"last_auth_error": "qrcode required"}

        @staticmethod
        def auth_short_session_rebuild_debug_state():
            return {
                "last_short_session_rebuild": {"result": "failed"},
                "last_auth_recovery_flow": {"result": "failed"},
            }

    class _Config:
        auth_token_path = "conf/auth.json"
        qrcode_timeout = 120
        keyword_override_mode = "override"
        keyword_conflicts = []
        music_path = "."
        download_path = "."
        temp_path = "."
        cache_dir = "."
        conf_path = "."
        ffmpeg_location = ""
        jellyfin_enabled = False
        jellyfin_base_url = ""
        jellyfin_api_key = ""

    xiaomusic = SimpleNamespace(
        startup_diagnostics=startup,
        auth_manager=_Auth(),
        token_store=_FakeTokenStore({}),
        last_download_result=None,
        music_library=SimpleNamespace(all_music={}),
        online_music_service=None,
        js_plugin_manager=None,
        device_manager=SimpleNamespace(devices={}),
    )

    async def _runtime_ready():
        return False

    monkeypatch.setattr(system, "config", _Config())
    monkeypatch.setattr(system, "xiaomusic", xiaomusic)
    monkeypatch.setattr(system, "_runtime_auth_ready", _runtime_ready)
    monkeypatch.setattr(system, "qrcode_login_task", None)
    monkeypatch.setattr(system, "qrcode_login_started_at", 0.0)
    monkeypatch.setattr(system, "qrcode_login_error", "")
    monkeypatch.setattr(diagnostics_module, "_get_device_reachability_cache", lambda: {})

    response = client.get("/diagnostics")
    assert response.status_code == 200
    payload = response.json()

    assert payload["overall_status"] == "failed"
    assert payload["areas"]["auth"]["status"] == "failed"
    assert payload["areas"]["auth"]["data"]["status_reason"] == "manual_login_required"
    assert "auth" in payload["summary"]
