import sys
import types

import pytest

miservice_stub = types.ModuleType("miservice")
miservice_stub.MiAccount = object
miservice_stub.MiIOService = object
miservice_stub.MiNAService = object
miservice_stub.miio_command = lambda *args, **kwargs: None
sys.modules["miservice"] = miservice_stub

class _OpenCC:
    def __init__(self, *_args, **_kwargs):
        pass

    def convert(self, text):
        return text


opencc_stub = types.ModuleType("opencc")
opencc_stub.OpenCC = _OpenCC
sys.modules["opencc"] = opencc_stub

from xiaomusic.xiaomusic import XiaoMusic


@pytest.mark.asyncio
async def test_getalldevices_self_heal_when_devices_empty():
    calls = {"update": 0}

    class _Auth:
        async def mina_call(self, method, retry=1, ctx=""):
            assert method == "device_list"
            return [{"miotDID": "d1"}]

    class _DM:
        def __init__(self):
            self.devices = {}

        async def update_device_info(self, auth_manager):
            calls["update"] += 1
            self.devices = {"d1": object()}

    fake = types.SimpleNamespace(
        auth_manager=_Auth(),
        device_manager=_DM(),
        log=types.SimpleNamespace(warning=lambda *_args, **_kwargs: None),
    )

    out = await XiaoMusic.getalldevices(fake)
    assert len(out) == 1
    assert calls["update"] == 1


@pytest.mark.asyncio
async def test_getalldevices_rebuild_path_updates_device_manager():
    calls = {"mina": 0, "ensure": 0, "update": 0}

    class _Auth:
        async def mina_call(self, method, retry=1, ctx=""):
            calls["mina"] += 1
            if calls["mina"] == 1:
                raise RuntimeError("mina unavailable")
            return [{"miotDID": "d1"}]

        async def ensure_logged_in(self, force=False, reason="", prefer_refresh=False):
            calls["ensure"] += 1
            assert force is True
            assert prefer_refresh is True

    class _DM:
        def __init__(self):
            self.devices = {}

        async def update_device_info(self, auth_manager):
            calls["update"] += 1
            self.devices = {"d1": object()}

    fake = types.SimpleNamespace(
        auth_manager=_Auth(),
        device_manager=_DM(),
        log=types.SimpleNamespace(warning=lambda *_args, **_kwargs: None),
    )

    out = await XiaoMusic.getalldevices(fake)
    assert len(out) == 1
    assert calls["ensure"] == 1
    assert calls["update"] == 1
    assert calls["mina"] == 2
