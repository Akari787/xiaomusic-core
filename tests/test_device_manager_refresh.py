import logging
import types

import pytest

from xiaomusic.config import Device
from xiaomusic.device_manager import DeviceManager


class _FakePlayer:
    fail_for = set()
    fail_cancel_for = set()

    def __init__(self, _xm, device, group_name):
        if device.did in self.fail_for:
            raise RuntimeError(f"cannot build {device.did}")
        self.device = device
        self.group_name = group_name
        self.config = None
        self.cancel_count = 0
        self.update_count = 0
        self._play_list_items = ["existing-track"]
        self.runtime_state = {"playing": True}

    def cancel_all_timer(self):
        self.cancel_count += 1
        if self.device.did in self.fail_cancel_for:
            raise RuntimeError(f"cannot cancel {self.device.did}")

    def update_playlist(self):
        self.update_count += 1


def _build_config(devices):
    return types.SimpleNamespace(devices=devices, group_list="")


def test_update_devices_reuses_same_did_without_playlist_side_effect(monkeypatch):
    monkeypatch.setattr("xiaomusic.device_manager.XiaoMusicDevice", _FakePlayer)
    _FakePlayer.fail_for = set()

    did = "981257654"
    dev1 = Device(did=did, device_id="dev-id-1", name="A")
    cfg = _build_config({did: dev1})
    mgr = DeviceManager(cfg, logging.getLogger("dm-test"), xiaomusic=object())

    mgr._update_devices()
    first = mgr.devices[did]
    tracks = first._play_list_items
    state = first.runtime_state

    dev2 = Device(did=did, device_id="dev-id-1", name="A2")
    cfg.devices = {did: dev2}
    mgr._update_devices()

    assert mgr.devices[did] is first
    assert first.cancel_count == 0
    assert first.update_count == 0
    assert first.device.name == "A2"
    assert first._play_list_items is tracks
    assert first.runtime_state is state


def test_update_devices_build_failure_preserves_config_runtime_and_player_state(monkeypatch):
    monkeypatch.setattr("xiaomusic.device_manager.XiaoMusicDevice", _FakePlayer)
    _FakePlayer.fail_for = set()

    first = Device(did="d1", device_id="dev-1", name="A")
    second = Device(did="d2", device_id="dev-2", name="B")
    cfg = _build_config({"d1": first, "d2": second})
    mgr = DeviceManager(cfg, logging.getLogger("dm-test"), xiaomusic=object())
    mgr._update_devices()
    old_mapping = dict(mgr.devices)
    old_config = cfg.devices
    old_values = {
        did: (player.device, player.group_name, player.config, player._play_list_items, player.runtime_state)
        for did, player in mgr.devices.items()
    }

    _FakePlayer.fail_for = {"d2"}
    candidate = {
        "d1": Device(did="d1", device_id="dev-1", name="A2"),
        "d2": Device(did="d2", device_id="dev-2-new", name="B2"),
    }
    with pytest.raises(RuntimeError):
        mgr._update_devices(candidate)

    assert mgr.devices == old_mapping
    assert cfg.devices is old_config
    assert cfg.devices == {"d1": first, "d2": second}
    for did, player in mgr.devices.items():
        assert (player.device, player.group_name, player.config, player._play_list_items, player.runtime_state) == old_values[did]
        assert player.cancel_count == 0
    _FakePlayer.fail_for = set()


@pytest.mark.asyncio
async def test_update_device_info_none_result_restores_current_did():
    cfg = _build_config({})

    class _Auth:
        _cur_did = "old-current"

        async def try_update_device_id(self):
            self._cur_did = "new-current"
            return None

    mgr = DeviceManager(cfg, logging.getLogger("dm-test"), xiaomusic=object())
    auth = _Auth()

    assert await mgr.update_device_info(auth) is False
    assert auth._cur_did == "old-current"


@pytest.mark.asyncio
async def test_new_configured_did_empty_live_stays_empty_then_self_heals(monkeypatch):
    monkeypatch.setattr("xiaomusic.device_manager.XiaoMusicDevice", _FakePlayer)
    cfg = types.SimpleNamespace(mi_did="new-did", devices={}, group_list="")

    class _Auth:
        _cur_did = None
        candidates = [
            {},
            {"new-did": Device(did="new-did", device_id="new-id", hardware="m", name="new")},
        ]

        async def try_update_device_id(self):
            return self.candidates.pop(0)

    mgr = DeviceManager(cfg, logging.getLogger("dm-test"), xiaomusic=object())
    auth = _Auth()

    assert await mgr.update_device_info(auth) is True
    assert cfg.devices == {}
    assert mgr.devices == {}
    assert await mgr.update_device_info(auth) is True
    assert set(cfg.devices) == {"new-did"}
    assert set(mgr.devices) == {"new-did"}


@pytest.mark.asyncio
async def test_update_device_info_rolls_config_back_when_runtime_build_fails(monkeypatch):
    monkeypatch.setattr("xiaomusic.device_manager.XiaoMusicDevice", _FakePlayer)
    _FakePlayer.fail_for = set()
    old = Device(did="d1", device_id="old-id", name="old")
    cfg = _build_config({"d1": old})
    mgr = DeviceManager(cfg, logging.getLogger("dm-test"), xiaomusic=object())
    mgr._update_devices()
    old_config = cfg.devices
    old_mapping = dict(mgr.devices)

    candidate = {"d1": Device(did="d1", device_id="new-id", name="new")}

    class _Auth:
        _cur_did = "old-current"

        async def try_update_device_id(self):
            self._cur_did = "new-current"
            return candidate

    auth = _Auth()
    old_cur_did = auth._cur_did
    _FakePlayer.fail_for = {"d1"}
    assert await mgr.update_device_info(auth) is False
    assert cfg.devices is old_config
    assert cfg.devices == {"d1": old}
    assert mgr.devices == old_mapping
    assert auth._cur_did == old_cur_did
    _FakePlayer.fail_for = set()


def test_removed_players_cleanup_is_best_effort_after_commit(monkeypatch):
    monkeypatch.setattr("xiaomusic.device_manager.XiaoMusicDevice", _FakePlayer)
    _FakePlayer.fail_for = set()
    _FakePlayer.fail_cancel_for = {"d1"}

    cfg = _build_config({
        "d1": Device(did="d1", device_id="dev-1", name="A"),
        "d2": Device(did="d2", device_id="dev-2", name="B"),
    })
    mgr = DeviceManager(cfg, logging.getLogger("dm-test"), xiaomusic=object())
    mgr._update_devices()
    old_d1, old_d2 = mgr.devices["d1"], mgr.devices["d2"]

    cfg.devices = {"new": Device(did="new", device_id="dev-new", name="N")}
    mgr._update_devices()

    assert set(mgr.devices) == {"new"}
    assert old_d1.cancel_count == 1
    assert old_d2.cancel_count == 1
    _FakePlayer.fail_cancel_for = set()


def test_update_devices_cancels_removed_player(monkeypatch):
    monkeypatch.setattr("xiaomusic.device_manager.XiaoMusicDevice", _FakePlayer)
    _FakePlayer.fail_for = set()

    did = "981257654"
    dev = Device(did=did, device_id="dev-id-1", name="A")
    cfg = _build_config({did: dev})
    mgr = DeviceManager(cfg, logging.getLogger("dm-test"), xiaomusic=object())

    mgr._update_devices()
    old = mgr.devices[did]
    cfg.devices = {}
    mgr._update_devices()

    assert did not in mgr.devices
    assert old.cancel_count == 1
