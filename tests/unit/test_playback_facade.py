from __future__ import annotations

import pytest

from xiaomusic.core.models.media import PlayOptions
from xiaomusic.core.models.media import DeliveryPlan, PreparedStream
from xiaomusic.playback.facade import PlaybackFacade


def test_playback_facade_serialize_dataclass() -> None:
    plan = DeliveryPlan(
        primary=PreparedStream(final_url="http://example.com/stream", source="site_media"),
        strategy="direct_only",
        decision_reason="pre_streamed_source",
    )

    out = PlaybackFacade._serialize(plan)

    assert out["primary"]["final_url"] == "http://example.com/stream"
    assert out["strategy"] == "direct_only"
    assert out["decision_reason"] == "pre_streamed_source"


def test_playback_facade_records_playback_capability_when_auth_manager_supports_it() -> None:
    captured = {}

    class _Auth:
        @staticmethod
        def record_playback_capability_verify(**kwargs):
            captured.update(kwargs)

    class _XM:
        auth_manager = _Auth()

    facade = PlaybackFacade(_XM())
    facade._record_playback_capability_verify(
        result="failed",
        verify_method="playback_dispatch",
        playback_capability_level="actual_playback_path",
        transport="mina",
        error_code="E_XIAOMI_PLAY_FAILED",
        error_message="transport dispatch failed",
    )

    assert captured["result"] == "failed"
    assert captured["transport"] == "mina"


@pytest.mark.asyncio
async def test_playback_facade_playlist_context_play_uses_runtime_playlist_flow() -> None:
    calls: list[tuple[str, str, str]] = []

    class _XM:
        @staticmethod
        def did_exist(did: str) -> bool:
            return did == "did-1"

        async def do_play_music_list(self, did: str, playlist_name: str, music_name: str):
            calls.append((did, playlist_name, music_name))

    facade = PlaybackFacade(_XM())
    out = await facade.play(
        device_id="did-1",
        query="Song A",
        source_hint="local_library",
        options=PlayOptions(
            title="Song A",
            context_hint={"context_type": "playlist", "context_name": "所有歌曲"},
            source_payload={
                "source": "local_library",
                "playlist_name": "所有歌曲",
                "music_name": "Song A",
                "context_type": "playlist",
                "context_name": "所有歌曲",
            },
        ),
        request_id="rid-playlist",
    )

    assert calls == [("did-1", "所有歌曲", "Song A")]
    assert out["status"] == "playing"
    assert out["device_id"] == "did-1"
    assert out["source_plugin"] == "local_library"
    assert out["transport"] == "device_player"
    assert out["media"]["title"] == "Song A"
