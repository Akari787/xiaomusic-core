from __future__ import annotations

from types import SimpleNamespace

import pytest

from xiaomusic.core.device.device_registry import DeviceRegistry
from xiaomusic.core.models.device import DeviceProfile, DeviceReachability
from xiaomusic.core.models.transport import TransportCapabilityMatrix


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
    device = SimpleNamespace(hardware="OH2P", name="speaker-1", host="192.168.7.88")
    player = SimpleNamespace(device=device, group_name="default")
    fake_xiaomusic = SimpleNamespace(device_manager=SimpleNamespace(devices={"d1": player}))

    registry = DeviceRegistry(fake_xiaomusic)
    capability = registry.get_capability_matrix("d1")

    assert capability.play == ["mina"]
    assert capability.tts == ["miio", "mina"]
