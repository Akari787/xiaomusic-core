from __future__ import annotations

from dataclasses import dataclass

import pytest

from xiaomusic.adapters.sources.network_audio_source_plugin import NetworkAudioSourcePlugin
from xiaomusic.core.delivery.delivery_adapter import DeliveryAdapter
from xiaomusic.core.models.media import MediaRequest
from xiaomusic.network_audio.contracts import ResolveResult


@dataclass
class _ResolverStub:
    out: ResolveResult

    def resolve(self, url: str, timeout_seconds: float = 8) -> ResolveResult:
        _ = (url, timeout_seconds)
        return self.out


@pytest.mark.unit
@pytest.mark.asyncio
async def test_network_audio_source_plugin_resolve_then_delivery_prepare():
    plugin = NetworkAudioSourcePlugin(
        resolver=_ResolverStub(
            ResolveResult(
                ok=True,
                source_url="https://cdn.example.com/audio.m4a",
                title="network-audio-title",
                is_live=False,
                container_hint="m4a",
                meta={"raw_id": "na-1"},
            )
        )
    )
    req = MediaRequest(
        request_id="req-na",
        source_hint="network_audio",
        query="https://www.youtube.com/watch?v=iPnaF8Ngk3Q",
    )

    resolved = await plugin.resolve(req)
    prepared = DeliveryAdapter().prepare(resolved)

    assert resolved.source == "network_audio"
    assert resolved.media_id == "na-1"
    assert prepared.final_url == "https://cdn.example.com/audio.m4a"
