from __future__ import annotations

from types import SimpleNamespace

import pytest

from xiaomusic.core.models.media import PlayOptions
from xiaomusic.core.models.media import DeliveryPlan, PreparedStream
from xiaomusic.playback.facade import PlaybackFacade, build_track_id


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
    captured: dict[str, object] = {}

    class _Dispatch:
        transport = "mina"
        data = {"accepted": True}

    class _Prepared:
        source = "local_library"
        final_url = "http://example.com/song-a.mp3"

    class _Resolved:
        media_id = "media-song-a"
        title = "Song A"
        source = "local_library"
        is_live = False

    class _Outcome:
        accepted = True
        started = True

    class _Core:
        async def play(self, request, device_id=None):
            captured["request"] = request
            captured["device_id"] = device_id
            return {
                "prepared_stream": _Prepared(),
                "resolved_media": _Resolved(),
                "dispatch": _Dispatch(),
                "outcome": _Outcome(),
                "delivery_plan": None,
            }

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

        async def get_player_status(self, did: str) -> dict:
            _ = did
            return {"status": 1}

    facade = PlaybackFacade(_XM())
    facade._core_coordinator = _Core()
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

    request = captured["request"]
    assert captured["device_id"] == "did-1"
    assert request.source_hint == "local_library"
    assert request.context["source_payload"]["playlist_name"] == "所有歌曲"
    assert request.context["source_payload"]["music_name"] == "Song A"
    assert request.context["context_hint"]["context_type"] == "playlist"
    assert request.context["context_hint"]["context_id"] == "所有歌曲"
    assert out["status"] == "playing"
    assert out["device_id"] == "did-1"
    assert out["source_plugin"] == "local_library"
    assert out["transport"] == "mina"
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
async def test_build_player_state_snapshot_keeps_jellyfin_source_for_auto_stream_url_play() -> None:
    captured: dict[str, object] = {}

    class _Dispatch:
        transport = "mina"
        data = {"accepted": True}

    class _Prepared:
        source = "jellyfin"
        final_url = "http://192.168.7.4:30013/Audio/aa05e8ae29761e44e505f2a9b1816eb8/stream.mp3?api_key=demo"

    class _Resolved:
        media_id = "aa05e8ae29761e44e505f2a9b1816eb8"
        title = "慢慢懂-汪苏泷"
        source = "jellyfin"
        is_live = False

    class _Outcome:
        accepted = True
        started = True

    class _Core:
        async def play(self, request, device_id=None):
            captured["request"] = request
            captured["device_id"] = device_id
            return {
                "prepared_stream": _Prepared(),
                "resolved_media": _Resolved(),
                "dispatch": _Dispatch(),
                "outcome": _Outcome(),
                "delivery_plan": None,
            }

    class _DevicePlayer:
        _play_session_id = 1
        _current_index = -1
        _play_list = []
        _last_cmd = "external_play"
        _next_timer = None
        _play_failed_cnt = 0
        _degraded = False

        def get_cur_music(self):
            return ""

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
            self.music_library = SimpleNamespace(
                get_proxy_url=lambda origin_url, name=None: origin_url,
                is_jellyfin_url=lambda url: True,
                all_music={},
                is_web_music=lambda _name: True,
            )
            self.online_music_service = SimpleNamespace(
                _get_plugin_proxy_url=lambda payload: ""
            )
            self.device_manager = SimpleNamespace(devices={"did-1": _DevicePlayer()})
            self.config = SimpleNamespace(music_list_json="")

        @staticmethod
        def did_exist(did: str) -> bool:
            return did == "did-1"

        @staticmethod
        def isplaying(did: str) -> bool:
            return True

        @staticmethod
        def get_offset_duration(did: str) -> tuple[int, int]:
            return (0, 0)

        async def get_player_status(self, did: str) -> dict:
            return {
                "status": 1,
                "play_song_detail": {
                    "audio_name": "",
                    "position": 0,
                    "duration": 0,
                    "source": "",
                },
            }

        @staticmethod
        def get_cur_play_list(did: str) -> str:
            return "unknown-playlist"

        @staticmethod
        def playingmusic(did: str) -> str:
            return ""

    facade = PlaybackFacade(_XM())
    facade._core_coordinator = _Core()

    out = await facade.play(
        device_id="did-1",
        query="http://192.168.7.4:30013/Audio/aa05e8ae29761e44e505f2a9b1816eb8/stream.mp3?api_key=demo",
        source_hint="auto",
        options=PlayOptions(
            title="慢慢懂-汪苏泷",
            context_hint={"context_type": "playlist", "context_name": "中文", "context_id": "中文"},
        ),
        request_id="rid-jf-auto",
    )

    request = captured["request"]
    assert request.context["title"] == "慢慢懂-汪苏泷"
    assert out["source_plugin"] == "jellyfin"

    snapshot = await facade.build_player_state_snapshot("did-1")

    assert snapshot["track"]["source"] == "jellyfin"


