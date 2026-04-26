"""音乐库管理模块

负责音乐库的管理、播放列表操作、音乐搜索和标签管理。
"""

import asyncio
import base64
import copy
import hashlib
import json
import os
import random
import threading
import time
import urllib.parse
from collections import OrderedDict
from dataclasses import asdict
from urllib.parse import urlparse
from uuid import uuid4

from xiaomusic.const import SUPPORT_MUSIC_TYPE
from xiaomusic.events import CONFIG_CHANGED
from xiaomusic.utils.file_utils import not_in_dirs, traverse_music_directory
from xiaomusic.utils.music_utils import (
    Metadata,
    extract_audio_metadata,
    get_local_music_duration,
    get_web_music_duration,
    save_picture_by_base64,
    set_music_tag_to_file,
)
from xiaomusic.utils.network_utils import MusicUrlCache
from xiaomusic.utils.system_utils import try_add_access_control_param
from xiaomusic.utils.text_utils import custom_sort_key, find_best_match, fuzzyfinder


class MusicLibrary:
    """音乐库管理类

    负责管理本地和网络音乐库，包括：
    - 音乐列表生成和管理
    - 播放列表的增删改查
    - 音乐搜索和模糊匹配
    - 音乐标签的读取和更新
    """

    def __init__(
        self,
        config,
        log,
        event_bus=None,
    ):
        """初始化音乐库

        Args:
            config: 配置对象
            log: 日志对象
            event_bus: 事件总线对象（可选）
        """
        self.config = config
        self.log = log
        self.event_bus = event_bus

        # 音乐库数据
        self.all_music = {}  # 兼容只读视图：{legacy_name: filepath/url}
        self.music_list = {}  # 兼容只读视图：{list_name: [legacy_music_names]}
        self.music_entities = OrderedDict()  # 主模型：{entity_id: entity_record}
        self.playlist_definitions = OrderedDict()  # 歌单定义：{playlist_name: playlist_record}
        self.playlist_memberships = OrderedDict()  # 关系模型：{playlist_name: [membership_record]}
        self.name_index = {}  # 辅助索引：{display_name: [entity_id, ...]}
        self.legacy_name_to_entity = {}  # 兼容索引：{legacy_name: entity_id}
        self.default_music_list_names = []  # 非自定义歌单名称列表
        self.custom_play_list = None  # 自定义播放列表缓存

        # 网络音乐相关
        self._all_radio = {}  # 所有电台
        self._web_music_api = {}  # 兼容视图：{legacy_name: api_payload}
        self._entity_web_music_api = {}  # 主缓存：{entity_id: api_payload}

        # 搜索索引
        self._extra_index_search = {}  # 额外搜索索引 {filepath: name}

        # 标签管理
        self.all_music_tags = {}  # 兼容视图：{legacy_name: tags}
        self._entity_music_tags = {}  # 主缓存：{entity_id: tags}
        self._tag_generation_task = False  # 标签生成任务标志
        self._web_music_duration_cache = {}  # 兼容视图：{legacy_name: duration}
        self._entity_web_music_duration_cache = {}  # 主缓存：{entity_id: duration}

        # URL处理相关
        self.url_cache = MusicUrlCache()  # URL缓存
        self._proxy_url_tokens = {}
        self._proxy_url_tokens_lock = threading.Lock()

    @staticmethod
    def _normalize_local_path(filepath):
        try:
            return os.path.normcase(os.path.realpath(filepath))
        except Exception:
            return os.path.normcase(os.path.abspath(filepath))

    @staticmethod
    def _normalize_direct_url(url):
        raw = str(url or "").strip()
        if not raw:
            return ""
        parsed = urlparse(raw)
        if parsed.scheme not in {"http", "https", "self"}:
            return raw
        normalized_path = parsed.path or ""
        if parsed.scheme == "self":
            return f"self://{normalized_path}"
        return f"{parsed.scheme}://{parsed.netloc}{normalized_path}"

    def _infer_entity_identity(self, music, *, fallback_name="", fallback_url=""):
        music = music if isinstance(music, dict) else {}
        entity_id = str(music.get("entity_id") or "").strip()
        if entity_id:
            return entity_id

        source = str(music.get("source") or "").strip() or "direct"
        source_item_id = str(
            music.get("source_item_id")
            or music.get("media_id")
            or music.get("id")
            or music.get("track_id")
            or ""
        ).strip()
        if source_item_id:
            return f"{source}:{source_item_id}"

        path = str(music.get("path") or "").strip()
        if path:
            return f"local:{self._normalize_local_path(path)}"

        url = str(music.get("url") or fallback_url or "").strip()
        if url:
            normalized_url = self._normalize_direct_url(url)
            if self.is_jellyfin_url(url):
                parsed = urlparse(url)
                path_parts = [part for part in parsed.path.split("/") if part]
                if len(path_parts) >= 2 and path_parts[0].lower() == "audio":
                    return f"jellyfin:{path_parts[1]}"
            return f"{source}:{hashlib.md5(normalized_url.encode()).hexdigest()}"

        name = str(music.get("name") or fallback_name or "").strip()
        return f"unknown:{hashlib.md5(name.encode()).hexdigest()}"

    @staticmethod
    def _short_entity_suffix(entity_id):
        entity_id = str(entity_id or "")
        digest = hashlib.md5(entity_id.encode()).hexdigest()
        return digest[:6]

    @staticmethod
    def _system_playlist_id(name):
        mapping = {
            "所有歌曲": "system:all-songs",
            "所有电台": "system:all-radios",
            "收藏": "system:favorites",
            "全部": "system:all",
            "下载": "system:downloads",
            "其他": "system:others",
            "最近新增": "system:recent",
        }
        return mapping.get(name, f"system:{hashlib.md5(str(name).encode()).hexdigest()[:12]}")

    def _build_playlist_id(self, playlist_name, *, kind="source", source="", source_playlist_id=""):
        if kind == "system":
            return self._system_playlist_id(playlist_name)
        if kind == "custom":
            return f"custom:{hashlib.md5(str(playlist_name).encode()).hexdigest()[:12]}"
        if source and source_playlist_id:
            return f"{source}:{source_playlist_id}"
        prefix = source or kind or "playlist"
        return f"{prefix}:{hashlib.md5(str(playlist_name).encode()).hexdigest()[:12]}"

    def _register_playlist_definition(
        self,
        playlist_name,
        *,
        kind="source",
        source="",
        source_playlist_id="",
        readonly=True,
    ):
        playlist_name = str(playlist_name or "").strip()
        if not playlist_name:
            return None
        record = self.playlist_definitions.get(playlist_name)
        if record is None:
            record = {
                "playlist_id": self._build_playlist_id(
                    playlist_name,
                    kind=kind,
                    source=source,
                    source_playlist_id=source_playlist_id,
                ),
                "playlist_name": playlist_name,
                "kind": kind,
                "source": source,
                "source_playlist_id": source_playlist_id,
                "readonly": bool(readonly),
            }
            self.playlist_definitions[playlist_name] = record
        return record

    def _register_entity(self, *, entity_id, canonical_name, source, source_item_id="", origin_url="", path="", media_type="music", duration=0, extra=None):
        entity_id = str(entity_id or "").strip()
        if not entity_id:
            return None
        canonical_name = str(canonical_name or "").strip() or entity_id
        record = self.music_entities.get(entity_id)
        if record is None:
            record = {
                "entity_id": entity_id,
                "source": str(source or "").strip() or "unknown",
                "source_item_id": str(source_item_id or "").strip(),
                "origin_url": str(origin_url or "").strip(),
                "canonical_name": canonical_name,
                "media_type": str(media_type or "music").strip() or "music",
                "duration": float(duration or 0),
                "path": str(path or "").strip(),
                "extra": dict(extra or {}),
            }
            self.music_entities[entity_id] = record
        else:
            if canonical_name and not record.get("canonical_name"):
                record["canonical_name"] = canonical_name
            if source_item_id and not record.get("source_item_id"):
                record["source_item_id"] = str(source_item_id)
            if origin_url and not record.get("origin_url"):
                record["origin_url"] = str(origin_url)
            if path and not record.get("path"):
                record["path"] = str(path)
            if duration and not record.get("duration"):
                record["duration"] = float(duration)
            if extra:
                record.setdefault("extra", {}).update(extra)

        for alias in {canonical_name, str(extra.get("display_name") or "").strip() if isinstance(extra, dict) else ""}:
            alias = str(alias or "").strip()
            if not alias:
                continue
            bucket = self.name_index.setdefault(alias, [])
            if entity_id not in bucket:
                bucket.append(entity_id)
        return record

    def _register_membership(
        self,
        playlist_name,
        *,
        entity_id,
        display_name,
        order=0,
        source="",
        source_playlist_id="",
        readonly=True,
        kind="source",
        media_type="music",
    ):
        playlist = self._register_playlist_definition(
            playlist_name,
            kind=kind,
            source=source,
            source_playlist_id=source_playlist_id,
            readonly=readonly,
        )
        if playlist is None:
            return None
        entity_id = str(entity_id or "").strip()
        if not entity_id:
            return None
        display_name = str(display_name or "").strip() or entity_id
        members = self.playlist_memberships.setdefault(playlist_name, [])
        item_key = f"{playlist['playlist_id']}:{entity_id}:{int(order)}"
        membership = {
            "playlist_id": playlist["playlist_id"],
            "playlist_name": playlist_name,
            "entity_id": entity_id,
            "display_name": display_name,
            "order": int(order),
            "source": str(source or playlist.get("source") or "").strip(),
            "source_playlist_id": str(source_playlist_id or playlist.get("source_playlist_id") or "").strip(),
            "item_id": hashlib.md5(item_key.encode()).hexdigest()[:16],
            "media_type": str(media_type or "music").strip() or "music",
        }
        members.append(membership)
        bucket = self.name_index.setdefault(display_name, [])
        if entity_id not in bucket:
            bucket.append(entity_id)
        return membership

    def _rebuild_legacy_views_from_identity_model(self):
        self.all_music = {}
        self.music_list = OrderedDict()
        self._all_radio = {}
        self._web_music_api = {}
        self._extra_index_search = {}
        self.legacy_name_to_entity = {}

        for playlist_name, members in self.playlist_memberships.items():
            sorted_members = sorted(
                members,
                key=lambda item: (int(item.get("order", 0)), custom_sort_key(str(item.get("display_name") or ""))),
            )
            names = []
            used_names = set()
            for member in sorted_members:
                entity_id = str(member.get("entity_id") or "").strip()
                entity = self.music_entities.get(entity_id) or {}
                base_name = str(member.get("display_name") or entity.get("canonical_name") or entity_id).strip()
                legacy_name = base_name or entity_id
                if legacy_name in used_names and self.legacy_name_to_entity.get(legacy_name) != entity_id:
                    legacy_name = f"{legacy_name}-[{self._short_entity_suffix(entity_id)}]"
                while legacy_name in used_names and self.legacy_name_to_entity.get(legacy_name) != entity_id:
                    legacy_name = f"{base_name}-[{self._short_entity_suffix(entity_id)}]"
                used_names.add(legacy_name)
                member["legacy_name"] = legacy_name
                names.append(legacy_name)
                self.legacy_name_to_entity[legacy_name] = entity_id
                locator = str(entity.get("path") or entity.get("origin_url") or "").strip()
                if locator:
                    self.all_music[legacy_name] = locator
                if str(entity.get("media_type") or "") == "radio" and locator:
                    self._all_radio[legacy_name] = locator
                duration = entity.get("duration") or 0
                try:
                    duration = float(duration)
                except Exception:
                    duration = 0
                if duration > 0:
                    self._set_cached_duration(legacy_name, duration, entity_id=entity_id)
                api_payload = (entity.get("extra") or {}).get("api") if isinstance(entity.get("extra"), dict) else None
                if api_payload:
                    self._set_cached_web_music_api(legacy_name, api_payload, entity_id=entity_id)
                if locator and not str(entity.get("media_type") or "") == "radio":
                    self._extra_index_search[locator] = legacy_name
            self.music_list[playlist_name] = names

    def get_playlist_items(self, playlist_name=None):
        if playlist_name is not None:
            return list(self.playlist_memberships.get(playlist_name, []))
        return {name: list(items) for name, items in self.playlist_memberships.items()}

    def resolve_playlist_item_record(self, playlist_name, *, item_name="", item_id=""):
        members = self.playlist_memberships.get(str(playlist_name or "").strip(), [])
        target_item_id = str(item_id or "").strip()
        target_name = str(item_name or "").strip()
        if target_item_id:
            for member in members:
                if str(member.get("item_id") or "") == target_item_id:
                    return dict(member)
        if target_name:
            for member in members:
                if target_name in {
                    str(member.get("legacy_name") or "").strip(),
                    str(member.get("display_name") or "").strip(),
                }:
                    return dict(member)
        return None

    def resolve_playlist_item_record_any(self, *, item_name="", item_id=""):
        target_item_id = str(item_id or "").strip()
        target_name = str(item_name or "").strip()
        if target_item_id:
            for playlist_name in self.playlist_memberships:
                member = self.resolve_playlist_item_record(
                    playlist_name,
                    item_id=target_item_id,
                )
                if member:
                    return member
        if target_name:
            for playlist_name in self.playlist_memberships:
                member = self.resolve_playlist_item_record(
                    playlist_name,
                    item_name=target_name,
                )
                if member:
                    return member
        return None

    def resolve_playlist_item_identity(self, playlist_name, *, item_name="", item_id=""):
        member = self.resolve_playlist_item_record(
            playlist_name,
            item_name=item_name,
            item_id=item_id,
        )
        if member:
            return str(member.get("entity_id") or "")
        target_name = str(item_name or "").strip()
        if target_name and target_name in self.legacy_name_to_entity:
            return str(self.legacy_name_to_entity.get(target_name) or "")
        return ""

    def gen_all_music_list(self):
        """生成所有音乐列表

        扫描音乐目录，生成本地音乐列表和播放列表。
        """
        self.all_music = {}
        self.music_list = OrderedDict()
        self.music_entities = OrderedDict()
        self.playlist_definitions = OrderedDict()
        self.playlist_memberships = OrderedDict()
        self.name_index = {}
        self.legacy_name_to_entity = {}
        self._all_radio = {}
        self._web_music_api = {}
        self._entity_web_music_api = {}
        self._web_music_duration_cache = {}
        self._entity_web_music_duration_cache = {}
        self._entity_music_tags = {}

        local_entity_records = []

        # 扫描本地音乐目录
        exclude_dirs_set = self.config.get_exclude_dirs_set()
        local_musics = traverse_music_directory(
            self.config.music_path,
            depth=self.config.music_path_depth,
            exclude_dirs=exclude_dirs_set,
            support_extension=SUPPORT_MUSIC_TYPE,
        )

        for dir_name, files in local_musics.items():
            if len(files) == 0:
                continue

            # 处理目录名称
            if dir_name == os.path.basename(self.config.music_path):
                dir_name = "其他"
            if (
                self.config.music_path != self.config.download_path
                and dir_name == os.path.basename(self.config.download_path)
            ):
                dir_name = "下载"

            self._register_playlist_definition(
                dir_name,
                kind="source",
                source="local",
                source_playlist_id=dir_name,
                readonly=True,
            )

            sorted_files = sorted(files, key=lambda item: custom_sort_key(os.path.splitext(os.path.basename(item))[0]))
            for idx, file in enumerate(sorted_files):
                filename = os.path.basename(file)
                (name, _) = os.path.splitext(filename)
                entity_id = f"local:{self._normalize_local_path(file)}"
                self._register_entity(
                    entity_id=entity_id,
                    canonical_name=name,
                    source="local",
                    path=file,
                    media_type="music",
                    extra={"display_name": name},
                )
                self._register_membership(
                    dir_name,
                    entity_id=entity_id,
                    display_name=name,
                    order=idx,
                    source="local",
                    source_playlist_id=dir_name,
                    readonly=True,
                    kind="source",
                    media_type="music",
                )
                local_entity_records.append(
                    {
                        "entity_id": entity_id,
                        "display_name": name,
                        "path": file,
                        "dir_name": dir_name,
                    }
                )
                self.log.debug(f"gen_all_music_list {name}:{dir_name}:{file}")

        # 补充网络歌单
        try:
            self._append_music_list()
        except Exception as e:
            self.log.exception(f"Execption {e}")

        # 初始化系统歌单定义。系统歌单的聚合规则在此显式固定：
        # - 全部 / 所有歌曲：按 entity 去重聚合
        # - 所有电台：按 radio entity 聚合
        # - 最近新增：按最近本地 entity 聚合
        # - 收藏：由 custom playlist store 反射成 system-facing playlist
        system_playlists = ["所有歌曲", "所有电台", "收藏", "全部", "下载", "其他", "最近新增"]
        for playlist_name in system_playlists:
            self._register_playlist_definition(
                playlist_name,
                kind="system",
                source="system",
                source_playlist_id=playlist_name,
                readonly=True,
            )
            self.playlist_memberships.setdefault(playlist_name, [])

        # 最近新增(仅本地文件)
        sorted_recent = sorted(
            local_entity_records,
            key=lambda item: os.path.getmtime(item["path"]),
            reverse=True,
        )[: self.config.recently_added_playlist_len]
        for idx, item in enumerate(sorted_recent):
            self._register_membership(
                "最近新增",
                entity_id=item["entity_id"],
                display_name=item["display_name"],
                order=idx,
                source="system",
                source_playlist_id="recent",
                readonly=True,
                kind="system",
                media_type="music",
            )

        # 全部 / 所有歌曲 / 所有电台 按实体聚合，避免同实体异名重复出现
        ordered_entity_ids = list(self.music_entities.keys())
        all_idx = 0
        song_idx = 0
        radio_idx = 0
        for entity_id in ordered_entity_ids:
            entity = self.music_entities.get(entity_id) or {}
            display_name = str(entity.get("canonical_name") or entity_id)
            media_type = str(entity.get("media_type") or "music")
            self._register_membership(
                "全部",
                entity_id=entity_id,
                display_name=display_name,
                order=all_idx,
                source="system",
                source_playlist_id="all",
                readonly=True,
                kind="system",
                media_type=media_type,
            )
            all_idx += 1
            if media_type == "radio":
                self._register_membership(
                    "所有电台",
                    entity_id=entity_id,
                    display_name=display_name,
                    order=radio_idx,
                    source="system",
                    source_playlist_id="all-radios",
                    readonly=True,
                    kind="system",
                    media_type=media_type,
                )
                radio_idx += 1
            else:
                self._register_membership(
                    "所有歌曲",
                    entity_id=entity_id,
                    display_name=display_name,
                    order=song_idx,
                    source="system",
                    source_playlist_id="all-songs",
                    readonly=True,
                    kind="system",
                    media_type=media_type,
                )
                song_idx += 1

        self._rebuild_legacy_views_from_identity_model()

        # 非自定义歌单
        self.default_music_list_names = list(self.music_list.keys())

        # 刷新自定义歌单
        self.refresh_custom_play_list()

        # all_music 更新，重建 tag（仅在事件循环启动后才会执行）
        self.try_gen_all_music_tag()

    def _append_music_list(self):
        """给歌单里补充网络歌单"""
        if not self.config.music_list_json:
            return

        music_list = json.loads(self.config.music_list_json)

        try:
            for item in music_list:
                list_name = item.get("name")
                musics = item.get("musics")
                if (not list_name) or (not musics):
                    continue

                source = str(item.get("source") or "direct").strip() or "direct"
                source_playlist_id = str(item.get("playlist_id") or item.get("source_playlist_id") or list_name).strip()
                self._register_playlist_definition(
                    list_name,
                    kind="source",
                    source=source,
                    source_playlist_id=source_playlist_id,
                    readonly=True,
                )

                for idx, music in enumerate(musics):
                    if not isinstance(music, dict):
                        continue
                    name = str(music.get("name") or music.get("title") or "").strip()
                    url = str(music.get("url") or "").strip()
                    path = str(music.get("path") or "").strip()
                    music_type = str(music.get("type") or "music").strip() or "music"
                    if (not name) or (not url and not path):
                        continue

                    entity_id = self._infer_entity_identity(
                        music,
                        fallback_name=name,
                        fallback_url=url,
                    )
                    canonical_name = str(music.get("canonical_name") or name).strip() or name
                    duration = music.get("duration", 0)
                    try:
                        duration = float(duration)
                    except Exception:
                        duration = 0

                    self._register_entity(
                        entity_id=entity_id,
                        canonical_name=canonical_name,
                        source=source,
                        source_item_id=str(
                            music.get("source_item_id")
                            or music.get("media_id")
                            or music.get("id")
                            or music.get("track_id")
                            or ""
                        ).strip(),
                        origin_url=url,
                        path=path,
                        media_type=music_type,
                        duration=duration,
                        extra={
                            "display_name": name,
                            "api": music.get("api"),
                        },
                    )
                    self._register_membership(
                        list_name,
                        entity_id=entity_id,
                        display_name=name,
                        order=idx,
                        source=source,
                        source_playlist_id=source_playlist_id,
                        readonly=True,
                        kind="source",
                        media_type=music_type,
                    )
                    if music.get("api"):
                        self._set_cached_web_music_api(entity_id, music, entity_id=entity_id)
        except Exception as e:
            self.log.exception(f"Execption {e}")

    def refresh_custom_play_list(self):
        """刷新自定义歌单"""
        try:
            # 删除旧的自定义歌单定义与关系
            for k in list(self.playlist_memberships.keys()):
                playlist = self.playlist_definitions.get(k) or {}
                if playlist.get("kind") == "custom":
                    self.playlist_memberships.pop(k, None)
                    self.playlist_definitions.pop(k, None)

            # 合并新的自定义歌单
            custom_play_list = self.get_custom_play_list()
            custom_play_list, changed = self._normalize_custom_playlist_conflicts(
                custom_play_list
            )
            custom_play_list, normalized_changed = self._normalize_custom_playlist_payload(
                custom_play_list
            )
            if changed or normalized_changed:
                self.custom_play_list = custom_play_list
                self.config.custom_play_list_json = json.dumps(
                    custom_play_list, ensure_ascii=False
                )

            for playlist_name, entries in custom_play_list.items():
                self._register_playlist_definition(
                    playlist_name,
                    kind="custom",
                    source="custom",
                    source_playlist_id=playlist_name,
                    readonly=False,
                )
                self.playlist_memberships[playlist_name] = []
                for idx, entry in enumerate(entries):
                    entity_id = str(entry.get("entity_id") or "").strip()
                    if not entity_id:
                        continue
                    self._register_membership(
                        playlist_name,
                        entity_id=entity_id,
                        display_name=str(entry.get("display_name") or self._display_name_for_entity(entity_id)).strip(),
                        order=idx,
                        source="custom",
                        source_playlist_id=playlist_name,
                        readonly=False,
                        kind="custom",
                        media_type=str((self.music_entities.get(entity_id) or {}).get("media_type") or "music"),
                    )

            self._rebuild_legacy_views_from_identity_model()
        except Exception as e:
            self.log.exception(f"Execption {e}")

    def _is_reserved_playlist_name(self, name):
        """判断是否与系统/目录歌单冲突（自定义歌单不可占用）。

        `收藏` 是特例：对外表现为系统歌单，但底层数据源固定落在 custom
        playlist store，作为 favorites 的 backing playlist 使用。
        """
        token = str(name or "").strip()
        if token == "收藏":
            return False
        return token in self.default_music_list_names

    def _build_custom_conflict_name(self, base_name, existed_names):
        """为冲突的自定义歌单生成可用的新名称"""
        suffix = "(自定义)"
        candidate = f"{base_name}{suffix}"
        if candidate not in existed_names:
            return candidate

        index = 2
        while True:
            candidate = f"{base_name}{suffix}{index}"
            if candidate not in existed_names:
                return candidate
            index += 1

    def _normalize_custom_playlist_conflicts(self, custom_play_list):
        """清理历史同名冲突：目录/系统歌单名被自定义占用时自动改名"""
        normalized = {}
        changed = False

        reserved_names = {
            item for item in self.default_music_list_names if str(item or "").strip() != "收藏"
        }
        occupied_names = set(reserved_names)

        for name, musics in custom_play_list.items():
            final_name = name
            if final_name in reserved_names or final_name in occupied_names:
                final_name = self._build_custom_conflict_name(name, occupied_names)
                changed = True
                self.log.info(
                    "自定义歌单名与系统/目录歌单冲突，已自动改名: %s -> %s",
                    name,
                    final_name,
                )

            occupied_names.add(final_name)
            normalized[final_name] = list(musics)

        return normalized, changed

    def _display_name_for_entity(self, entity_id, fallback_name=""):
        entity = self.music_entities.get(str(entity_id or "").strip()) or {}
        return str(entity.get("canonical_name") or fallback_name or entity_id or "").strip()

    def _entity_id_for_name(self, name):
        return str(self.resolve_entity_id_by_name(name) or "").strip()

    def _get_cached_tags(self, name="", *, entity_id=""):
        resolved_entity_id = str(entity_id or self._entity_id_for_name(name) or "").strip()
        if resolved_entity_id and resolved_entity_id in self._entity_music_tags:
            return copy.copy(self._entity_music_tags.get(resolved_entity_id, asdict(Metadata())))
        return copy.copy(self.all_music_tags.get(name, asdict(Metadata())))

    def _set_cached_tags(self, name, tags, *, entity_id=""):
        resolved_entity_id = str(entity_id or self._entity_id_for_name(name) or "").strip()
        if resolved_entity_id:
            self._entity_music_tags[resolved_entity_id] = copy.copy(tags)
        if name:
            self.all_music_tags[name] = copy.copy(tags)

    def _get_cached_duration(self, name="", *, entity_id="") -> float:
        resolved_entity_id = str(entity_id or self._entity_id_for_name(name) or "").strip()
        if resolved_entity_id and resolved_entity_id in self._entity_web_music_duration_cache:
            return float(self._entity_web_music_duration_cache.get(resolved_entity_id) or 0)
        return float(self._web_music_duration_cache.get(name) or 0)

    def _set_cached_duration(self, name, duration, *, entity_id=""):
        resolved_entity_id = str(entity_id or self._entity_id_for_name(name) or "").strip()
        if resolved_entity_id:
            self._entity_web_music_duration_cache[resolved_entity_id] = float(duration or 0)
        if name:
            self._web_music_duration_cache[name] = float(duration or 0)

    def _get_cached_web_music_api(self, name="", *, entity_id=""):
        resolved_entity_id = str(entity_id or self._entity_id_for_name(name) or "").strip()
        if resolved_entity_id and resolved_entity_id in self._entity_web_music_api:
            return self._entity_web_music_api.get(resolved_entity_id)
        return self._web_music_api.get(name)

    def _set_cached_web_music_api(self, name, api_payload, *, entity_id=""):
        resolved_entity_id = str(entity_id or self._entity_id_for_name(name) or "").strip()
        if resolved_entity_id:
            self._entity_web_music_api[resolved_entity_id] = api_payload
        if name:
            self._web_music_api[name] = api_payload

    def resolve_entity_id_by_name(self, name):
        token = str(name or "").strip()
        if not token:
            return ""
        if token in self.music_entities:
            return token
        if token in self.legacy_name_to_entity:
            return str(self.legacy_name_to_entity.get(token) or "")
        bucket = self.name_index.get(token) or []
        if bucket:
            return str(bucket[0] or "")
        return ""

    def get_legacy_name_for_entity(self, entity_id, playlist_name=""):
        entity_id = str(entity_id or "").strip()
        if not entity_id:
            return ""
        playlist_name = str(playlist_name or "").strip()
        if playlist_name:
            for member in self.playlist_memberships.get(playlist_name, []):
                if str(member.get("entity_id") or "").strip() == entity_id:
                    return str(member.get("legacy_name") or member.get("display_name") or "").strip()
        for legacy_name, mapped in self.legacy_name_to_entity.items():
            if str(mapped or "").strip() == entity_id:
                return str(legacy_name or "").strip()
        return self._display_name_for_entity(entity_id)

    def _normalize_custom_playlist_entry(self, entry, *, playlist_name=""):
        playlist_name = str(playlist_name or "").strip()
        if isinstance(entry, dict):
            playlist_item_id = str(entry.get("playlist_item_id") or entry.get("item_id") or "").strip()
            entity_id = str(entry.get("entity_id") or "").strip()
            display_name = str(entry.get("display_name") or entry.get("name") or "").strip()
            if playlist_item_id:
                member = None
                if playlist_name:
                    member = self.resolve_playlist_item_record(
                        playlist_name,
                        item_id=playlist_item_id,
                        item_name=display_name,
                    )
                if not member:
                    member = self.resolve_playlist_item_record_any(
                        item_id=playlist_item_id,
                        item_name=display_name,
                    )
                if member:
                    entity_id = str(member.get("entity_id") or entity_id or "").strip()
                    display_name = str(
                        member.get("display_name") or member.get("legacy_name") or display_name
                    ).strip()
            if not entity_id and playlist_name:
                entity_id = self.resolve_playlist_item_identity(
                    playlist_name,
                    item_name=display_name,
                    item_id=playlist_item_id,
                )
            if not entity_id:
                entity_id = self.resolve_playlist_item_identity("", item_name=display_name)
            if not entity_id and display_name in self.legacy_name_to_entity:
                entity_id = str(self.legacy_name_to_entity.get(display_name) or "")
            if not entity_id:
                return None
            return {
                "entity_id": entity_id,
                "display_name": display_name or self._display_name_for_entity(entity_id),
            }

        display_name = str(entry or "").strip()
        if not display_name:
            return None
        entity_id = self.resolve_entity_id_by_name(display_name)
        if not entity_id:
            entity_id = self.resolve_playlist_item_identity(playlist_name, item_name=display_name)
        if not entity_id:
            entity_id = self.resolve_playlist_item_identity("", item_name=display_name)
        if not entity_id:
            return None
        return {
            "entity_id": entity_id,
            "display_name": self._display_name_for_entity(entity_id, fallback_name=display_name),
        }

    def _normalize_custom_playlist_payload(self, custom_play_list):
        normalized = {}
        changed = False
        for playlist_name, entries in (custom_play_list or {}).items():
            normalized_entries = []
            seen_entity_ids = set()
            for entry in entries if isinstance(entries, list) else []:
                normalized_entry = self._normalize_custom_playlist_entry(entry, playlist_name=playlist_name)
                if not normalized_entry:
                    changed = True
                    continue
                entity_id = normalized_entry["entity_id"]
                if entity_id in seen_entity_ids:
                    changed = True
                    continue
                seen_entity_ids.add(entity_id)
                normalized_entries.append(normalized_entry)
                if normalized_entry != entry:
                    changed = True
            normalized[playlist_name] = normalized_entries
        return normalized, changed

    def _custom_playlist_legacy_view(self, entries):
        output = []
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict):
                entity_id = str(entry.get("entity_id") or "").strip()
                display_name = str(entry.get("display_name") or "").strip()
                display_name = display_name or self._display_name_for_entity(entity_id)
                if display_name:
                    output.append(display_name)
            else:
                display_name = str(entry or "").strip()
                if display_name:
                    output.append(display_name)
        return output

    def get_custom_play_list(self):
        """获取自定义播放列表

        Returns:
            dict: 自定义播放列表字典
        """
        if self.custom_play_list is None:
            self.custom_play_list = {}
            if self.config.custom_play_list_json:
                self.custom_play_list = json.loads(self.config.custom_play_list_json)
            self.custom_play_list, changed = self._normalize_custom_playlist_payload(
                self.custom_play_list
            )
            if changed:
                self.config.custom_play_list_json = json.dumps(
                    self.custom_play_list, ensure_ascii=False
                )
        return self.custom_play_list

    def save_custom_play_list(self):
        """保存自定义播放列表"""
        custom_play_list, _ = self._normalize_custom_playlist_payload(
            self.get_custom_play_list()
        )
        self.custom_play_list = custom_play_list
        self.refresh_custom_play_list()
        self.config.custom_play_list_json = json.dumps(
            custom_play_list, ensure_ascii=False
        )
        # 发布配置变更事件
        if self.event_bus:
            self.event_bus.publish(CONFIG_CHANGED)

    # ==================== 播放列表管理 ====================

    def play_list_add(self, name):
        """新增歌单

        Args:
            name: 歌单名称

        Returns:
            bool: 是否成功
        """
        custom_play_list = self.get_custom_play_list()
        if self._is_reserved_playlist_name(name):
            self.log.info(f"歌单名字与系统/目录歌单冲突 {name}")
            return False
        if name in custom_play_list:
            return False
        custom_play_list[name] = []
        self.save_custom_play_list()
        return True

    def play_list_del(self, name):
        """移除歌单

        Args:
            name: 歌单名称

        Returns:
            bool: 是否成功
        """
        custom_play_list = self.get_custom_play_list()
        if name not in custom_play_list:
            return False
        custom_play_list.pop(name)
        self.save_custom_play_list()
        return True

    def play_list_update_name(self, oldname, newname):
        """修改歌单名字

        Args:
            oldname: 旧歌单名称
            newname: 新歌单名称

        Returns:
            bool: 是否成功
        """
        custom_play_list = self.get_custom_play_list()
        if oldname not in custom_play_list:
            self.log.info(f"旧歌单名字不存在 {oldname}")
            return False
        if self._is_reserved_playlist_name(newname):
            self.log.info(f"新歌单名字与系统/目录歌单冲突 {newname}")
            return False
        if newname in custom_play_list:
            self.log.info(f"新歌单名字已存在 {newname}")
            return False

        play_list = custom_play_list[oldname]
        custom_play_list.pop(oldname)
        custom_play_list[newname] = play_list
        self.save_custom_play_list()
        return True

    def get_play_list_names(self):
        """获取所有自定义歌单名称

        Returns:
            list: 歌单名称列表
        """
        custom_play_list = self.get_custom_play_list()
        return list(custom_play_list.keys())

    def play_list_items(self, name):
        custom_play_list = self.get_custom_play_list()
        if name not in custom_play_list:
            return "歌单不存在", []
        play_list = custom_play_list[name]
        items = []
        for entry in play_list if isinstance(play_list, list) else []:
            normalized_entry = self._normalize_custom_playlist_entry(entry, playlist_name=name)
            if not normalized_entry:
                continue
            entity_id = str(normalized_entry.get("entity_id") or "").strip()
            display_name = str(
                normalized_entry.get("display_name")
                or self._display_name_for_entity(entity_id)
                or ""
            ).strip()
            items.append(
                {
                    "playlist_item_id": "",
                    "entity_id": entity_id,
                    "display_name": display_name,
                    "title": display_name,
                }
            )
        return "OK", items

    def play_list_musics(self, name):
        """获取歌单中所有歌曲

        Args:
            name: 歌单名称

        Returns:
            tuple: (状态消息, 歌曲列表)
        """
        ret, items = self.play_list_items(name)
        if ret != "OK":
            return ret, []
        return ret, [str(item.get("display_name") or item.get("title") or "") for item in items]

    def play_list_update_music(self, name, music_list):
        """歌单更新歌曲（覆盖）

        Args:
            name: 歌单名称
            music_list: 歌曲列表

        Returns:
            bool: 是否成功
        """
        custom_play_list = self.get_custom_play_list()
        if name not in custom_play_list:
            # 歌单不存在则新建
            if not self.play_list_add(name):
                return False
            custom_play_list = self.get_custom_play_list()

        play_list = []
        seen_entity_ids = set()
        for music_name in music_list:
            normalized_entry = self._normalize_custom_playlist_entry(music_name, playlist_name=name)
            if not normalized_entry:
                continue
            entity_id = normalized_entry["entity_id"]
            if entity_id in seen_entity_ids:
                continue
            seen_entity_ids.add(entity_id)
            play_list.append(normalized_entry)

        # 直接覆盖
        custom_play_list[name] = play_list
        self.save_custom_play_list()
        return True

    def update_music_list_json(self, list_name, update_list, append=False):
        """
        更新配置的音乐歌单Json，如果歌单存在则根据 append：False:覆盖； True:追加
        Args:
            list_name: 更新的歌单名称
            update_list: 更新的歌单列表
            append: 追加歌曲，默认 False

        Returns:
            list: 转换后的音乐项目列表
        """
        # 更新配置中的音乐列表
        if self.config.music_list_json:
            music_list = json.loads(self.config.music_list_json)
        else:
            music_list = []

        # 检查是否已存在同名歌单
        existing_index = None
        for i, item in enumerate(music_list):
            if item.get("name") == list_name:
                existing_index = i
                break

        # 构建新歌单数据
        new_music_items = []
        for item in update_list:
            normalized_item = {
                "name": item["name"],
                "url": item["url"],
                "type": item["type"],
            }
            for optional_key in (
                "entity_id",
                "canonical_name",
                "source",
                "source_item_id",
                "media_id",
                "track_id",
                "path",
                "duration",
                "playlist_id",
                "source_playlist_id",
                "title",
                "id",
                "api",
            ):
                value = item.get(optional_key)
                if value not in (None, ""):
                    normalized_item[optional_key] = value
            new_music_items.append(normalized_item)

        if existing_index is not None:
            if append:
                # 追加模式：将新项目添加到现有歌单中，避免重复
                existing_musics = music_list[existing_index]["musics"]
                existing_names = {music["name"] for music in existing_musics}

                # 只添加不存在的项目
                for new_item in new_music_items:
                    if new_item["name"] not in existing_names:
                        existing_musics.append(new_item)

                music_list[existing_index]["musics"] = existing_musics
            else:
                # 覆盖模式：替换整个歌单
                music_list[existing_index] = {
                    "name": list_name,
                    "musics": new_music_items,
                }
        else:
            # 添加新歌单
            new_music_list = {"name": list_name, "musics": new_music_items}
            music_list.append(new_music_list)

        # 保存更新后的配置
        self.config.music_list_json = json.dumps(music_list, ensure_ascii=False)

    def play_list_add_music(self, name, music_list):
        """歌单新增歌曲

        Args:
            name: 歌单名称
            music_list: 歌曲列表

        Returns:
            bool: 是否成功
        """
        custom_play_list = self.get_custom_play_list()
        if name not in custom_play_list:
            # 歌单不存在则新建
            if not self.play_list_add(name):
                return False
            custom_play_list = self.get_custom_play_list()

        play_list = custom_play_list[name]
        existing_entity_ids = {
            str(item.get("entity_id") or "").strip()
            for item in play_list
            if isinstance(item, dict)
        }
        for music_name in music_list:
            normalized_entry = self._normalize_custom_playlist_entry(music_name, playlist_name=name)
            if not normalized_entry:
                continue
            entity_id = normalized_entry["entity_id"]
            if entity_id in existing_entity_ids:
                continue
            existing_entity_ids.add(entity_id)
            play_list.append(normalized_entry)

        self.save_custom_play_list()
        return True

    def play_list_del_music(self, name, music_list):
        """歌单移除歌曲

        Args:
            name: 歌单名称
            music_list: 歌曲列表

        Returns:
            bool: 是否成功
        """
        custom_play_list = self.get_custom_play_list()
        if name not in custom_play_list:
            return False

        play_list = custom_play_list[name]
        remove_entity_ids = set()
        remove_display_names = set()
        for music_name in music_list:
            normalized_entry = self._normalize_custom_playlist_entry(music_name, playlist_name=name)
            if normalized_entry:
                remove_entity_ids.add(normalized_entry["entity_id"])
                remove_display_names.add(str(normalized_entry.get("display_name") or "").strip())
            else:
                remove_display_names.add(str(music_name or "").strip())

        custom_play_list[name] = [
            item
            for item in play_list
            if not (
                (isinstance(item, dict) and str(item.get("entity_id") or "").strip() in remove_entity_ids)
                or (isinstance(item, str) and str(item).strip() in remove_display_names)
            )
        ]

        self.save_custom_play_list()
        return True

    # ==================== 音乐搜索 ====================

    def find_real_music_name(self, name, n):
        """模糊搜索音乐名称

        Args:
            name: 搜索关键词
            n: 返回结果数量

        Returns:
            list: 匹配的音乐名称列表
        """
        if not self.config.enable_fuzzy_match:
            self.log.debug("没开启模糊匹配")
            return []

        all_music_list = list(self.all_music.keys())
        real_names = find_best_match(
            name,
            all_music_list,
            cutoff=self.config.fuzzy_match_cutoff,
            n=n,
            extra_search_index=self._extra_index_search,
        )
        if not real_names:
            self.log.info(f"没找到歌曲【{name}】")
            return []
        self.log.info(f"根据【{name}】找到歌曲【{real_names}】")
        if name in real_names:
            return [name]

        # 音乐不在查找结果同时n大于1, 模糊匹配模式，扩大范围再找，最后保留随机 n 个
        if n > 1:
            real_names = find_best_match(
                name,
                all_music_list,
                cutoff=self.config.fuzzy_match_cutoff,
                n=n * 2,
                extra_search_index=self._extra_index_search,
            )
            random.shuffle(real_names)
        self.log.info(f"没找到歌曲【{name}】")
        return real_names[:n]

    def find_real_music_list_name(self, list_name):
        """模糊搜索播放列表名称

        Args:
            list_name: 播放列表名称

        Returns:
            str: 匹配的播放列表名称
        """
        if not self.config.enable_fuzzy_match:
            self.log.debug("没开启模糊匹配")
            return list_name

        # 模糊搜一个播放列表（只需要一个，不需要 extra index）
        real_name = find_best_match(
            list_name,
            self.music_list,
            cutoff=self.config.fuzzy_match_cutoff,
            n=1,
        )[0]

        if real_name:
            self.log.info(f"根据【{list_name}】找到播放列表【{real_name}】")
            list_name = real_name
        else:
            self.log.info(f"没找到播放列表【{list_name}】")

        return list_name

    def searchmusic(self, name):
        """搜索音乐

        Args:
            name: 搜索关键词

        Returns:
            list: 搜索结果列表
        """
        all_music_list = list(self.all_music.keys())
        search_list = fuzzyfinder(name, all_music_list, self._extra_index_search)
        self.log.debug(f"searchmusic. name:{name} search_list:{search_list}")
        return search_list

    # ==================== 音乐信息 ====================

    def get_filename(self, name):
        """获取音乐文件路径

        Args:
            name: 音乐名称

        Returns:
            str: 文件路径，不存在返回空字符串
        """
        if name not in self.all_music:
            self.log.info(f"get_filename not in. name:{name}")
            return ""

        filename = self.all_music[name]
        self.log.info(f"try get_filename. filename:{filename}")

        if os.path.exists(filename):
            return filename
        return ""

    def is_music_exist(self, name):
        """判断本地音乐是否存在，网络歌曲不判断

        Args:
            name: 音乐名称

        Returns:
            bool: 是否存在
        """
        if name not in self.all_music:
            return False
        if self.is_web_music(name):
            return True
        filename = self.get_filename(name)
        if filename:
            return True
        return False

    def is_web_radio_music(self, name):
        """是否是网络电台

        Args:
            name: 音乐名称

        Returns:
            bool: 是否是网络电台
        """
        return name in self._all_radio

    # 是否是在线音乐
    @staticmethod
    def is_online_music(cur_playlist):
        # cur_playlist 开头是 '_online_' 则表示online
        return cur_playlist.startswith("_online_")

    def is_web_music(self, name):
        """是否是网络歌曲

        Args:
            name: 音乐名称

        Returns:
            bool: 是否是网络歌曲
        """
        if name not in self.all_music:
            return False
        url = self.all_music[name]
        return url.startswith(("http://", "https://", "self://"))

    def is_need_use_play_music_api(self, name):
        """是否是需要通过api获取播放链接的网络歌曲

        Args:
            name: 音乐名称

        Returns:
            bool: 是否需要通过API获取
        """
        return bool(self._get_cached_web_music_api(name))

    # ==================== 标签管理 ====================

    def _resolve_query_name(self, name="", *, entity_id="", playlist_name="") -> str:
        music_name = str(name or "").strip()
        requested_entity_id = str(entity_id or "").strip()
        requested_playlist = str(playlist_name or "").strip()
        if requested_entity_id:
            music_name = self.get_legacy_name_for_entity(
                requested_entity_id,
                playlist_name=requested_playlist,
            )
        return str(music_name or "").strip()

    async def get_music_url_by_entity(self, entity_id, *, playlist_name=""):
        music_name = self._resolve_query_name(
            entity_id=entity_id,
            playlist_name=playlist_name,
        )
        return await self.get_music_url(music_name)

    async def get_music_tags_by_entity(self, entity_id, *, playlist_name=""):
        music_name = self._resolve_query_name(
            entity_id=entity_id,
            playlist_name=playlist_name,
        )
        return await self.get_music_tags(music_name)

    def set_music_tag_by_entity(self, entity_id, info, *, playlist_name=""):
        music_name = self._resolve_query_name(
            entity_id=entity_id,
            playlist_name=playlist_name,
        )
        return self.set_music_tag(music_name, info)

    async def get_music_tags(self, name):
        """获取音乐标签信息

        Args:
            name: 音乐名称

        Returns:
            dict: 标签信息字典
        """
        entity_id = self._entity_id_for_name(name)
        tags = self._get_cached_tags(name, entity_id=entity_id)
        picture = tags["picture"]

        if picture:
            if picture.startswith(self.config.picture_cache_path):
                picture = picture[len(self.config.picture_cache_path) :]
            picture = picture.replace("\\", "/")
            if picture.startswith("/"):
                picture = picture[1:]
            encoded_name = urllib.parse.quote(picture)
            tags["picture"] = try_add_access_control_param(
                self.config,
                f"{self.config.get_public_base_url()}/picture/{encoded_name}",
            )

        # 如果是网络音乐，获取时长
        if self.is_web_music(name):
            try:
                duration = await self.get_music_duration(name)
                if duration > 0:
                    tags["duration"] = duration
            except Exception as e:
                self.log.exception(f"获取网络音乐 {name} 时长失败: {e}")
        return tags

    def set_music_tag(self, name, info):
        """修改标签信息

        Args:
            name: 音乐名称
            info: 标签信息对象

        Returns:
            str: 操作结果消息
        """
        if self._tag_generation_task:
            self.log.info("tag 更新中，请等待")
            return "Tag generation task running"

        entity_id = str(getattr(info, "entity_id", "") or self._entity_id_for_name(name) or "").strip()
        tags = self._get_cached_tags(name, entity_id=entity_id)
        tags["title"] = info.title
        tags["artist"] = info.artist
        tags["album"] = info.album
        tags["year"] = info.year
        tags["genre"] = info.genre
        tags["lyrics"] = info.lyrics

        file_path = self.all_music[name]
        if info.picture:
            tags["picture"] = save_picture_by_base64(
                info.picture, self.config.picture_cache_path, file_path
            )

        if self.config.enable_save_tag and (not self.is_web_music(name)):
            set_music_tag_to_file(file_path, Metadata(tags))

        self._set_cached_tags(name, tags, entity_id=entity_id)
        self.try_save_tag_cache()
        return "OK"

    async def get_music_duration(self, name: str) -> float:
        """获取歌曲时长

        优先从缓存中读取，如果缓存中没有则获取并缓存
        注意：此方法不处理在线音乐，在线音乐的时长获取在 music_url 中处理

        Args:
            name: 歌曲名称

        Returns:
            float: 歌曲时长（秒），失败返回 0
        """
        # 检查歌曲是否存在
        if name not in self.all_music:
            self.log.warning(f"歌曲 {name} 不存在")
            return 0

        # 电台直接返回 0
        if self.is_web_radio_music(name):
            self.log.info(f"电台 {name} 不会有播放时长")
            return 0

        # 网络音乐：使用内存缓存
        if self.is_web_music(name):
            # 先检查内存缓存
            cached_duration = self._get_cached_duration(name)
            if cached_duration > 0:
                self.log.debug(f"从内存缓存读取网络音乐 {name} 时长: {cached_duration} 秒")
                return cached_duration

            # 缓存中没有，获取时长
            try:
                url, _ = await self._get_web_music_url(name)
                duration, _ = await get_web_music_duration(url, self.config)
                self.log.info(f"网络音乐 {name} 时长: {duration} 秒")

                # 存入内存缓存（不持久化）
                if duration > 0:
                    self._set_cached_duration(name, duration)
                    self.log.info(f"已缓存网络音乐 {name} 时长到内存: {duration} 秒")

                return duration
            except Exception as e:
                self.log.exception(f"获取网络音乐 {name} 时长失败: {e}")
                return 0

        # 本地音乐：使用持久化缓存
        # 先检查缓存中是否有时长信息
        cached_tags = self._get_cached_tags(name)
        duration = cached_tags.get("duration", 0)
        if duration > 0:
            self.log.debug(f"从缓存读取本地音乐 {name} 时长: {duration} 秒")
            return duration

        # 缓存中没有，需要获取时长
        duration = 0
        try:
            filename = self.all_music[name]
            if os.path.exists(filename):
                duration = await get_local_music_duration(filename, self.config)
                self.log.info(f"本地音乐 {name} 时长: {duration} 秒")
            else:
                self.log.warning(f"本地音乐文件 {filename} 不存在")

            # 获取到时长后，更新到缓存并持久化
            if duration > 0:
                tags = self._get_cached_tags(name)
                if not tags:
                    tags = asdict(Metadata())
                tags["duration"] = duration
                self._set_cached_tags(name, tags)
                # 保存缓存
                self.try_save_tag_cache()
                self.log.info(f"已缓存本地音乐 {name} 时长: {duration} 秒")

        except Exception as e:
            self.log.exception(f"获取本地音乐 {name} 时长失败: {e}")

        return duration

    def refresh_music_tag(self):
        """刷新音乐标签（给前端调用）"""
        if not self.ensure_single_thread_for_tag():
            return

        filename = self.config.tag_cache_path
        if filename is not None:
            # 清空 cache
            with open(filename, "w", encoding="utf-8") as f:
                json.dump({}, f, ensure_ascii=False, indent=2)
            self.log.info("刷新：已清空 tag cache")
        else:
            self.log.info("刷新：tag cache 未启用")

        # TODO: 优化性能？
        # TODO 如何安全的清空 picture_cache_path
        self.all_music_tags = {}  # 需要清空内存残留
        self._entity_music_tags = {}
        self.clear_web_music_duration_cache()  # 清空网络音乐时长缓存
        self.try_gen_all_music_tag()
        self.log.info("刷新：已启动重建 tag cache")

    def try_load_from_tag_cache(self):
        """从缓存加载标签

        Returns:
            dict: 标签缓存字典
        """
        filename = self.config.tag_cache_path
        tag_cache = {}

        try:
            if filename is not None:
                if os.path.exists(filename):
                    with open(filename, encoding="utf-8") as f:
                        tag_cache = json.load(f)
                    self.log.info(f"已从【{filename}】加载 tag cache")
                else:
                    self.log.info(f"【{filename}】tag cache 已启用，但文件不存在")
            else:
                self.log.info("加载：tag cache 未启用")
        except Exception as e:
            self.log.exception(f"Execption {e}")

        return tag_cache

    def try_save_tag_cache(self):
        """保存标签缓存"""
        filename = self.config.tag_cache_path
        if filename is not None:
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(self.all_music_tags, f, ensure_ascii=False, indent=2)
            self.log.info(f"保存：tag cache 已保存到【{filename}】")
        else:
            self.log.info("保存：tag cache 未启用")

    def ensure_single_thread_for_tag(self):
        """确保标签生成任务单线程执行

        Returns:
            bool: 是否可以执行新任务
        """
        if self._tag_generation_task:
            self.log.info("tag 更新中，请等待")
        return not self._tag_generation_task

    def try_gen_all_music_tag(self, only_items=None):
        """尝试生成所有音乐标签

        Args:
            only_items: 仅更新指定的音乐项，None表示更新全部
        """
        if self.ensure_single_thread_for_tag():
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                # 没有运行中的事件循环，跳过
                self.log.info("协程时间循环未启动")
                return
            asyncio.ensure_future(self._gen_all_music_tag(only_items))
            self.log.info("启动后台构建 tag cache")

    @staticmethod
    def _file_tag_signature(file_path: str) -> dict:
        try:
            return {
                "__source_mtime": int(os.path.getmtime(file_path)),
                "__source_size": int(os.path.getsize(file_path)),
            }
        except OSError:
            return {"__source_mtime": 0, "__source_size": 0}

    def _need_refresh_tag(self, tag: dict, file_path: str) -> bool:
        sig = self._file_tag_signature(file_path)
        return (
            int(tag.get("__source_mtime", 0)) != sig["__source_mtime"]
            or int(tag.get("__source_size", 0)) != sig["__source_size"]
        )

    async def _gen_all_music_tag(self, only_items=None):
        """生成所有音乐标签（异步）

        Args:
            only_items: 仅更新指定的音乐项，None表示更新全部
        """
        self._tag_generation_task = True
        if only_items is None:
            only_items = self.all_music  # 默认更新全部

        all_music_tags = self.try_load_from_tag_cache()
        all_music_tags.update(self.all_music_tags)  # 保证最新

        ignore_tag_absolute_dirs = self.config.get_ignore_tag_dirs()
        self.log.info(f"ignore_tag_absolute_dirs: {ignore_tag_absolute_dirs}")

        scanned = 0
        refreshed = 0
        skipped = 0
        build_start = time.perf_counter()

        for name, file_or_url in only_items.items():
            # 跳过网络音乐
            if self.is_web_music(name):
                continue
            scanned += 1
            start = time.perf_counter()
            need_refresh = True
            if name in all_music_tags and not self._need_refresh_tag(
                all_music_tags[name], file_or_url
            ):
                need_refresh = False
            if need_refresh:
                try:
                    if os.path.exists(file_or_url) and not_in_dirs(
                        file_or_url, ignore_tag_absolute_dirs
                    ):
                        all_music_tags[name] = extract_audio_metadata(
                            file_or_url, self.config.picture_cache_path
                        )
                        all_music_tags[name].update(self._file_tag_signature(file_or_url))
                        refreshed += 1
                    else:
                        self.log.info(f"{name} {file_or_url} 无法更新 tag")
                except BaseException as e:
                    self.log.exception(f"{e} {file_or_url} error {type(file_or_url)}!")
            else:
                skipped += 1

            # 获取并缓存歌曲时长（仅本地音乐）
            if name in all_music_tags and "duration" not in all_music_tags[name]:
                try:
                    duration = await self.get_music_duration(name)
                    if duration > 0:
                        all_music_tags[name]["duration"] = duration
                except Exception as e:
                    self.log.warning(f"获取歌曲 {name} 时长失败: {e}")

            if (time.perf_counter() - start) < 1:
                await asyncio.sleep(0.001)
            else:
                # 处理一首歌超过1秒，则等1秒，解决挂载网盘卡死的问题
                await asyncio.sleep(1)

        # 全部更新结束后，一次性赋值
        self.all_music_tags = all_music_tags
        self._entity_music_tags = {}
        for legacy_name, tag_data in all_music_tags.items():
            entity_id = self._entity_id_for_name(legacy_name)
            if entity_id:
                self._entity_music_tags[entity_id] = copy.copy(tag_data)
        # 刷新 tag cache
        self.try_save_tag_cache()
        self._tag_generation_task = False
        elapsed_ms = int((time.perf_counter() - build_start) * 1000)
        self.log.info(
            "tag 更新完成 benchmark scanned=%s refreshed=%s skipped=%s cost_ms=%s",
            scanned,
            refreshed,
            skipped,
            elapsed_ms,
        )

    # ==================== 辅助方法 ====================

    def get_music_list(self):
        """获取所有播放列表

        Returns:
            dict: 播放列表字典
        """
        return self.music_list

    def get_all_music(self):
        """获取所有音乐

        Returns:
            dict: 所有音乐字典
        """
        return self.all_music

    def get_web_music_api(self):
        """获取网络音乐API配置

        Returns:
            dict: 网络音乐API配置字典
        """
        return self._web_music_api

    def get_web_music_api_by_entity(self, entity_id):
        return self._get_cached_web_music_api(entity_id=entity_id)

    def get_all_radio(self):
        """获取所有电台

        Returns:
            dict: 所有电台字典
        """
        return self._all_radio

    def clear_web_music_duration_cache(self):
        """清空网络音乐时长缓存

        清空内存中的网络音乐时长缓存，不影响本地音乐的缓存
        """
        self._web_music_duration_cache = {}
        self._entity_web_music_duration_cache = {}
        self.log.info("已清空网络音乐时长缓存")

    # ==================== URL处理方法 ====================

    async def get_music_url(self, name):
        """获取音乐播放地址

        Args:
            name: 歌曲名称

        Returns:
            tuple: (播放地址, 原始地址) - 网络音乐时可能有原始地址
        """
        self.log.info(f"get_music_url name:{name}")
        if self.is_web_music(name):
            return await self._get_web_music_url(name)
        return self._get_local_music_url(name), None

    async def _get_web_music_url(self, name):
        """获取网络音乐播放地址

        Args:
            name: 歌曲名称

        Returns:
            tuple: (播放地址, 原始地址)
        """
        self.log.info("in _get_web_music_url")
        url = self.all_music[name]
        self.log.info(f"get_music_url web music. name:{name}, url:{url}")

        # 需要通过API获取真实播放地址
        if self.is_need_use_play_music_api(name):
            url = await self._get_url_from_api(name, url)
            if not url:
                return "", None

        def is_jellyfin_url(u: str) -> bool:
            return self.is_jellyfin_url(u)

        def is_private_base_url() -> bool:
            import ipaddress

            base = (self.config.jellyfin_base_url or "").rstrip("/")
            if not base:
                return False
            try:
                p = urlparse(base)
                host = p.hostname or ""
                if not host:
                    return False
                if host in ("localhost",):
                    return True
                # If host is an IP, check RFC1918/loopback/link-local
                try:
                    ip = ipaddress.ip_address(host)
                    return bool(ip.is_private or ip.is_loopback or ip.is_link_local)
                except ValueError:
                    # Domain name: treat as public
                    return False
            except Exception:
                return False

        # 是否需要代理
        jellyfin_mode = (
            getattr(self.config, "jellyfin_proxy_mode", "auto") or "auto"
        ).lower()

        # For Jellyfin, we support an internal auto-fallback strategy:
        # - on: always proxy
        # - off: never proxy
        # - auto: try direct first; if playback fails, device layer will retry via proxy
        jellyfin_is = is_jellyfin_url(url)
        if jellyfin_is and jellyfin_mode == "on":
            is_radio = self.is_web_radio_music(name)
            proxy_url = self._get_proxy_url(url, is_radio=is_radio)
            return proxy_url, url

        needs_proxy = self.config.web_music_proxy or url.startswith("self://")
        if needs_proxy:
            is_radio = self.is_web_radio_music(name)
            proxy_url = self._get_proxy_url(url, is_radio=is_radio)
            return proxy_url, url

        # Direct play.
        if jellyfin_is and jellyfin_mode == "auto":
            # Return origin URL so device layer can retry via proxy if needed.
            return url, url
        return url, None

    def is_jellyfin_url(self, u: str) -> bool:
        """Best-effort check whether a URL belongs to the configured Jellyfin.

        Users may configure base_url with/without scheme, port, or a base path.
        We compare hostname (and port if provided) and ensure the path is under
        the base path (if any).
        """

        base_raw = (self.config.jellyfin_base_url or "").strip()
        if not base_raw or not u:
            return False

        def _ensure_scheme(s: str) -> str:
            if "://" in s:
                return s
            return "http://" + s

        try:
            base = urlparse(_ensure_scheme(base_raw))
            cand = urlparse(u)
            if not base.hostname or not cand.hostname:
                return False

            base_host = base.hostname.strip().lower().rstrip(".")
            cand_host = cand.hostname.strip().lower().rstrip(".")
            if base_host != cand_host:
                return False

            # If base explicitly sets a port, require it to match.
            if base.port is not None and base.port != cand.port:
                return False

            base_path = (base.path or "").rstrip("/")
            if base_path:
                cand_path = cand.path or ""
                if cand_path != base_path and not cand_path.startswith(base_path + "/"):
                    return False
            return True
        except Exception:
            return False

    def get_proxy_url(self, origin_url: str, *, name: str = "") -> str:
        """Public wrapper to build a proxy URL for a given origin URL."""
        is_radio = False
        try:
            if name:
                is_radio = self.is_web_radio_music(name)
        except Exception:
            is_radio = False
        return self._get_proxy_url(origin_url, is_radio=is_radio)

    async def _get_url_from_api(self, name, url):
        """通过API获取真实播放地址

        Args:
            name: 歌曲名称
            url: 原始URL

        Returns:
            str: 真实播放地址，失败返回空字符串
        """
        api_payload = self._get_cached_web_music_api(name) or {}
        headers = api_payload.get("headers", {})
        url = await self.url_cache.get(url, headers, self.config)
        if not url:
            self.log.error(f"_get_url_from_api use api fail. name:{name}, url:{url}")
        return url

    def _get_proxy_url(self, origin_url, is_radio=None):
        """获取代理URL

        Args:
            origin_url: 原始URL
            is_radio: 是否为电台直播流

        Returns:
            str: 代理URL
        """
        token = self.register_proxy_url(origin_url)
        urlb64 = f"t.{token}"

        # 使用路径参数方式，避免查询参数转义问题
        proxy_type = "radio" if is_radio else "music"
        proxy_url = f"{self.config.get_public_base_url()}/proxy/{proxy_type}?urlb64={urlb64}"
        self.log.info(f"Using proxy url: {proxy_url}")
        return proxy_url

    def register_proxy_url(self, origin_url: str, ttl_seconds: int = 7200) -> str:
        token = uuid4().hex[:16]
        expires_at = int(time.time()) + max(60, int(ttl_seconds))
        with self._proxy_url_tokens_lock:
            self._proxy_url_tokens[token] = (str(origin_url), expires_at)
            self._cleanup_proxy_tokens_locked(max_items=2048)
        return token

    def resolve_proxy_url_token(self, token: str) -> str:
        now_ts = int(time.time())
        with self._proxy_url_tokens_lock:
            item = self._proxy_url_tokens.get(token)
            if not item:
                return ""
            url, expires_at = item
            if expires_at <= now_ts:
                self._proxy_url_tokens.pop(token, None)
                return ""
            return str(url)

    def _cleanup_proxy_tokens_locked(self, max_items: int = 2048) -> None:
        now_ts = int(time.time())
        expired = [k for k, (_, exp) in self._proxy_url_tokens.items() if int(exp) <= now_ts]
        for k in expired:
            self._proxy_url_tokens.pop(k, None)
        if len(self._proxy_url_tokens) <= max_items:
            return
        items = sorted(self._proxy_url_tokens.items(), key=lambda kv: int(kv[1][1]))
        for k, _ in items[: max(0, len(items) - max_items)]:
            self._proxy_url_tokens.pop(k, None)

    def _get_local_music_url(self, name):
        """获取本地音乐播放地址

        Args:
            name: 歌曲名称

        Returns:
            str: 本地音乐播放URL
        """
        filename = self.get_filename(name)
        self.log.info(
            f"_get_local_music_url local music. name:{name}, filename:{filename}"
        )
        return self._get_file_url(filename)

    def _get_file_url(self, filepath):
        """根据文件路径生成可访问的URL

        Args:
            filepath: 文件的完整路径

        Returns:
            str: 文件访问URL
        """
        filename = filepath

        # 处理文件路径
        if filename.startswith(self.config.music_path):
            filename = filename[len(self.config.music_path) :]
        filename = filename.replace("\\", "/")
        if filename.startswith("/"):
            filename = filename[1:]

        self.log.info(f"_get_file_url filepath:{filepath}, filename:{filename}")

        # 构造URL
        encoded_name = urllib.parse.quote(filename)
        url = f"{self.config.get_public_base_url()}/music/{encoded_name}"
        return try_add_access_control_param(self.config, url)

    @staticmethod
    async def get_play_url(proxy_url):
        """获取播放URL

        Args:
            proxy_url: 代理URL

        Returns:
            str: 最终重定向的URL
        """
        import aiohttp

        async with aiohttp.ClientSession() as session:
            async with session.get(proxy_url) as response:
                # 获取最终重定向的 URL
                return str(response.url)

    def expand_self_url(self, origin_url):
        parsed_url = urlparse(origin_url)
        self.log.info(f"链接处理前 ${parsed_url}")
        if parsed_url.scheme != "self":
            return parsed_url, origin_url

        url = f"{self.config.get_public_base_url()}{parsed_url.path}"
        if parsed_url.query:
            url += f"?{parsed_url.query}"
        if parsed_url.fragment:
            url += f"#{parsed_url.fragment}"

        return urlparse(url), url
