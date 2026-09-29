import types

import pytest

from xiaomusic.auth import AuthManager
from xiaomusic.config import Device


class _Log:
    def __init__(self):
        self.warnings = []
        self.infos = []

    def info(self, message, *_args, **_kwargs):
        self.infos.append(str(message))

    def warning(self, message, *_args, **_kwargs):
        self.warnings.append(str(message))


def _manager(config, live):
    manager = AuthManager.__new__(AuthManager)
    manager.config = config
    manager.log = _Log()
    manager._cur_did = "old-current"
    manager.mina_service = types.SimpleNamespace(device_list=lambda: live())
    return manager


@pytest.mark.asyncio
async def test_existing_snapshot_and_empty_live_list_are_preserved():
    did = "d1"
    config = types.SimpleNamespace(
        mi_did=did,
        devices={did: Device(did=did, device_id="old", hardware="m", name="old")},
    )
    stable_before = config.devices
    manager = _manager(config, _empty)

    candidate = await manager.try_update_device_id()

    assert candidate[did].device_id == "old"
    assert config.devices is stable_before
    assert config.devices[did].device_id == "old"
    assert manager._cur_did == did


@pytest.mark.asyncio
async def test_partial_live_list_preserves_missing_target_and_updates_present_target():
    d1 = Device(did="d1", device_id="old-1", hardware="m1", name="old-1")
    d2 = Device(did="d2", device_id="old-2", hardware="m2", name="old-2")
    config = types.SimpleNamespace(mi_did="d2,d1,d2", devices={"d1": d1, "d2": d2})
    manager = _manager(config, _partial)

    candidate = await manager.try_update_device_id()

    assert list(candidate) == ["d2", "d1"]
    assert candidate["d1"].device_id == "new-1"
    assert candidate["d1"].name == "new name"
    assert candidate["d2"].device_id == "old-2"
    assert manager._cur_did == "d1"


@pytest.mark.asyncio
async def test_partial_live_missing_current_keeps_current_stable_did():
    config = types.SimpleNamespace(
        mi_did="d1,d2",
        devices={
            "d1": Device(did="d1", device_id="old-1", hardware="m1"),
            "d2": Device(did="d2", device_id="old-2", hardware="m2"),
        },
    )
    manager = _manager(config, _only_d2)
    manager._cur_did = "d1"

    await manager.try_update_device_id()

    assert manager._cur_did == "d1"


@pytest.mark.asyncio
async def test_explicit_mi_did_removal_of_current_uses_next_stable_in_order():
    config = types.SimpleNamespace(
        mi_did="d2,d3",
        devices={
            "d1": Device(did="d1", device_id="old-1", hardware="m1"),
            "d2": Device(did="d2", device_id="old-2", hardware="m2"),
        },
    )
    manager = _manager(config, _empty)
    manager._cur_did = "d1"

    await manager.try_update_device_id()

    assert manager._cur_did == "d2"


@pytest.mark.asyncio
async def test_explicit_mi_did_removal_is_reflected_in_snapshot():
    config = types.SimpleNamespace(
        mi_did="d1",
        devices={
            "d1": Device(did="d1", device_id="one"),
            "removed": Device(did="removed", device_id="two"),
        },
    )
    manager = _manager(config, _one)

    candidate = await manager.try_update_device_id()

    assert set(candidate) == {"d1"}


@pytest.mark.asyncio
async def test_new_configured_did_without_live_or_snapshot_is_skipped():
    config = types.SimpleNamespace(mi_did="new-did", devices={})
    manager = _manager(config, _empty)

    candidate = await manager.try_update_device_id()

    assert candidate == {}
    assert config.devices == {}


@pytest.mark.asyncio
async def test_empty_mi_did_explicitly_removes_all_snapshot_devices():
    config = types.SimpleNamespace(
        mi_did="",
        devices={"d1": Device(did="d1", device_id="one")},
    )
    manager = _manager(config, _empty)

    candidate = await manager.try_update_device_id()

    # Empty mi_did is an explicit empty target set, not an invalid input.
    assert candidate == {}


@pytest.mark.asyncio
async def test_mixed_malformed_partial_live_entries_are_skipped_without_pollution():
    stable = Device(did="stable", device_id="old", hardware="m", name="old")
    config = types.SimpleNamespace(
        mi_did="stable,new,complete",
        devices={"stable": stable},
    )
    manager = _manager(config, _mixed_invalid)

    candidate = await manager.try_update_device_id()

    assert candidate["stable"].device_id == "old"
    assert "new" not in candidate
    assert candidate["complete"].device_id == "new-complete"
    assert candidate["complete"].hardware == "m-complete"
    assert config.devices == {"stable": stable}
    assert len(manager.log.warnings) >= 3


@pytest.mark.asyncio
async def test_incomplete_first_complete_second_accepts_complete_record():
    config = types.SimpleNamespace(mi_did="new", devices={})
    manager = _manager(config, _incomplete_then_complete)

    candidate = await manager.try_update_device_id()

    assert candidate["new"].device_id == "accepted-id"
    assert candidate["new"].hardware == "accepted-model"


@pytest.mark.asyncio
async def test_complete_first_duplicate_second_keeps_first_record():
    config = types.SimpleNamespace(mi_did="new", devices={})
    manager = _manager(config, _complete_then_duplicate)

    candidate = await manager.try_update_device_id()

    assert candidate["new"].device_id == "first-id"
    assert candidate["new"].hardware == "first-model"


@pytest.mark.asyncio
async def test_non_list_payload_does_not_change_stable_snapshot():
    stable = Device(did="d1", device_id="old", hardware="m", name="old")
    config = types.SimpleNamespace(mi_did="d1", devices={"d1": stable})
    manager = _manager(config, _invalid_payload)

    assert await manager.try_update_device_id() is None
    assert config.devices == {"d1": stable}


async def _empty():
    return []


async def _partial():
    return [{"miotDID": "d1", "deviceID": "new-1", "hardware": "m1", "alias": "new name"}]


async def _one():
    return [{"miotDID": "d1", "deviceID": "one", "hardware": "m", "alias": "one"}]


async def _only_d2():
    return [{"miotDID": "d2", "deviceID": "new-2", "hardware": "m2"}]


async def _mixed_invalid():
    return [
        "not-an-object",
        {"miotDID": "stable", "hardware": "m"},
        {"miotDID": "new", "deviceID": "new-id"},
        {"miotDID": "complete", "deviceID": "new-complete", "hardware": "m-complete"},
        {"miotDID": "complete", "deviceID": "duplicate", "hardware": "m-duplicate"},
    ]


async def _invalid_payload():
    return {"miotDID": "d1"}


async def _incomplete_then_complete():
    return [
        {"miotDID": "new", "hardware": "missing-id"},
        {"miotDID": "new", "deviceID": "accepted-id", "hardware": "accepted-model"},
    ]


async def _complete_then_duplicate():
    return [
        {"miotDID": "new", "deviceID": "first-id", "hardware": "first-model"},
        {"miotDID": "new", "deviceID": "second-id", "hardware": "second-model"},
    ]
