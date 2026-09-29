from __future__ import annotations

from types import SimpleNamespace

import pytest

from xiaomusic.core.device.device_registry import DeviceRegistry
from xiaomusic.core.errors import DeviceNotFoundError
from xiaomusic.core.models.device import DeviceProfile, DeviceReachability
from xiaomusic.core.models.transport import TransportCapabilityMatrix


def _registered_registry(player=None):
    if player is None:
        player = SimpleNamespace(
            device=SimpleNamespace(hardware="OH2P", name="speaker-1", host="192.168.7.88"),
            group_name="default",
        )
    fake_xiaomusic = SimpleNamespace(
        device_manager=SimpleNamespace(devices={"d1": player})
    )
    registry = DeviceRegistry(fake_xiaomusic)
    registry.get_profile("d1")
    return registry, fake_xiaomusic, player


@pytest.mark.unit
def test_device_registry_update_reachability_updates_snapshot():
    registry = DeviceRegistry()
    registry.register_device(
        profile=DeviceProfile(did="d1", model="m", name="n", group="g"),
        reachability=DeviceReachability(
            ip="192.168.1.2",
            local_reachable=False,
            cloud_reachable=False,
            last_probe_ts=1,
        ),
        capability_matrix=TransportCapabilityMatrix(play=["mina"]),
    )

    updated = registry.update_reachability(
        "d1",
        {
            "ip": "192.168.1.20",
            "local_reachable": True,
            "cloud_reachable": True,
            "last_probe_ts": 99,
        },
    )

    assert updated.ip == "192.168.1.20"
    assert updated.local_reachable is True
    assert updated.cloud_reachable is True
    assert updated.last_probe_ts == 99


@pytest.mark.unit
def test_device_registry_hydrate_legacy_uses_mina_only_for_play():
    registry, _fake, _player = _registered_registry()
    capability = registry.get_capability_matrix("d1")

    assert capability.play == ["mina"]
    assert capability.tts == ["miio", "mina"]
    assert registry.get_reachability("d1").local_reachable is False
    assert registry.get_reachability("d1").cloud_reachable is False


@pytest.mark.unit
def test_cached_registry_entry_is_invalidated_when_legacy_device_is_removed():
    registry, fake_xiaomusic, _player = _registered_registry()
    assert registry.get_profile("d1").did == "d1"

    fake_xiaomusic.device_manager.devices.clear()

    with pytest.raises(DeviceNotFoundError):
        registry.get_profile("d1")
    with pytest.raises(DeviceNotFoundError):
        registry.get_reachability("d1")
    assert "d1" not in registry._profiles
    assert "d1" not in registry._reachability
    assert "d1" not in registry._capability


@pytest.mark.unit
def test_profile_refreshes_runtime_ip_before_probe():
    registry, _fake, player = _registered_registry()
    player.device.host = "new-runtime-ip"

    registry.get_profile("d1")

    assert registry.get_reachability("d1").ip == "new-runtime-ip"


@pytest.mark.unit
def test_profile_refreshes_runtime_metadata_without_resetting_probe_reachability():
    registry, _fake, player = _registered_registry()
    registry.update_reachability(
        "d1",
        {"ip": "probe-ip", "local_reachable": True, "cloud_reachable": False, "last_probe_ts": 9},
    )

    player.device.hardware = "new-model"
    player.device.name = "new-name"
    player.device.host = "runtime-ip"
    player.group_name = "new-group"

    profile = registry.get_profile("d1")
    assert (profile.model, profile.name, profile.group) == ("new-model", "new-name", "new-group")
    reachability = registry.get_reachability("d1")
    assert (reachability.ip, reachability.local_reachable, reachability.cloud_reachable) == (
        "probe-ip", True, False
    )


@pytest.mark.unit
def test_invalidate_device_clears_all_cached_state():
    registry, fake_xiaomusic, _player = _registered_registry()
    registry.invalidate_device("d1")
    fake_xiaomusic.device_manager.devices.clear()

    with pytest.raises(DeviceNotFoundError):
        registry.get_profile("d1")
