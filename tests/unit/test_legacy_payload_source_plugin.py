from __future__ import annotations

from xiaomusic.adapters.sources.legacy_payload_source_plugin import LegacyPayloadSourcePlugin


def test_legacy_payload_source_plugin_prefers_playlist_item_id_and_entity_id() -> None:
    plugin = LegacyPayloadSourcePlugin()
    req = plugin.adapt_request(
        request_id="r1",
        speaker_id="did-1",
        payload={
            "source": "local_library",
            "playlist_item_id": "playlist-item-42",
            "entity_id": "entity-42",
            "track_id": "track-42",
            "music_name": "Song A",
            "title": "Song A",
        },
    )

    assert req.source_hint == "local_library"
    assert req.query == "playlist-item-42"
    assert req.context["source_payload"]["entity_id"] == "entity-42"
