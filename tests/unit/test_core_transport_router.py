from __future__ import annotations

import pytest

from xiaomusic.core.errors.transport_errors import TransportError
from xiaomusic.core.models.device import DeviceProfile
from xiaomusic.core.models.media import PreparedStream
from xiaomusic.core.models.transport import TransportCapabilityMatrix
from xiaomusic.core.transport.transport import Transport
from xiaomusic.core.transport.transport_policy import TransportPolicy
from xiaomusic.core.transport.transport_router import TransportRouter


class _TransportStub(Transport):
    def __init__(self, name: str, fail_actions: set[str] | None = None) -> None:
        self.name = name
        self.fail_actions = fail_actions or set()

    async def play_url(self, device_id: str, prepared: PreparedStream) -> dict:
        _ = (device_id, prepared)
        if "play" in self.fail_actions:
            raise RuntimeError("play failed")
        return {"ret": "OK"}

    async def stop(self, device_id: str) -> dict:
        _ = device_id
        if "stop" in self.fail_actions:
            raise RuntimeError("stop failed")
        return {"ret": "OK"}

    async def previous(self, device_id: str) -> dict:
        _ = device_id
        if "previous" in self.fail_actions:
            raise RuntimeError("previous failed")
        return {"ret": "OK"}

    async def next(self, device_id: str) -> dict:
        _ = device_id
        if "next" in self.fail_actions:
            raise RuntimeError("next failed")
        return {"ret": "OK"}

    async def pause(self, device_id: str) -> dict:
        _ = device_id
        if "pause" in self.fail_actions:
            raise RuntimeError("pause failed")
        return {"ret": "OK"}

    async def tts(self, device_id: str, text: str) -> dict:
        _ = (device_id, text)
        if "tts" in self.fail_actions:
            raise RuntimeError("tts failed")
        return {"ret": "OK"}

    async def set_volume(self, device_id: str, volume: int) -> dict:
        _ = (device_id, volume)
        if "volume" in self.fail_actions:
            raise RuntimeError("volume failed")
        return {"ret": "OK"}

    async def probe(self, device_id: str) -> dict:
        _ = device_id
        if "probe" in self.fail_actions:
            raise RuntimeError("probe failed")
        return {"local_reachable": True, "cloud_reachable": False}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_transport_router_uses_capability_policy_intersection_for_play():
    router = TransportRouter(policy=TransportPolicy({"play": ["miio", "mina"]}))
    router.register_transport(_TransportStub("miio", fail_actions={"play"}))
    router.register_transport(_TransportStub("mina"))

    profile = DeviceProfile(did="d1", model="m", name="n", group="g")
    capability = TransportCapabilityMatrix(play=["mina"])
    prepared = PreparedStream(final_url="https://example.com/a.mp3", source="direct_url")

    out = await router.dispatch_play_url(prepared=prepared, profile=profile, capability_matrix=capability)

    assert out.ok is True
    assert out.transport == "mina"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_transport_router_fallback_for_tts_inside_candidates():
    router = TransportRouter(policy=TransportPolicy({"tts": ["miio", "mina"]}))
    router.register_transport(_TransportStub("miio", fail_actions={"tts"}))
    router.register_transport(_TransportStub("mina"))

    profile = DeviceProfile(did="d1", model="m", name="n", group="g")
    capability = TransportCapabilityMatrix(tts=["miio", "mina"])

    out = await router.dispatch(
        action="tts",
        device_id="d1",
        profile=profile,
        capability_matrix=capability,
        text="hello",
    )

    assert out.ok is True
    assert out.transport == "mina"
    assert out.errors


@pytest.mark.unit
@pytest.mark.asyncio
async def test_transport_router_routes_previous_with_capability_policy_intersection():
    router = TransportRouter(policy=TransportPolicy({"previous": ["miio", "mina"]}))
    router.register_transport(_TransportStub("miio"))
    router.register_transport(_TransportStub("mina"))

    profile = DeviceProfile(did="d1", model="m", name="n", group="g")
    capability = TransportCapabilityMatrix(previous=["miio"])

    out = await router.dispatch(
        action="previous",
        device_id="d1",
        profile=profile,
        capability_matrix=capability,
    )

    assert out.ok is True
    assert out.transport == "miio"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_transport_router_raises_when_no_candidates():
    router = TransportRouter(policy=TransportPolicy({"play": ["miio"]}))
    router.register_transport(_TransportStub("miio"))

    profile = DeviceProfile(did="d1", model="m", name="n", group="g")
    capability = TransportCapabilityMatrix(play=["mina"])
    prepared = PreparedStream(final_url="https://example.com/a.mp3", source="direct_url")

    with pytest.raises(TransportError):
        await router.dispatch_play_url(prepared=prepared, profile=profile, capability_matrix=capability)
