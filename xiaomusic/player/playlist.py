"""Playlist session snapshot, current-track identity and next/previous selection.

Mixin of :class:`xiaomusic.device_player.XiaoMusicDevice`; relies on the
attributes it sets up in ``__init__``.
"""

import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass
from xiaomusic.const import (
    PLAY_TYPE_ONE,
    PLAY_TYPE_RND,
    PLAY_TYPE_SEQ,
)
from xiaomusic.utils.text_utils import list2str


class PlaylistMixin:
    def get_cur_music(self):
        """获取当前播放的音乐名称"""
        return str(
            getattr(self.device, "current_display_name", "") or self.device.cur_music
        )

    def _get_playlist_names(self) -> list[str]:
        """从 _play_list_items 派生当前播放列表的名称列表（只读视图）"""
        return [str(item.get("display_name") or "") for item in self._play_list_items]

    @staticmethod
    def _normalize_playlist_runtime_item(item) -> dict[str, str]:
        if not isinstance(item, dict):
            title = str(item or "").strip()
            return {
                "item_id": "",
                "entity_id": "",
                "display_name": title,
                "legacy_name": title,
            }
        title = str(
            item.get("display_name")
            or item.get("legacy_name")
            or item.get("title")
            or item.get("name")
            or ""
        ).strip()
        legacy_name = str(item.get("legacy_name") or title).strip()
        return {
            "item_id": str(item.get("item_id") or item.get("id") or "").strip(),
            "entity_id": str(item.get("entity_id") or "").strip(),
            "display_name": title,
            "legacy_name": legacy_name,
        }

    def _build_playlist_runtime_items(self, playlist_name: str) -> list[dict[str, str]]:
        music_library = getattr(self.xiaomusic, "music_library", None)
        getter = getattr(music_library, "get_playlist_items", None)
        if callable(getter):
            try:
                items = getter(playlist_name)
            except Exception:
                items = None
            if isinstance(items, list) and items:
                return [self._normalize_playlist_runtime_item(item) for item in items]

        music_list = getattr(music_library, "music_list", {}) or {}
        legacy_items = music_list.get(playlist_name, [])
        if not isinstance(legacy_items, list):
            return []
        return [self._normalize_playlist_runtime_item(name) for name in legacy_items]

    def _find_playlist_index(
        self,
        *,
        item_id: str = "",
        entity_id: str = "",
        display_name: str = "",
    ) -> int:
        items = list(getattr(self, "_play_list_items", []) or [])
        target_item_id = str(item_id or "").strip()
        target_entity_id = str(entity_id or "").strip()
        target_display = str(display_name or "").strip()
        if target_item_id:
            for idx, item in enumerate(items):
                if str(item.get("item_id") or "") == target_item_id:
                    return idx
        if target_entity_id:
            for idx, item in enumerate(items):
                if str(item.get("entity_id") or "") == target_entity_id:
                    return idx
        if target_display:
            # 如果同时提供了 entity_id，优先匹配 display_name + entity_id 都相符的项
            # 应对随机打乱或列表中有多首同名歌曲的情况
            best_idx = -1
            for idx, item in enumerate(items):
                if target_display in {
                    str(item.get("display_name") or "").strip(),
                    str(item.get("legacy_name") or "").strip(),
                }:
                    if target_entity_id and str(item.get("entity_id") or "") == target_entity_id:
                        return idx  # 精确命中：display_name + entity_id 双重匹配
                    if best_idx < 0:
                        best_idx = idx  # 兜底：第一个仅 display_name 匹配的
            if best_idx >= 0:
                return best_idx
        return -1

    def _set_runtime_track_reference(
        self,
        *,
        playlist_name: str | None = None,
        display_name: str = "",
        entity_id: str = "",
        playlist_item_id: str = "",
        current_index: int | None = None,
    ) -> None:
        playlist_name = str(
            playlist_name if playlist_name is not None else self.device.cur_playlist or ""
        ).strip()
        resolved_index = current_index if current_index is not None else -1
        if resolved_index < 0:
            resolved_index = self._find_playlist_index(
                item_id=playlist_item_id,
                entity_id=entity_id,
                display_name=display_name,
            )
        item = None
        if 0 <= resolved_index < len(getattr(self, "_play_list_items", []) or []):
            item = self._play_list_items[resolved_index]
        final_display = str(
            display_name
            or (item.get("display_name") if item else "")
            or getattr(self.device, "current_display_name", "")
            or getattr(self.device, "cur_music", "")
            or ""
        ).strip()
        final_entity_id = str(
            entity_id
            or (item.get("entity_id") if item else "")
            or getattr(self.device, "current_entity_id", "")
            or ""
        ).strip()
        final_playlist_item_id = str(
            playlist_item_id
            or (item.get("item_id") if item else "")
            or getattr(self.device, "current_playlist_item_id", "")
            or ""
        ).strip()

        self.device.cur_music = final_display
        self.device.current_display_name = final_display
        self.device.current_entity_id = final_entity_id
        self.device.current_playlist_item_id = final_playlist_item_id
        self._current_index = resolved_index if resolved_index >= 0 else -1

        playlist2music = getattr(self.device, "playlist2music", None)
        if playlist_name and isinstance(playlist2music, dict):
            playlist2music[playlist_name] = final_display

    def get_current_track_reference(self) -> dict[str, str | int]:
        return {
            "display_name": self.get_cur_music(),
            "entity_id": str(getattr(self.device, "current_entity_id", "") or ""),
            "playlist_item_id": str(
                getattr(self.device, "current_playlist_item_id", "") or ""
            ),
            "current_index": int(getattr(self, "_current_index", -1) or -1),
            "playlist_name": str(getattr(self.device, "cur_playlist", "") or ""),
        }

    def update_playlist(self):
        """初始化/更新播放列表"""
        # 没有重置 list 且非初始化
        if self.device.cur_playlist not in self.xiaomusic.music_library.music_list:
            self.device.cur_playlist = "全部"

        list_name = self.device.cur_playlist
        playlist_items = self._build_playlist_runtime_items(list_name)

        if self.device.play_type == PLAY_TYPE_RND:
            random.shuffle(playlist_items)
            self.log.info(
                f"随机打乱 {list_name} {list2str([item.get('display_name', '') for item in playlist_items], self.config.verbose)}"
            )
        else:
            # Playlist order as the library has it (Jellyfin order, local
            # library order), the same as the v1 API path (ADR-0007 decision
            # 1). Sorting by name here made a play mode change reorder the
            # list under a queue already on the speaker.
            self.log.info(
                f"没打乱 {list_name} {list2str([item.get('display_name', '') for item in playlist_items], self.config.verbose)}"
            )

        self._play_list_items = playlist_items
        self._set_runtime_track_reference(
            playlist_name=list_name,
            display_name=str(
                getattr(self.device, "current_display_name", "")
                or getattr(self.device, "cur_music", "")
                or ""
            ),
            entity_id=str(getattr(self.device, "current_entity_id", "") or ""),
            playlist_item_id=str(
                getattr(self.device, "current_playlist_item_id", "") or ""
            ),
        )

    def get_music(self, direction="next", *, skip_one_repeat: bool = False):
        """获取下一首或上一首音乐

        skip_one_repeat=True：单曲循环模式下强制前进到下一首（手动切歌场景）

        索引解析优先级（从快到慢，从可靠到不可靠）：
        1. _current_index — 运行时维护，指向打乱后列表中的当前位置（最快，最可靠）
        2. 按 display_name 搜索 _play_list_items — 兜底（device.model 可能携带原始顺序 ID）
        3. index=0 硬兜底
        """
        play_list_len = len(self._play_list_items)
        if play_list_len == 0:
            self.log.warning("当前播放列表没有歌曲")
            return ""

        # 确定当前索引
        index = -1
        cur_music = self.get_cur_music()

        # Step 1: 优先使用 _current_index，但必须验证其有效性
        if 0 <= self._current_index < play_list_len:
            item_at_idx = self._play_list_items[self._current_index]
            idx_display = str(
                item_at_idx.get("display_name") or item_at_idx.get("legacy_name") or ""
            ).strip()
            if idx_display and idx_display == cur_music:
                index = self._current_index
            else:
                self.log.info(
                    "_current_index=%d 与当前歌曲 %s 不匹配（位置上是 %s），回退搜索",
                    self._current_index,
                    cur_music,
                    idx_display,
                )

        # Step 2: 兜底搜索 —— 优先按 display_name + entity_id（最可靠），其次用 device.model 的 ID 辅助
        if index < 0:
            # 先按 display_name 搜索（打乱后的 _play_list_items），同时传入 entity_id 辅助去重
            index = self._find_playlist_index(
                display_name=cur_music,
                entity_id=str(getattr(self.device, "current_entity_id", "") or ""),
            )
        if index < 0:
            # 再尝试 device.model 的 ID（可能携带原始顺序索引，但在打乱列表中搜索仍然能找到正确位置）
            index = self._find_playlist_index(
                item_id=str(getattr(self.device, "current_playlist_item_id", "") or ""),
                entity_id=str(getattr(self.device, "current_entity_id", "") or ""),
            )
        if index < 0:
            index = 0
        # 如果 Step 1 的 _current_index 与搜索结果不一致，立即同步 runtime 索引
        # 随机模式下常见：列表打乱后 _current_index 指向旧位置，必须强制更正
        if index >= 0 and index != self._current_index:
            self.log.info(
                "get_music 索引修正: _current_index %d -> %d (cur_music=%s)",
                self._current_index,
                index,
                cur_music,
            )
            self._current_index = index

        self.log.debug(
            "get_music direction=%s play_type=%d play_list_len=%d _current_index=%d resolved_index=%d cur_music=%s",
            direction,
            self.device.play_type,
            play_list_len,
            self._current_index,
            index,
            cur_music,
        )

        if play_list_len == 1:
            new_index = index  # 当只有一首歌曲时保持当前索引不变
        else:
            if direction == "next":
                if self.device.play_type == PLAY_TYPE_ONE and not skip_one_repeat:
                    # 单曲循环：自动切歌时重复当前歌曲；手动切歌时前进到下一首
                    new_index = index
                else:
                    new_index = index + 1
                    if (
                        self.device.play_type == PLAY_TYPE_SEQ
                        and new_index >= play_list_len
                    ):
                        self.log.info("顺序播放结束")
                        return ""
                    if new_index >= play_list_len:
                        new_index = 0
            elif direction == "prev":
                new_index = index - 1
                if new_index < 0:
                    new_index = play_list_len - 1
            else:
                self.log.error("无效的方向参数")
                return ""

        names = self._get_playlist_names()
        name = names[new_index]
        if not self.xiaomusic.music_library.is_music_exist(name):
            self._play_list_items.pop(new_index)
            return self.get_music(direction)
        return name

    def get_next_music(self, *, skip_one_repeat: bool = False):
        """获取下一首音乐

        skip_one_repeat=True：单曲循环模式下强制前进到下一首
        """
        return self.get_music(direction="next", skip_one_repeat=skip_one_repeat)

    def get_prev_music(self):
        """获取上一首音乐"""
        return self.get_music(direction="prev")

    def check_play_next(self):
        """判断是否需要播放下一首歌曲"""
        # 当前歌曲不在当前播放列表
        if self._find_playlist_index(display_name=self.get_cur_music()) < 0:
            self.log.info(f"当前歌曲 {self.get_cur_music()} 不在当前播放列表")
            return True

        # 当前没我在播放的歌曲
        if self.get_cur_music() == "":
            self.log.info("当前没我在播放的歌曲")
            return True
        else:
            # 当前播放的歌曲不存在了
            if not self.xiaomusic.music_library.is_music_exist(self.get_cur_music()):
                self.log.info(f"当前播放的歌曲 {self.get_cur_music()} 不存在了")
                return True
        return False

    def get_cur_play_list(self):
        """获取当前播放列表名称"""
        return self.device.cur_playlist

    def find_cur_playlist(self, name):
        """根据当前歌曲匹配歌曲列表

        匹配顺序：
        1. 收藏
        2. 最近新增
        3. 排除（全部,所有歌曲,所有电台）
        4. 所有歌曲
        5. 所有电台
        6. 全部
        """
        music_list = self.xiaomusic.music_library.music_list
        if name in music_list.get("收藏", []):
            return "收藏"
        if name in music_list.get("最近新增", []):
            return "最近新增"
        for list_name, play_list in music_list.items():
            if (list_name not in ["全部", "所有歌曲", "所有电台"]) and (
                name in play_list
            ):
                return list_name
        if name in music_list.get("所有歌曲", []):
            return "所有歌曲"
        if name in music_list.get("所有电台", []):
            return "所有电台"
        return "全部"

    def _bootstrap_playlist_session_for_external_url(self, context: dict | None = None):
        context = context if isinstance(context, dict) else {}
        context_hint = context.get("context_hint")
        if not isinstance(context_hint, dict):
            context_hint = {}
        source_payload = context.get("source_payload")
        if not isinstance(source_payload, dict):
            source_payload = {}

        context_type = str(
            context_hint.get("context_type") or source_payload.get("context_type") or ""
        ).strip().lower()
        playlist_name = str(
            context_hint.get("context_name")
            or context_hint.get("context_id")
            or source_payload.get("playlist_name")
            or source_payload.get("context_name")
            or ""
        ).strip()
        music_name = str(
            source_payload.get("music_name")
            or source_payload.get("track_name")
            or context.get("title")
            or ""
        ).strip()
        if context_type != "playlist":
            return
        if not playlist_name:
            return

        playlist_items = self._build_playlist_runtime_items(playlist_name)
        if not playlist_items:
            return

        self.device.cur_playlist = playlist_name
        # Shuffle when play_type is RND (persistent) OR context carries shuffle=true (single-session).
        # WebUI next/prev no longer re-POSTs /play, so each external session bootstraps only once;
        # subsequent control next/prev consume the existing snapshot via _play_next/prev.
        should_shuffle = (
            self.device.play_type == PLAY_TYPE_RND
            or bool(context.get("shuffle", False))
        )
        self._playlist_session_shuffled = should_shuffle
        if should_shuffle:
            random.shuffle(playlist_items)
            self.log.info(
                f"external_url playlist shuffled {playlist_name} {list2str([item.get('display_name', '') for item in playlist_items], self.config.verbose)}"
            )
        self._play_list_items = playlist_items
        self._set_runtime_track_reference(
            playlist_name=playlist_name,
            display_name=music_name,
            entity_id=str(
                source_payload.get("entity_id") or context_hint.get("entity_id") or ""
            ).strip(),
            playlist_item_id=str(
                source_payload.get("playlist_item_id")
                or source_payload.get("item_id")
                or source_payload.get("id")
                or context_hint.get("playlist_item_id")
                or context_hint.get("item_id")
                or ""
            ).strip(),
        )
