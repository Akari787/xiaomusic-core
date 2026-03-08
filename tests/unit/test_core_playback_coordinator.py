from __future__ import annotations

import time

import pytest

from xiaomusic.core.coordinator.playback_coordinator import PlaybackCoordinator
from xiaomusic.core.delivery.delivery_adapter import DeliveryAdapter
from xiaomusic.core.models.device import DeviceProfile, DeviceReachability
from xiaomusic.core.models.media import MediaRequest, PreparedStream, ResolvedMedia
from xiaomusic.core.models.transport import TransportCapabilityMatrix
from xiaomusic.core.source.source_plugin import SourcePlugin
from xiaomusic.core.source.source_registry import SourceRegistry
from xiaomusic.core.transport.transport import Transport
from xiaomusic.core.transport.transport_policy import TransportPolicy
from xiaomusic.core.transport.transport_router import TransportRouter
from xiaomusic.core.device.device_registry import DeviceRegistry


class _CyclingSourcePlugin(SourcePlugin):
    name = "cycling"

    def __init__(self, outputs: list[ResolvedMedia]) -> None:
        self._outputs = outputs
        self.calls = 0

    def can_resolve(self, request: MediaRequest) -> bool:
        _ = request
        return True

    async def resolve(self, request: MediaRequest) -> ResolvedMedia:
        _ = request
        idx = min(self.calls, len(self._outputs) - 1)
        self.calls += 1
        return self._outputs[idx]


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
        return {
            "ip": "192.168.7.100",
            "local_reachable": True,
            "cloud_reachable": False,
            "last_probe_ts": int(time.time()),
            "device_id": device_id,
        }


class _RecordingTransportStub(Transport):
    name = "mina"

    def __init__(self) -> None:
        self.urls: list[str] = []

    async def play_url(self, device_id: str, prepared: PreparedStream) -> dict:
        _ = device_id
        self.urls.append(prepared.final_url)
        return {"ret": "OK", "url": prepared.final_url}

    async def stop(self, device_id: str) -> dict:
        return {"ret": "OK", "device_id": device_id}

    async def pause(self, device_id: str) -> dict:
        return {"ret": "OK", "device_id": device_id}

    async def tts(self, device_id: str, text: str) -> dict:
        return {"ret": "OK", "device_id": device_id, "text": text}

    async def set_volume(self, device_id: str, volume: int) -> dict:
        return {"ret": "OK", "device_id": device_id, "volume": volume}

    async def probe(self, device_id: str) -> dict:
        return {"local_reachable": True, "cloud_reachable": True, "device_id": device_id}


def _build_coordinator(
    plugin: SourcePlugin,
    *,
    transport: Transport | None = None,
    status_provider=None,
    proxy_builder=None,
) -> PlaybackCoordinator:
    source_registry = SourceRegistry()
    source_registry.register(plugin)

    device_registry = DeviceRegistry()
    device_registry.register_device(
        profile=DeviceProfile(did="d1", model="OH2P", name="speaker", group="default"),
        reachability=DeviceReachability(
            ip="192.168.7.10",
            local_reachable=False,
            cloud_reachable=False,
            last_probe_ts=1,
        ),
        capability_matrix=TransportCapabilityMatrix(
            play=["mina"],
            stop=["mina"],
            pause=["mina"],
            tts=["mina"],
            volume=["mina"],
            probe=["mina"],
        ),
    )

    router = TransportRouter(policy=TransportPolicy())
    router.register_transport(transport or _TransportStub())
    return PlaybackCoordinator(
        source_registry=source_registry,
        device_registry=device_registry,
        delivery_adapter=DeliveryAdapter(expiry_skew_seconds=0, proxy_url_builder=proxy_builder),
        transport_router=router,
        max_resolve_retry=1,
        playback_status_provider=status_provider,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_playback_coordinator_play_success_and_retry_on_expired():
    plugin = _CyclingSourcePlugin(
        outputs=[
            ResolvedMedia(
                media_id="m-expired",
                source="direct_url",
                title="expired",
                stream_url="https://example.com/expired.mp3",
                expires_at=int(time.time()) - 1,
                is_live=False,
            ),
            ResolvedMedia(
                media_id="m-ok",
                source="direct_url",
                title="ok",
                stream_url="https://example.com/ok.mp3",
                expires_at=None,
                is_live=False,
            ),
        ]
    )
    coordinator = _build_coordinator(plugin)

    out = await coordinator.play(
        MediaRequest(
            request_id="r1",
            source_hint="cycling",
            query="https://example.com/ok.mp3",
            device_id="d1",
        )
    )

    assert out["ok"] is True
    assert out["transport"] == "mina"
    assert plugin.calls == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_playback_coordinator_control_actions_and_probe_update():
    plugin = _CyclingSourcePlugin(
        outputs=[
            ResolvedMedia(
                media_id="m-ok",
                source="direct_url",
                title="ok",
                stream_url="https://example.com/ok.mp3",
                expires_at=None,
                is_live=False,
            )
        ]
    )
    coordinator = _build_coordinator(plugin)

    stop_out = await coordinator.stop("d1")
    pause_out = await coordinator.pause("d1")
    tts_out = await coordinator.tts("d1", "hello")
    volume_out = await coordinator.set_volume("d1", 33)
    probe_out = await coordinator.probe("d1")

    assert stop_out["transport"] == "mina"
    assert pause_out["transport"] == "mina"
    assert tts_out["dispatch"].data["text"] == "hello"
    assert volume_out["dispatch"].data["volume"] == 33
    assert probe_out["dispatch"].data["local_reachable"] is True
    assert probe_out["reachability"].local_reachable is True
    assert probe_out["reachability"].last_probe_ts >= 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_playback_coordinator_fallback_to_proxy_when_direct_not_started():
    plugin = _CyclingSourcePlugin(
        outputs=[
            ResolvedMedia(
                media_id="m-site",
                source="site_media",
                title="yt",
                stream_url="https://googlevideo.example/v1.mp4",
                expires_at=None,
                is_live=False,
            )
        ]
    )
    transport = _RecordingTransportStub()

    status_values = iter([{"status": 0}, {"status": 1}])

    async def _status_provider(device_id: str) -> dict:
        _ = device_id
        return next(status_values)

    coordinator = _build_coordinator(
        plugin,
        transport=transport,
        status_provider=_status_provider,
        proxy_builder=lambda url, name: f"http://127.0.0.1:58090/proxy?name={name}",
    )

    out = await coordinator.play(
        MediaRequest(
            request_id="r-proxy",
            source_hint="site_media",
            query="https://www.youtube.com/watch?v=iPnaF8Ngk3Q",
            device_id="d1",
            context={
                "prefer_proxy": False,
                "confirm_start_delay_ms": 0,
                "confirm_start_interval_ms": 0,
                "confirm_start_retries": 0,
            },
        )
    )

    assert out["ok"] is True
    assert len(transport.urls) == 2
    assert transport.urls[0].startswith("https://")
    assert transport.urls[1].startswith("http://127.0.0.1:58090/proxy")
    assert out["prepared_stream"].is_proxy is True
    assert out["outcome"].fallback_triggered is True