@pytest.mark.asyncio
async def test_build_player_state_snapshot_includes_volume_from_status_and_cache() -> None:
    class _DevicePlayer:
        _play_session_id = 5
        _current_index = -1
        _play_list = []
        _last_cmd = "play"
        _last_volume = 27

        @staticmethod
        def get_cur_music() -> str:
            return ""

    class _XM:
        def __init__(self, status_payload):
            self._status_payload = status_payload
            self.device_manager = SimpleNamespace(devices={"did-1": _DevicePlayer()})
            self.config = SimpleNamespace(music_list_json="[]")
            self.music_library = SimpleNamespace(all_music={}, is_web_music=lambda _name: True)

        @staticmethod
        def did_exist(did: str) -> bool:
            return did == "did-1"

        @staticmethod
        def isplaying(did: str) -> bool:
            return False

        @staticmethod
        def get_offset_duration(did: str) -> tuple[int, int]:
            return (0, 0)

        @staticmethod
        def get_cur_play_list(did: str) -> str:
            return ""

        async def get_player_status(self, did: str) -> dict:
            return dict(self._status_payload)

    facade = PlaybackFacade(_XM({"status": 0, "volume": 41}))
    snapshot = await facade.build_player_state_snapshot("did-1")
    assert snapshot["volume"] == 41

    facade_cached = PlaybackFacade(_XM({"status": 0}))
    snapshot_cached = await facade_cached.build_player_state_snapshot("did-1")
    assert snapshot_cached["volume"] == 27


@pytest.mark.asyncio
async def test_build_player_state_snapshot_track_id_uses_stable_identity_not_random_index() -> None:
    class _DevicePlayer:
        _play_session_id = 5
        _current_index = 7
        _play_list = ["Song A", "Song B", "Song C"]
        _last_cmd = "play"
        _next_timer = None
        _play_failed_cnt = 0
        _degraded = False

        def get_cur_music(self):
            return "Song B"

    class _XM:
        def __init__(self):
            self.device_manager = SimpleNamespace(devices={"did-1": _DevicePlayer()})
            self.music_library = SimpleNamespace(
                all_music={"Song B": "/music/song-b.flac"},
                is_web_music=lambda _name: False,
            )
            self.config = SimpleNamespace(music_list_json="")

        @staticmethod
        def did_exist(did: str) -> bool:
            return did == "did-1"

        @staticmethod
        def isplaying(did: str) -> bool:
            return True

        @staticmethod
        def playingmusic(did: str) -> str:
            return "Song B"

        @staticmethod
        def get_offset_duration(did: str) -> tuple[int, int]:
            return (0, 180)

        @staticmethod
        def get_cur_play_list(did: str) -> str:
            return "随机歌单"

        async def get_player_status(self, did: str) -> dict:
            return {
                "status": 1,
                "play_song_detail": {
                    "audio_name": "Song B",
                    "position": 0,
                    "duration": 180000,
                },
            }

    facade = PlaybackFacade(_XM())

    first = await facade.build_player_state_snapshot("did-1")
    expected_id = build_track_id("随机歌单", 7, "Song B", identity_hint="/music/song-b.flac")
    assert first["track"]["id"] == expected_id

    _DevicePlayer._current_index = 1
    second = await facade.build_player_state_snapshot("did-1")
    assert second["track"]["id"] == expected_id


