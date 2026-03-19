from __future__ import annotations

import types

import pytest

from xiaomusic.xiaomusic import XiaoMusic


def _build_xm(playlists: dict[str, list[str]]):
    played: list[tuple[str, str]] = []
    spoken: list[tuple[str, str]] = []
    logs: list[str] = []

    class _Device:
        async def play_music_list(self, list_name, music_name):
            played.append((list_name, music_name))

    xm = XiaoMusic.__new__(XiaoMusic)
    xm.music_library = types.SimpleNamespace(
        music_list=playlists,
        find_real_music_list_name=lambda name: name,
    )
    xm.device_manager = types.SimpleNamespace(devices={"did-1": _Device()})
    xm.do_tts = lambda did, text: _record_tts(spoken, did, text)
    xm.log = types.SimpleNamespace(info=logs.append)
    return xm, played, spoken, logs


async def _record_tts(spoken: list[tuple[str, str]], did: str, text: str):
    spoken.append((did, text))


@pytest.mark.asyncio
async def test_play_music_list_by_index_plays_selected_track():
    xm, played, spoken, logs = _build_xm({"收藏": ["song-a", "song-b"]})

    await xm.play_music_list_by_index("did-1", "收藏", 2)

    assert played == [("收藏", "song-b")]
    assert spoken == []
    assert logs == ["即将播放歌单 收藏 里的第 2 个: song-b"]


@pytest.mark.asyncio
async def test_play_music_list_by_index_handles_out_of_range_and_empty_playlist():
    xm, played, spoken, logs = _build_xm({"空列表": []})

    await xm.play_music_list_by_index("did-1", "空列表", 1)
    await xm.play_music_list_by_index("did-1", "空列表", 2)

    assert played == []
    assert spoken == [
        ("did-1", "播放列表空列表中找不到第1个"),
        ("did-1", "播放列表空列表中找不到第2个"),
    ]
    assert logs == []
