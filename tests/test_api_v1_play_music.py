import pytest

pytest.importorskip("aiofiles")

from xiaomusic.api.models import ApiV1PlayMusicListRequest, ApiV1PlayMusicRequest
from xiaomusic.api.routers import v1


@pytest.mark.asyncio
async def test_api_v1_play_music_success(monkeypatch):
    calls = {}

    async def _do_play(did, name, search_key=""):
        calls["did"] = did
        calls["name"] = name
        calls["search_key"] = search_key

    monkeypatch.setattr(v1.xiaomusic, "do_play", _do_play)
    out = await v1.api_v1_play_music(
        ApiV1PlayMusicRequest(
            speaker_id="did-1",
            music_name="song-a",
            search_key="song-a",
        )
    )
    assert out["success"] is True
    assert out["state"] == "playing"
    assert calls == {"did": "did-1", "name": "song-a", "search_key": "song-a"}


@pytest.mark.asyncio
async def test_api_v1_play_music_list_success(monkeypatch):
    calls = {}

    async def _do_play_music_list(did, list_name, music_name=""):
        calls["did"] = did
        calls["list_name"] = list_name
        calls["music_name"] = music_name

    monkeypatch.setattr(v1.xiaomusic, "do_play_music_list", _do_play_music_list)
    out = await v1.api_v1_play_music_list(
        ApiV1PlayMusicListRequest(
            speaker_id="did-1",
            list_name="全部",
            music_name="song-a",
        )
    )
    assert out["success"] is True
    assert out["state"] == "playing"
    assert calls == {"did": "did-1", "list_name": "全部", "music_name": "song-a"}
