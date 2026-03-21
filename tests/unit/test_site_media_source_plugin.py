from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import pytest

from xiaomusic.adapters.sources.site_media_source_plugin import SiteMediaSourcePlugin
from xiaomusic.core.delivery.delivery_adapter import DeliveryAdapter
from xiaomusic.core.errors.source_errors import SourceResolveError
from xiaomusic.core.models.media import MediaRequest
from xiaomusic.relay.contracts import ResolveResult


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
async def test_site_media_source_plugin_resolve_then_delivery_prepare():
    plugin = SiteMediaSourcePlugin(
        resolver=cast(
            Any,
            _ResolverStub(
                ResolveResult(
                    ok=True,
                    source_url="https://cdn.example.com/audio.m4a",
                    title="site-media-title",
                    is_live=False,
                    container_hint="m4a",
                    meta={"raw_id": "sm-1"},
                )
            ),
        ),
    )
    req = MediaRequest(
        request_id="req-sm",
        source_hint="site_media",
        query="https://www.youtube.com/watch?v=iPnaF8Ngk3Q",
    )

    resolved = await plugin.resolve(req)
    prepared = DeliveryAdapter().prepare(resolved)

    assert resolved.source == "site_media"
    assert resolved.media_id == "sm-1"
    assert prepared.final_url == "https://cdn.example.com/audio.m4a"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_site_media_source_plugin_resolve_failure_raises_error():
    plugin = SiteMediaSourcePlugin(resolver=cast(Any, _ResolverFailStub()))
    req = MediaRequest(
        request_id="req-sm-fail",
        source_hint="site_media",
        query="https://www.youtube.com/watch?v=iPnaF8Ngk3Q",
    )

    with pytest.raises(SourceResolveError):
        await plugin.resolve(req)