@pytest.mark.asyncio
async def test_build_player_state_snapshot_prefers_membership_item_id_and_entity_id() -> None:
    class _DevicePlayer:
        _play_session_id = 6
        _current_index = 0
        _play_list = ["Song B"]
        _last_cmd = "play"
        _next_timer = None
        _play_failed_cnt = 0
        _degraded = False

        def get_cur_music(self):
            return "Song B"

    class _XM:
        def __init__(self):
            self.device_manager = SimpleNamespace(devices={"did-1": _DevicePlayer()})
            self.music_library = SimpleNamespace(
                all_music={"Song B": "/music/song-b.flac"},
                is_web_music=lambda _name: False,
                resolve_playlist_item_record=lambda playlist_name, item_name="", item_id="": {
                    "item_id": "playlist-item-42",
                    "entity_id": "local:/music/song-b.flac",
                },
                resolve_playlist_item_identity=lambda playlist_name, item_name="", item_id="": "local:/music/song-b.flac",
                resolve_entity_id_by_name=lambda name: "local:/music/song-b.flac" if name == "Song B" else "",
            )
            self.config = SimpleNamespace(music_list_json="")

        @staticmethod
        def did_exist(did: str) -> bool:
            return did == "did-1"

        @staticmethod
        def isplaying(did: str) -> bool:
            return True

        @staticmethod
        def playingmusic(did: str) -> str:
            return "Song B"

        @staticmethod
        def get_offset_duration(did: str) -> tuple[int, int]:
            return (0, 180)

        @staticmethod
        def get_cur_play_list(did: str) -> str:
            return "我的歌单"

        async def get_player_status(self, did: str) -> dict:
            return {
                "status": 1,
                "play_song_detail": {
                    "audio_name": "Song B",
                    "position": 0,
                    "duration": 180000,
                },
            }

    facade = PlaybackFacade(_XM())
    snapshot = await facade.build_player_state_snapshot("did-1")
    assert snapshot["track"]["id"] == "playlist-item-42"
    assert snapshot["track"]["entity_id"] == "local:/music/song-b.flac"


@pytest.mark.asyncio
async def test_build_player_state_snapshot_prefers_runtime_playlist_item_reference_when_titles_collide() -> None:
    class _DevicePlayer:
        _play_session_id = 7
        _current_index = 1
        _play_list = ["same-song", "same-song"]
        _last_cmd = "play"
        _next_timer = None
        _play_failed_cnt = 0
        _degraded = False

        def get_cur_music(self):
            return "same-song"

        def get_current_track_reference(self):
            return {
                "display_name": "same-song",
                "entity_id": "entity-2",
                "playlist_item_id": "item-2",
                "current_index": 1,
                "playlist_name": "中文",
            }

    class _XM:
        def __init__(self):
            self.device_manager = SimpleNamespace(devices={"did-1": _DevicePlayer()})
            self.music_library = SimpleNamespace(
                all_music={"same-song": "/music/same-song.flac"},
                is_web_music=lambda _name: False,
                resolve_playlist_item_record=lambda playlist_name, item_name="", item_id="": {
                    "item_id": "item-2",
                    "entity_id": "entity-2",
                }
                if item_id == "item-2"
                else {
                    "item_id": "item-1",
                    "entity_id": "entity-1",
                },
                resolve_playlist_item_identity=lambda playlist_name, item_name="", item_id="": "entity-2"
                if item_id == "item-2"
                else "entity-1",
                resolve_entity_id_by_name=lambda name: "entity-1",
            )
            self.config = SimpleNamespace(music_list_json="")

        @staticmethod
        def did_exist(did: str) -> bool:
            return did == "did-1"

        @staticmethod
        def isplaying(did: str) -> bool:
            return True

        @staticmethod
        def playingmusic(did: str) -> str:
            return "same-song"

        @staticmethod
        def get_offset_duration(did: str) -> tuple[int, int]:
            return (0, 180)

        @staticmethod
        def get_cur_play_list(did: str) -> str:
            return "中文"

        async def get_player_status(self, did: str) -> dict:
            return {
                "status": 1,
                "play_song_detail": {
                    "audio_name": "same-song",
                    "position": 0,
                    "duration": 180000,
                },
            }

    snapshot = await PlaybackFacade(_XM()).build_player_state_snapshot("did-1")
    assert snapshot["track"]["id"] == "item-2"
    assert snapshot["track"]["entity_id"] == "entity-2"
    assert snapshot["context"]["current_index"] == 1


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
