from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from xiaomusic.adapters.sources.default_registry import register_default_source_plugins
from xiaomusic.adapters.sources.online_plugin_source_plugin import (
    OnlinePluginSourcePlugin,
)
from xiaomusic.core.errors.source_errors import SourceResolveError
from xiaomusic.core.models.media import MediaRequest
from xiaomusic.core.source import SourceRegistry
from xiaomusic.core.source.online_plugin_reference_store import (
    OnlinePluginReferenceStore,
)
from xiaomusic.playback.facade import PlaybackFacade


def test_default_registry_registers_online_plugin_without_changing_existing_plugins():
    class _XM:
        config = type("Config", (), {"jellyfin_base_url": ""})()
        music_library = object()
        online_music_service = object()

    registry = SourceRegistry()
    register_default_source_plugins(registry, _XM())
    assert set(registry._plugins) == {
        "direct_url",
        "jellyfin",
        "local_library",
        "online_plugin",
        "site_media",
    }


def test_reference_store_is_opaque_bounded_and_ttl(monkeypatch):
    clock = {"now": 100.0}
    monkeypatch.setattr("xiaomusic.core.source.online_plugin_reference_store.time.monotonic", lambda: clock["now"])
    store = OnlinePluginReferenceStore(ttl_seconds=5, capacity=2)
    first = {"platform": "qq", "id": "a", "cookie": "secret-a"}
    second = {"platform": "qq", "id": "b"}
    third = {"platform": "qq", "id": "c"}
    token_a = store.put(first)
    token_b = store.put(second)
    token_c = store.put(third)
    assert token_a.startswith("opq_")
    assert "secret-a" not in token_a
    assert len({token_a, token_b, token_c}) == 3
    with pytest.raises(SourceResolveError, match="reference unavailable"):
        store.consume(token_a)
    assert store.consume(token_b)["id"] == "b"
    assert store.consume(token_c)["id"] == "c"

    expired = store.put(first)
    clock["now"] = 106.0
    with pytest.raises(SourceResolveError, match="reference unavailable"):
        store.consume(expired)


def test_reference_store_concurrent_tokens_do_not_cross(monkeypatch):
    store = OnlinePluginReferenceStore(capacity=64)
    tokens = [store.put({"platform": "qq", "id": str(index)}) for index in range(32)]

    async def consume_all():
        return await asyncio.gather(
            *(asyncio.to_thread(store.consume, token) for token in tokens)
        )

    items = asyncio.run(consume_all())
    assert {item["id"] for item in items} == {str(index) for index in range(32)}


@pytest.mark.asyncio
async def test_online_plugin_resolves_with_existing_online_music_service():
    store = OnlinePluginReferenceStore()
    item = {"platform": "qq", "id": "media-a", "title": "Song A"}
    token = store.put(item)

    class _Service:
        async def get_media_source_url(self, music_item):
            assert music_item == item
            return {"url": "https://cdn.example.invalid/audio.m4a", "duration_seconds": 12.5}

    plugin = OnlinePluginSourcePlugin(store, _Service())
    resolved = await plugin.resolve(
        MediaRequest(request_id="rid", query=token, source_hint="online_plugin")
    )
    assert resolved.source == "online_plugin"
    assert resolved.media_id == "media-a"
    assert resolved.title == "Song A"
    assert resolved.stream_url == "https://cdn.example.invalid/audio.m4a"
    assert resolved.duration_seconds == 12.5


@pytest.mark.asyncio
async def test_online_plugin_errors_are_stable_and_does_not_leak_item():
    store = OnlinePluginReferenceStore()
    token = store.put({"platform": "qq", "id": "secret-id", "cookie": "SECRET_COOKIE"})

    class _Service:
        async def get_media_source_url(self, music_item):
            raise RuntimeError(f"upstream failed for {music_item}")

    plugin = OnlinePluginSourcePlugin(store, _Service())
    with pytest.raises(SourceResolveError) as error:
        await plugin.resolve(MediaRequest(request_id="rid", query=token, source_hint="online_plugin"))
    assert str(error.value) == "online plugin media resolution failed"
    assert "SECRET_COOKIE" not in str(error.value)
    with pytest.raises(SourceResolveError, match="media resolution failed"):
        await plugin.resolve(MediaRequest(request_id="rid", query=token, source_hint="online_plugin"))


def test_search_token_round_trips_through_v1_play_and_facade_after_registry_reload(monkeypatch, tmp_path):
    from xiaomusic.api.routers import v1

    raw_item = {"platform": "qq", "id": "media-roundtrip", "title": "Round Trip"}
    calls = []

    class _PluginManager:
        @staticmethod
        def get_enabled_plugins():
            return ["qq"]

    class _OnlineMusicService:
        async def get_media_source_url(self, music_item):
            calls.append(music_item)
            return {"url": "https://cdn.example.invalid/roundtrip.m4a"}

    class _XM:
        config = type("Config", (), {"jellyfin_base_url": "", "conf_path": str(tmp_path)})()
        music_library = object()
        js_plugin_manager = _PluginManager()
        online_music_service = _OnlineMusicService()
        device_manager = type(
            "DeviceManager",
            (),
            {
                "devices": {
                    "did-roundtrip": type(
                        "Player",
                        (),
                        {
                            "device": type(
                                "Device",
                                (),
                                {"hardware": "test", "name": "test", "host": "127.0.0.1"},
                            )(),
                            "group_name": "default",
                        },
                    )()
                }
            },
        )()

        def did_exist(self, device_id):
            return device_id == "did-roundtrip"

        async def play_url(self, **kwargs):
            return {"ret": "OK"}

        @staticmethod
        async def get_music_list_online(**kwargs):
            return {"success": True, "data": [dict(raw_item)], "total": 1}

    xm = _XM()
    facade = PlaybackFacade(xm)
    app = FastAPI()
    app.include_router(v1.router)
    monkeypatch.setattr(v1, "_get_xiaomusic", lambda: xm)
    monkeypatch.setattr(v1, "_get_facade", lambda: facade)
    client = TestClient(app)

    search_response = client.get("/api/v1/search/online", params={"keyword": "round-trip"})
    token = search_response.json()["data"]["items"][0]["play_reference"]["query"]
    assert token.startswith("opq_")

    play_payload = {
        "device_id": "did-roundtrip",
        "query": token,
        "source_hint": "online_plugin",
        "options": {"title": "Round Trip", "media_id": "opm-roundtrip"},
    }
    first = client.post("/api/v1/play", json=play_payload)
    assert first.status_code == 200
    assert first.json()["code"] == 0
    assert calls == [raw_item]

    manager = facade._get_source_plugin_manager()
    assert xm._online_plugin_reference_store is manager.get_active_registry()._plugins["online_plugin"]._store
    version_before_reload = manager.registry_version
    manager.reload_plugins()
    assert manager.registry_version > version_before_reload
    second = client.post("/api/v1/play", json=play_payload)
    assert second.status_code == 200
    assert second.json()["code"] == 0
    assert calls == [raw_item, raw_item]


@pytest.mark.asyncio
async def test_online_plugin_unknown_or_expired_token_is_stable_error():
    plugin = OnlinePluginSourcePlugin(OnlinePluginReferenceStore(), object())
    with pytest.raises(SourceResolveError, match="reference unavailable"):
        await plugin.resolve(
            MediaRequest(request_id="rid", query="opq_unknown", source_hint="online_plugin")
        )
