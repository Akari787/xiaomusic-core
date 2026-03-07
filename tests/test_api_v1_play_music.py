import pytest

pytest.importorskip("aiofiles")

from xiaomusic.api.models import ApiV1PlayMusicListRequest, ApiV1PlayMusicRequest
from xiaomusic.api.routers import v1


@pytest.mark.asyncio
async def test_api_v1_play_music_success(monkeypatch):
    class _Facade:
        async def play(self, *, device_id, query, source_hint="auto", options=None, request_id=None):  # noqa: ANN001
            _ = request_id
            return {
                "status": "playing",
                "device_id": device_id,
                "source_plugin": source_hint,
                "transport": "mina",
                "media": {"title": query, "stream_url": "http://a/b.mp3", "is_live": False},
                "extra": options or {},
            }

    monkeypatch.setattr(v1, "_get_facade", lambda: _Facade())
    out = await v1.api_v1_play_music(
        ApiV1PlayMusicRequest(
            speaker_id="did-1",
            music_name="song-a",
            search_key="song-a",
        )
    )
    assert out["code"] == 0
    assert out["data"]["device_id"] == "did-1"
    assert out["data"]["source_plugin"] == "local_library"


@pytest.mark.asyncio
async def test_api_v1_play_music_list_success(monkeypatch):
    class _Facade:
        async def play(self, *, device_id, query, source_hint="auto", options=None, request_id=None):  # noqa: ANN001
            _ = request_id
            return {
                "status": "playing",
                "device_id": device_id,
                "source_plugin": source_hint,
                "transport": "mina",
                "media": {"title": query, "stream_url": "http://a/b.mp3", "is_live": False},
                "extra": options or {},
            }

    monkeypatch.setattr(v1, "_get_facade", lambda: _Facade())
    out = await v1.api_v1_play_music_list(
        ApiV1PlayMusicListRequest(
            speaker_id="did-1",
            list_name="全部",
            music_name="song-a",
        )
    )
    assert out["code"] == 0
    assert out["data"]["device_id"] == "did-1"
    assert out["data"]["source_plugin"] == "local_library"
