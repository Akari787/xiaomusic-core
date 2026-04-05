from __future__ import annotations

import pytest

from xiaomusic.core.models.media import PlayOptions
from xiaomusic.core.models.media import DeliveryPlan, PreparedStream
from xiaomusic.playback.facade import PlaybackFacade


def test_playback_facade_serialize_dataclass() -> None:
    plan = DeliveryPlan(
        primary=PreparedStream(
            final_url="http://example.com/stream", source="site_media"
        ),
        strategy="direct_only",
        decision_reason="pre_streamed_source",
    )

    out = PlaybackFacade._serialize(plan)

    assert out["primary"]["final_url"] == "http://example.com/stream"
    assert out["strategy"] == "direct_only"
    assert out["decision_reason"] == "pre_streamed_source"


def test_playback_facade_records_playback_capability_when_auth_manager_supports_it() -> (
    None
):
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
async def test_playback_facade_playlist_context_play_uses_runtime_playlist_flow() -> (
    None
):
    calls: list[tuple[str, str, str]] = []

    class _XM:
        def __init__(self):
            self.log = type(
                "L",
                (),
                {
                    "info": lambda *a, **k: None,
                    "warning": lambda *a, **k: None,
                    "error": lambda *a, **k: None,
                },
            )()
            self.music_library = type(
                "ML",
                (),
                {
                    "get_proxy_url": staticmethod(
                        lambda origin_url, name=None: origin_url
                    ),
                    "is_jellyfin_url": staticmethod(lambda url: False),
                },
            )()
            self.online_music_service = type(
                "OMS", (), {"_get_plugin_proxy_url": staticmethod(lambda payload: "")}
            )()

        @staticmethod
        def did_exist(did: str) -> bool:
            return did == "did-1"

        async def do_play_music_list(
            self, did: str, playlist_name: str, music_name: str
        ):
            calls.append((did, playlist_name, music_name))

        async def get_player_status(self, did: str) -> dict:
            _ = did
            return {"status": 1}

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


@pytest.mark.asyncio
async def test_player_state_prefers_play_song_detail_title_over_playingmusic() -> None:
    class _XM:
        @staticmethod
        def did_exist(did: str) -> bool:
            return True

        @staticmethod
        def isplaying(did: str) -> bool:
            return True

        @staticmethod
        def playingmusic(did: str) -> str:
            return "Old Local Song"

        @staticmethod
        def get_offset_duration(did: str) -> tuple[int, int]:
            return (0, 0)

        async def get_player_status(self, did: str) -> dict:
            return {
                "status": 1,
                "play_song_detail": {
                    "audio_name": "YouTube Video Title",
                    "position": 0,
                    "duration": 0,
                },
            }

    facade = PlaybackFacade(_XM())
    state = await facade.player_state("did-test")

    assert state["cur_music"] == "YouTube Video Title"
    assert state["is_playing"] is True


@pytest.mark.asyncio
async def test_player_state_falls_back_to_playingmusic_when_detail_has_no_title() -> (
    None
):
    class _XM:
        @staticmethod
        def did_exist(did: str) -> bool:
            return True

        @staticmethod
        def isplaying(did: str) -> bool:
            return True

        @staticmethod
        def playingmusic(did: str) -> str:
            return "Local Song"

        @staticmethod
        def get_offset_duration(did: str) -> tuple[int, int]:
            return (0, 0)

        async def get_player_status(self, did: str) -> dict:
            return {
                "status": 1,
                "play_song_detail": {
                    "position": 0,
                    "duration": 0,
                },
            }

    facade = PlaybackFacade(_XM())
    state = await facade.player_state("did-test")

    assert state["cur_music"] == "Local Song"
    assert state["is_playing"] is True


@pytest.mark.asyncio
async def test_player_state_detail_title_overrides_playingmusic_when_playing() -> None:
    class _XM:
        @staticmethod
        def did_exist(did: str) -> bool:
            return True

        @staticmethod
        def isplaying(did: str) -> bool:
            return False

        @staticmethod
        def playingmusic(did: str) -> str:
            return "Local Song"

        @staticmethod
        def get_offset_duration(did: str) -> tuple[int, int]:
            return (0, 0)

        async def get_player_status(self, did: str) -> dict:
            return {
                "status": 1,
                "play_song_detail": {
                    "audio_name": "YouTube Title",
                    "position": 5000,
                    "duration": 180000,
                },
            }

    facade = PlaybackFacade(_XM())
    state = await facade.player_state("did-test")

    assert state["cur_music"] == "YouTube Title"
    assert state["is_playing"] is True


@pytest.mark.asyncio
async def test_player_state_detail_title_not_used_when_not_playing() -> None:
    class _XM:
        @staticmethod
        def did_exist(did: str) -> bool:
            return True

        @staticmethod
        def isplaying(did: str) -> bool:
            return False

        @staticmethod
        def playingmusic(did: str) -> str:
            return "Local Song"

        @staticmethod
        def get_offset_duration(did: str) -> tuple[int, int]:
            return (0, 0)

        async def get_player_status(self, did: str) -> dict:
            return {
                "status": 0,
                "play_song_detail": {
                    "audio_name": "Unknown Track",
                    "position": 0,
                    "duration": 0,
                },
            }

    facade = PlaybackFacade(_XM())
    state = await facade.player_state("did-test")

    assert state["cur_music"] == ""
    assert state["is_playing"] is False


@pytest.mark.asyncio
async def test_player_state_stale_playingmusic_ignored_when_idle() -> None:
    class _XM:
        @staticmethod
        def did_exist(did: str) -> bool:
            return True

        @staticmethod
        def isplaying(did: str) -> bool:
            return False

        @staticmethod
        def playingmusic(did: str) -> str:
            return "Stale Old Song"

        @staticmethod
        def get_offset_duration(did: str) -> tuple[int, int]:
            return (0, 0)

        async def get_player_status(self, did: str) -> dict:
            return {"status": 0}

    facade = PlaybackFacade(_XM())
    state = await facade.player_state("did-test")

    assert state["cur_music"] == ""
    assert state["is_playing"] is False


@pytest.mark.asyncio
async def test_player_state_idle_returns_empty_music() -> None:
    class _XM:
        @staticmethod
        def did_exist(did: str) -> bool:
            return True

        @staticmethod
        def isplaying(did: str) -> bool:
            return False

        @staticmethod
        def playingmusic(did: str) -> str:
            return ""

        @staticmethod
        def get_offset_duration(did: str) -> tuple[int, int]:
            return (0, 0)

        async def get_player_status(self, did: str) -> dict:
            return {
                "status": 0,
                "play_song_detail": {
                    "audio_name": "Unknown Music",
                    "title": "未知音乐",
                    "position": 0,
                    "duration": 0,
                },
            }

    facade = PlaybackFacade(_XM())
    state = await facade.player_state("did-test")

    assert state["cur_music"] == ""
    assert state["is_playing"] is False
