from __future__ import annotations

import time

import pytest

from xiaomusic.adapters.sources.http_url_source_plugin import HttpUrlSourcePlugin
from xiaomusic.adapters.sources.jellyfin_source_plugin import JellyfinSourcePlugin
from xiaomusic.core.delivery.delivery_adapter import DeliveryAdapter
from xiaomusic.core.models.media import MediaRequest
from xiaomusic.core.source.source_registry import SourceRegistry


@pytest.mark.unit
@pytest.mark.asyncio
async def test_http_url_source_plugin_resolve_with_context_fields():
    plugin = HttpUrlSourcePlugin()
    req = MediaRequest(
        request_id="r1",
        source_hint="http_url",
        query="https://example.com/a.mp3",
        device_id="d1",
        context={
            "title": "hello",
            "headers": {"Auth": "x"},
            "expires_at": str(int(time.time()) + 3600),
            "is_live": True,
        },
    )

    out = await plugin.resolve(req)
    prepared = DeliveryAdapter().prepare(out)

    assert out.source == "http_url"
    assert out.title == "hello"
    assert out.stream_url == "https://example.com/a.mp3"
    assert out.headers == {"Auth": "x"}
    assert (out.expires_at or 0) > int(time.time())
    assert out.is_live is True
    assert prepared.final_url == "https://example.com/a.mp3"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_jellyfin_source_plugin_resolve_from_payload():
    plugin = JellyfinSourcePlugin(lambda payload: payload["url"])
    req = MediaRequest(
        request_id="r2",
        source_hint="jellyfin",
        query="jellyfin://item/1",
        device_id="d1",
        context={
            "source_payload": {
                "id": "jf-1",
                "source": "jellyfin",
                "title": "jf-title",
                "url": "http://192.168.7.4:30013/Audio/xxx/stream.mp3",
            }
        },
    )

    out = await plugin.resolve(req)
    prepared = DeliveryAdapter().prepare(out)

    assert out.media_id == "jf-1"
    assert out.source == "jellyfin"
    assert out.title == "jf-title"
    assert out.stream_url.startswith("http://192.168.7.4")
    assert prepared.final_url.startswith("http://192.168.7.4")


@pytest.mark.unit
def test_source_registry_prefers_source_hint_then_can_resolve():
    registry = SourceRegistry()
    http_plugin = HttpUrlSourcePlugin()
    jf_plugin = JellyfinSourcePlugin(lambda payload: str(payload.get("url") or ""))
    registry.register(http_plugin)
    registry.register(jf_plugin)

    req_hint = MediaRequest(
        request_id="r3",
        source_hint="jellyfin",
        query="dummy",
        context={"source_payload": {"source": "jellyfin", "url": "http://x/y.mp3"}},
    )
    picked_by_hint = registry.get_plugin(req_hint.source_hint, req_hint)
    assert picked_by_hint.name == "jellyfin"

    req_fallback = MediaRequest(
        request_id="r4",
        source_hint=None,
        query="https://example.com/b.mp3",
    )
    picked_by_match = registry.get_plugin(req_fallback.source_hint, req_fallback)
    assert picked_by_match.name == "http_url"
