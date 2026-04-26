from __future__ import annotations

from types import SimpleNamespace

import pytest

from xiaomusic.xiaomusic import XiaoMusic


@pytest.mark.asyncio
async def test_add_to_favorites_prefers_current_playlist_entity_identity() -> None:
    calls: list[tuple[str, list[str]]] = []

    class _Library:
        @staticmethod
        def resolve_playlist_item_identity(playlist_name: str, item_name: str = "", item_id: str = "") -> str:
            assert playlist_name == "日语"
            assert item_name == "Song A"
            return "jellyfin:item-a"

        @staticmethod
        def resolve_entity_id_by_name(name: str) -> str:
            return ""

        @staticmethod
        def play_list_add_music(name: str, music_list: list[str]) -> bool:
            calls.append((name, music_list))
            return True

    log = SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None)
    stub = SimpleNamespace(
        log=log,
        music_library=_Library(),
        playingmusic=lambda did: "Song A",
        get_cur_play_list=lambda did: "日语",
    )
    stub._resolve_current_track_reference = lambda did, fallback="": XiaoMusic._resolve_current_track_reference(stub, did, fallback)

    await XiaoMusic.add_to_favorites(stub, did="did-1")

    assert calls == [("收藏", ["jellyfin:item-a"])]


@pytest.mark.asyncio
async def test_del_from_favorites_falls_back_to_name_when_entity_unavailable() -> None:
    calls: list[tuple[str, list[str]]] = []

    class _Library:
        @staticmethod
        def resolve_playlist_item_identity(playlist_name: str, item_name: str = "", item_id: str = "") -> str:
            return ""

        @staticmethod
        def resolve_entity_id_by_name(name: str) -> str:
            assert name == "Song B"
            return ""

        @staticmethod
        def play_list_del_music(name: str, music_list: list[str]) -> bool:
            calls.append((name, music_list))
            return True

    log = SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None)
    stub = SimpleNamespace(
        log=log,
        music_library=_Library(),
        playingmusic=lambda did: "Song B",
        get_cur_play_list=lambda did: "中文",
    )
    stub._resolve_current_track_reference = lambda did, fallback="": XiaoMusic._resolve_current_track_reference(stub, did, fallback)

    await XiaoMusic.del_from_favorites(stub, did="did-1")

    assert calls == [("收藏", ["Song B"])]
