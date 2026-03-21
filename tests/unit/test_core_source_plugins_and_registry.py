from __future__ import annotations

import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast

import pytest

from xiaomusic.adapters.sources.direct_url_source_plugin import DirectUrlSourcePlugin
from xiaomusic.adapters.sources.jellyfin_source_plugin import JellyfinSourcePlugin
from xiaomusic.adapters.sources.local_library_source_plugin import LocalLibrarySourcePlugin
from xiaomusic.adapters.sources.site_media_source_plugin import SiteMediaSourcePlugin
from xiaomusic.core.delivery.delivery_adapter import DeliveryAdapter
from xiaomusic.core.models.media import MediaRequest
from xiaomusic.core.source.source_registry import SourceRegistry
from xiaomusic.relay.contracts import ResolveResult


@pytest.mark.unit
@pytest.mark.asyncio
async def test_direct_url_source_plugin_resolve_with_context_fields():
    plugin = DirectUrlSourcePlugin()
    req = MediaRequest(
        request_id="r1",
        source_hint="direct_url",
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

    assert out.source == "direct_url"
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
    direct_plugin = DirectUrlSourcePlugin()
    jf_plugin = JellyfinSourcePlugin(lambda payload: str(payload.get("url") or ""))
    site_plugin = SiteMediaSourcePlugin(resolver=cast(Any, _ResolverStub()))
    local_plugin = LocalLibrarySourcePlugin(_LocalMusicLibraryStub())
    registry.register(direct_plugin)
    registry.register(jf_plugin)
    registry.register(local_plugin)
    registry.register(site_plugin)

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
    assert picked_by_match.name == "direct_url"

    req_auto_jf = MediaRequest(
        request_id="r4-jf",
        source_hint=None,
        query="legacy://jellyfin",
        context={"source_payload": {"source": "jellyfin", "url": "http://x/jf.mp3"}},
    )
    assert registry.get_plugin(req_auto_jf.source_hint, req_auto_jf).name == "jellyfin"

    req_na = MediaRequest(
        request_id="r5",
        source_hint="site_media",
        query="https://www.youtube.com/watch?v=iPnaF8Ngk3Q",
    )
    assert registry.get_plugin(req_na.source_hint, req_na).name == "site_media"

    req_local = MediaRequest(
        request_id="r6",
        source_hint="local_library",
        query="/music/test.mp3",
    )
    assert registry.get_plugin(req_local.source_hint, req_local).name == "local_library"

    req_local_compat = MediaRequest(
        request_id="r6c",
        source_hint="local_music",
        query="/music/test.mp3",
    )
    assert registry.get_plugin(req_local_compat.source_hint, req_local_compat).name == "local_library"

    req_auto_local = MediaRequest(
        request_id="r6-auto",
        source_hint=None,
        query="/music/test.mp3",
    )
    assert registry.get_plugin(req_auto_local.source_hint, req_auto_local).name == "local_library"

    req_direct_compat = MediaRequest(
        request_id="r4c",
        source_hint="http_url",
        query="https://example.com/b.mp3",
    )
    assert registry.get_plugin(req_direct_compat.source_hint, req_direct_compat).name == "direct_url"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_local_library_source_plugin_resolve_by_track_name_and_path():
    with TemporaryDirectory() as tmp_dir:
        p = Path(tmp_dir) / "hello.mp3"
        p.write_bytes(b"demo")
        library = _LocalMusicLibraryStub(track_name="hello", file_path=str(p))
        plugin = LocalLibrarySourcePlugin(library)

        by_name = await plugin.resolve(
            MediaRequest(request_id="r7", source_hint="local_library", query="hello")
        )
        by_path = await plugin.resolve(
            MediaRequest(request_id="r8", source_hint="local_library", query=str(p))
        )

        assert by_name.source == "local_library"
        assert by_name.stream_url.endswith("/music/hello.mp3")
        assert by_path.stream_url.endswith("/music/hello.mp3")


class _ResolverStub:
    def resolve(self, url: str, timeout_seconds: float = 8) -> ResolveResult:
        _ = (url, timeout_seconds)
        return ResolveResult(
            ok=True,
            source_url="https://cdn.example.com/audio.m4a",
            title="na",
            is_live=False,
            container_hint="m4a",
            meta={"raw_id": "na-1"},
        )


class _LocalMusicLibraryStub:
    def __init__(self, track_name: str = "local-song", file_path: str = "/music/local-song.mp3") -> None:
        self.all_music = {track_name: file_path}
        self._track_name = track_name
        self._file_path = file_path

    def is_web_music(self, name: str) -> bool:
        _ = name
        return False

    def get_filename(self, name: str) -> str:
        return self.all_music.get(name, "")

    def _get_file_url(self, filepath: str) -> str:
        return f"http://127.0.0.1:58090/music/{Path(filepath).name}"

    def searchmusic(self, query: str):
        if query in self.all_music:
            return [query]
        return [self._track_name] if query in self._track_name else []
