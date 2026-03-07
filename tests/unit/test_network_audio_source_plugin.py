from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import pytest

from xiaomusic.adapters.sources.network_audio_source_plugin import NetworkAudioSourcePlugin
from xiaomusic.core.delivery.delivery_adapter import DeliveryAdapter
from xiaomusic.core.errors.source_errors import SourceResolveError
from xiaomusic.core.models.media import MediaRequest
from xiaomusic.network_audio.contracts import ResolveResult


@dataclass
class _ResolverStub:
    out: ResolveResult

    def resolve(self, url: str, timeout_seconds: float = 8) -> ResolveResult:
        _ = (url, timeout_seconds)
        return self.out


@dataclass
class _ResolverFailStub:
    def resolve(self, url: str, timeout_seconds: float = 8) -> ResolveResult:
        _ = (url, timeout_seconds)
        return ResolveResult(
            ok=False,
            source_url="",
            title="",
            is_live=False,
            container_hint="unknown",
            error_code="E_RESOLVE_TIMEOUT",
            error_message="timeout",
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_network_audio_source_plugin_resolve_then_delivery_prepare():
    plugin = NetworkAudioSourcePlugin(
        resolver=cast(
            Any,
            _ResolverStub(
            ResolveResult(
                ok=True,
                source_url="https://cdn.example.com/audio.m4a",
                title="network-audio-title",
                is_live=False,
                container_hint="m4a",
                meta={"raw_id": "na-1"},
            )
            ),
        ),
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


@pytest.mark.unit
@pytest.mark.asyncio
async def test_network_audio_source_plugin_resolve_failure_raises_error():
    plugin = NetworkAudioSourcePlugin(resolver=cast(Any, _ResolverFailStub()))
    req = MediaRequest(
        request_id="req-na-fail",
        source_hint="network_audio",
        query="https://www.youtube.com/watch?v=iPnaF8Ngk3Q",
    )

    with pytest.raises(SourceResolveError):
        await plugin.resolve(req)
