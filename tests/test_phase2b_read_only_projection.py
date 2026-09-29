from dataclasses import dataclass, field

import pytest

from xiaomusic.api.routers import system, v1
from xiaomusic.api.routers.v1_shared import configured_device_snapshot
from xiaomusic.config import Device


@dataclass
class _Config:
    mi_did: str = "981257654"
    devices: dict = field(default_factory=dict)
    auth_token_path: str = "auth.json"
    qrcode_timeout: int = 120


class _Auth:
    def __init__(self, ready=True, locked=False):
        self.ready = ready
        self.locked = locked
        self.calls = []
        self.mina_service = object() if ready else None
        self.login_signature = "same" if ready else "different"

    async def need_login(self):
        self.calls.append("need_login")
        raise AssertionError("read path must not probe auth")

    def auth_public_status_snapshot(self):
        return {"runtime_auth_ready": self.ready, "auth_locked": self.locked}


class _TokenStore:
    @staticmethod
    def get():
        return {}


class _XM:
    def __init__(self, config, auth, runtime):
        self.config = config
        self.auth_manager = auth
        self.device_manager = type("DM", (), {"devices": runtime})()
        async def _update_device_info(*args, **kwargs):
            raise AssertionError("update_device_info")
        self.device_manager.update_device_info = _update_device_info
        self.token_store = _TokenStore()
        self.getconfig = lambda: config
        self.getalldevices = self._raise("getalldevices")
        self.save_cur_config = self._raise("save_cur_config")

    @staticmethod
    def _raise(name):
        async def _called(*args, **kwargs):
            raise AssertionError(name)
        return _called


def _device(did="981257654"):
    return Device(
        did=did,
        device_id="0e050d91-1234-4abc-8def-0123456789ab",
        hardware="OH2P",
        name="XiaoAI",
    )


def test_projection_uses_config_known_and_cached_runtime_state_only():
    config = _Config(devices={"981257654": _device()})
    auth = _Auth(ready=True)
    runtime_player = object()
    xm = _XM(config, auth, {"981257654": runtime_player})
    config_identity = config.devices
    runtime_identity = xm.device_manager.devices

    rows = configured_device_snapshot(xm)

    assert rows == [{
        "did": "981257654",
        "device_id": "981257654",
        "cloud_device_id": "0e050d91-1234-4abc-8def-0123456789ab",
        "name": "XiaoAI",
        "model": "OH2P",
        "online": False,
        "known": True,
        "runtime_ready": True,
        "auth_ready": True,
        "controllable": True,
        "stream_ready": True,
        "reachable": None,
    }]
    assert config.devices is config_identity
    assert xm.device_manager.devices is runtime_identity
    assert auth.calls == []


def test_projection_accepts_dict_config_without_mutation():
    config = {"devices": {"981257654": _device()}}
    auth = _Auth(ready=True)
    xm = _XM(config, auth, {"981257654": object()})

    rows = configured_device_snapshot(xm)

    assert rows[0]["device_id"] == "981257654"
    assert rows[0]["cloud_device_id"] == "0e050d91-1234-4abc-8def-0123456789ab"
    assert config["devices"]["981257654"].did == "981257654"


@pytest.mark.asyncio
async def test_all_read_routes_do_not_call_refresh_or_probe(monkeypatch):
    config = _Config(devices={"981257654": _device()})
    auth = _Auth(ready=False)
    xm = _XM(config, auth, {})
    auth.mina_service = type("Mina", (), {"device_list": lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("device_list"))})()
    monkeypatch.setattr(v1, "_get_xiaomusic", lambda: xm)
    monkeypatch.setattr(system, "xiaomusic", xm)
    monkeypatch.setattr(system, "config", config)

    devices = await v1.api_v1_devices()
    status = await v1.api_v1_system_status()
    settings = await v1.api_v1_system_settings()
    legacy = await system.getsetting(need_device_list=True)

    assert devices["code"] == status["code"] == settings["code"] == 0
    row = devices["data"]["devices"][0]
    assert row["known"] is True
    assert row["runtime_ready"] is False
    assert row["auth_ready"] is False
    assert row["controllable"] is False
    assert row["stream_ready"] is False
    assert row["reachable"] is None
    assert status["data"]["devices_count"] == 1
    assert status["data"]["runtime_devices_count"] == 0
    assert legacy["device_list"][0]["did"] == "981257654"
    assert auth.calls == []


def test_locked_or_signature_mismatch_never_becomes_auth_ready():
    config = _Config(devices={"981257654": _device()})
    for auth in (_Auth(ready=True, locked=True), _Auth(ready=False, locked=False)):
        xm = _XM(config, auth, {"981257654": object()})
        row = configured_device_snapshot(xm)[0]
        assert row["auth_ready"] is False
        assert row["controllable"] is False


def test_runtime_orphans_are_not_projected_but_are_counted():
    config = _Config(devices={})
    xm = _XM(config, _Auth(ready=True), {"orphan": object()})
    assert configured_device_snapshot(xm) == []
    assert len(xm.device_manager.devices) == 1
