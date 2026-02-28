import logging
import types

from xiaomusic.config import Device
from xiaomusic.device_manager import DeviceManager


class _FakePlayer:
    def __init__(self, _xm, device, group_name):
        self.device = device
        self.group_name = group_name
        self.config = None
        self.cancel_count = 0
        self.update_count = 0

    def cancel_all_timer(self):
        self.cancel_count += 1

    def update_playlist(self):
        self.update_count += 1


def _build_config(devices):
    return types.SimpleNamespace(devices=devices, group_list="")


def test_update_devices_reuses_same_did_and_keeps_existing_player(monkeypatch):
    monkeypatch.setattr("xiaomusic.device_manager.XiaoMusicDevice", _FakePlayer)

    did = "981257654"
    dev1 = Device(did=did, device_id="dev-id-1", name="A")
    cfg = _build_config({did: dev1})
    mgr = DeviceManager(cfg, logging.getLogger("dm-test"), xiaomusic=object())

    mgr._update_devices()
    first = mgr.devices[did]

    # Refresh with same DID/device_id should not recreate/cancel timers.
    dev2 = Device(did=did, device_id="dev-id-1", name="A2")
    cfg.devices = {did: dev2}
    mgr._update_devices()
    second = mgr.devices[did]

    assert first is second
    assert second.cancel_count == 0
    assert second.update_count >= 1
    assert second.device.name == "A2"


def test_update_devices_cancels_removed_player(monkeypatch):
    monkeypatch.setattr("xiaomusic.device_manager.XiaoMusicDevice", _FakePlayer)

    did = "981257654"
    dev = Device(did=did, device_id="dev-id-1", name="A")
    cfg = _build_config({did: dev})
    mgr = DeviceManager(cfg, logging.getLogger("dm-test"), xiaomusic=object())

    mgr._update_devices()
    old = mgr.devices[did]

    # Remove device from config; stale player timers should be canceled.
    cfg.devices = {}
    mgr._update_devices()

    assert did not in mgr.devices
    assert old.cancel_count == 1
