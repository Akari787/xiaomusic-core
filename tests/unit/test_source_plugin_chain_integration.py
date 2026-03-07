from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from xiaomusic.adapters.sources.direct_url_source_plugin import DirectUrlSourcePlugin
from xiaomusic.adapters.sources.jellyfin_source_plugin import JellyfinSourcePlugin
from xiaomusic.adapters.sources.local_library_source_plugin import LocalLibrarySourcePlugin
from xiaomusic.adapters.sources.site_media_source_plugin import SiteMediaSourcePlugin
from xiaomusic.core.coordinator.playback_coordinator import PlaybackCoordinator
from xiaomusic.core.delivery.delivery_adapter import DeliveryAdapter
from xiaomusic.core.device.device_registry import DeviceRegistry
from xiaomusic.core.models.device import DeviceProfile, DeviceReachability
from xiaomusic.core.models.media import MediaRequest, PreparedStream
from xiaomusic.core.models.transport import TransportCapabilityMatrix
from xiaomusic.core.source.source_registry import SourceRegistry
from xiaomusic.core.transport.transport import Transport
from xiaomusic.core.transport.transport_policy import TransportPolicy
from xiaomusic.core.transport.transport_router import TransportRouter
from xiaomusic.network_audio.contracts import ResolveResult


class _TransportStub(Transport):
    name = "mina"

    async def play_url(self, device_id: str, prepared: PreparedStream) -> dict:
        return {"ret": "OK", "device_id": device_id, "url": prepared.final_url}

    async def stop(self, device_id: str) -> dict:
        return {"ret": "OK", "device_id": device_id}

    async def pause(self, device_id: str) -> dict:
        return {"ret": "OK", "device_id": device_id}

    async def tts(self, device_id: str, text: str) -> dict:
        return {"ret": "OK", "device_id": device_id, "text": text}

    async def set_volume(self, device_id: str, volume: int) -> dict:
        return {"ret": "OK", "device_id": device_id, "volume": volume}

    async def probe(self, device_id: str) -> dict:
        return {"local_reachable": True, "cloud_reachable": False, "device_id": device_id}


class _ResolverStub:
    def resolve(self, url: str, timeout_seconds: float = 8) -> ResolveResult:
        _ = (url, timeout_seconds)
        return ResolveResult(
            ok=True,
            source_url="https://cdn.example.com/network-audio.m4a",
            title="network-audio",
            is_live=False,
            container_hint="m4a",
            meta={"raw_id": "na-1"},
        )


class _LocalLibraryStub:
    all_music = {"local-song": "/music/local-song.mp3"}

    def is_web_music(self, name: str) -> bool:
        _ = name
        return False

    def get_filename(self, name: str) -> str:
        return self.all_music.get(name, "")

    def _get_file_url(self, filepath: str) -> str:
        return f"http://127.0.0.1:58090/music/{Path(filepath).name}"

    def searchmusic(self, query: str):
        return ["local-song"] if query else []


def _build_coordinator() -> PlaybackCoordinator:
    source_registry = SourceRegistry()
    source_registry.register(JellyfinSourcePlugin(lambda payload: str(payload.get("url") or "")))
    source_registry.register(DirectUrlSourcePlugin())
    source_registry.register(LocalLibrarySourcePlugin(_LocalLibraryStub()))
    source_registry.register(SiteMediaSourcePlugin(resolver=cast(Any, _ResolverStub())))

    device_registry = DeviceRegistry()
    device_registry.register_device(
        profile=DeviceProfile(did="d1", model="OH2P", name="speaker", group="default"),
        reachability=DeviceReachability(ip="192.168.7.10", local_reachable=True, cloud_reachable=True, last_probe_ts=1),
        capability_matrix=TransportCapabilityMatrix(play=["mina"]),
    )

    router = TransportRouter(policy=TransportPolicy())
    router.register_transport(_TransportStub())
    return PlaybackCoordinator(
        source_registry=source_registry,
        device_registry=device_registry,
        delivery_adapter=DeliveryAdapter(expiry_skew_seconds=0),
        transport_router=router,
        max_resolve_retry=0,
    )


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("media_request", "expected_source"),
    [
        (
            MediaRequest(
                request_id="r-jf",
                source_hint="jellyfin",
                query="legacy://jellyfin",
                device_id="d1",
                context={"source_payload": {"source": "jellyfin", "url": "http://192.168.7.4:30013/Audio/id/stream.mp3"}},
            ),
            "jellyfin",
        ),
        (
            MediaRequest(
                request_id="r-http",
                source_hint="direct_url",
                query="https://example.com/a.mp3",
                device_id="d1",
            ),
            "direct_url",
        ),
        (
            MediaRequest(
                request_id="r-local",
                source_hint="local_library",
                query="local-song",
                device_id="d1",
            ),
            "local_library",
        ),
        (
            MediaRequest(
                request_id="r-na",
                source_hint="site_media",
                query="https://www.youtube.com/watch?v=iPnaF8Ngk3Q",
                device_id="d1",
            ),
            "site_media",
        ),
    ],
)
async def test_unified_chain_for_all_source_plugins(media_request: MediaRequest, expected_source: str):
    coordinator = _build_coordinator()

    out = await coordinator.play(media_request, device_id="d1")

    assert out["ok"] is True
    assert out["prepared_stream"].source == expected_source
    assert out["dispatch"].transport == "mina"
