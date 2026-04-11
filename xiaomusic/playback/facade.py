"""Thin facade adapting API models to PlaybackCoordinator."""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from uuid import uuid4

from xiaomusic.adapters.mina import MinaTransport
from xiaomusic.adapters.miio import MiioTransport
from xiaomusic.adapters.sources import register_default_source_plugins
from xiaomusic.constants.api_fields import DEVICE_ID, REQUEST_ID
from xiaomusic.core.coordinator import PlaybackCoordinator
from xiaomusic.managers.source_plugin_manager import SourcePluginManager
from xiaomusic.core.delivery import DeliveryAdapter
from xiaomusic.core.device import DeviceRegistry
from xiaomusic.core.errors import (
    DeviceNotFoundError,
    InvalidRequestError,
    TransportError,
)
from xiaomusic.core.models import MediaRequest, PlayOptions
from xiaomusic.core.transport import TransportPolicy, TransportRouter


LOG = logging.getLogger("xiaomusic.playback.facade")

if TYPE_CHECKING:
    pass


def build_track_id(playlist_name: str, index: int | None, title: str) -> str:
    """Build stable track identity for playlist items.

    This function generates a consistent track ID that matches the logic
    used in build_player_state_snapshot(). Both must use the same
    algorithm to ensure frontend can match track.id with playlist items.

    Args:
        playlist_name: The playlist name (context_id)
        index: The position in playlist, or None if unknown
        title: The song title

    Returns:
        16-character hex string MD5 hash
    """
    track_key = (
        f"{playlist_name or 'default'}:"
        f"{index if index is not None else -1}:"
        f"{title or ''}"
    )
    return hashlib.md5(track_key.encode()).hexdigest()[:16]


class PlaybackFacade:
    """Keep API layer thin while exposing stable runtime methods."""

    def __init__(
        self,
        xiaomusic,
        runtime_provider: Callable[[], Any] | None = None,
        source_plugin_manager: SourcePluginManager | None = None,
    ) -> None:
        self.xiaomusic = xiaomusic
        self._runtime_provider = runtime_provider
        self._core_coordinator: PlaybackCoordinator | None = None
        self._core_registry_version: int | None = None
        self._device_track_source_hints: dict[str, dict[str, str]] = {}
        self._source_plugin_manager = source_plugin_manager or getattr(
            self.xiaomusic, "source_plugin_manager", None
        )

    def _get_source_plugin_manager(self) -> SourcePluginManager:
        if self._source_plugin_manager is not None:
            return self._source_plugin_manager
        config = getattr(self.xiaomusic, "config", None)
        conf_path = getattr(config, "conf_path", ".") if config is not None else "."
        manager = SourcePluginManager(
            register_defaults=lambda registry: register_default_source_plugins(
                registry,
                self.xiaomusic,
                runtime_provider=self._runtime_provider,
            ),
            plugins_dir=str(Path(conf_path) / "source_plugins"),
        )
        setattr(self.xiaomusic, "source_plugin_manager", manager)
        self._source_plugin_manager = manager
        return manager

    def _core(self) -> PlaybackCoordinator:
        if self._core_coordinator is not None and self._core_registry_version is None:
            return self._core_coordinator

        source_plugin_manager = self._get_source_plugin_manager()
        registry_version = source_plugin_manager.registry_version
        if (
            self._core_coordinator is not None
            and self._core_registry_version == registry_version
        ):
            return self._core_coordinator

        source_registry = source_plugin_manager.get_active_registry()
        device_registry = DeviceRegistry(self.xiaomusic)
        proxy_builder: Callable[[str, str], str] | None = None
        raw_proxy_builder = getattr(
            getattr(self.xiaomusic, "music_library", None), "get_proxy_url", None
        )
        if callable(raw_proxy_builder):
            proxy_builder = lambda origin_url, title: str(
                raw_proxy_builder(origin_url, name=title)
            )
        delivery_adapter = DeliveryAdapter(proxy_url_builder=proxy_builder)
        router = TransportRouter(policy=TransportPolicy())
        router.register_transport(MinaTransport(self.xiaomusic))
        router.register_transport(MiioTransport(self.xiaomusic))
        self._core_coordinator = PlaybackCoordinator(
            source_registry=source_registry,
            device_registry=device_registry,
            delivery_adapter=delivery_adapter,
            transport_router=router,
            playback_status_provider=getattr(self.xiaomusic, "get_player_status", None),
        )
        self._core_registry_version = registry_version
        return self._core_coordinator

    @staticmethod
    def _serialize(obj: Any) -> Any:
        if is_dataclass(obj) and not isinstance(obj, type):
            return asdict(obj)
        if isinstance(obj, list):
            return [PlaybackFacade._serialize(item) for item in obj]
        if isinstance(obj, dict):
            return {str(k): PlaybackFacade._serialize(v) for k, v in obj.items()}
        return obj

    @staticmethod
    def _normalize_hint(source_hint: str | None) -> str | None:
        hint = str(source_hint or "auto").strip().lower()
        return None if hint in {"", "auto"} else hint

    @staticmethod
    def _validate_device_id(device_id: str) -> str:
        did = str(device_id or "").strip()
        if not did:
            raise InvalidRequestError("device_id is required")
        return did

    @staticmethod
    def _normalize_track_source_value(source: Any) -> str | None:
        value = str(source or "").strip().lower()
        if value in {"local_library", "jellyfin", "site_media", "direct_url"}:
            return value
        return None

    def _remember_device_track_source(
        self,
        *,
        device_id: str,
        source: str | None,
        track_title: str = "",
        context_id: str = "",
        play_session_id: str = "",
    ) -> None:
        normalized = self._normalize_track_source_value(source)
        if normalized is None:
            return
        self._device_track_source_hints[device_id] = {
            "source": normalized,
            "track_title": str(track_title or "").strip(),
            "context_id": str(context_id or "").strip(),
            "play_session_id": str(play_session_id or "").strip(),
        }

    def _resolve_context_source(self, context_id: str) -> str | None:
        playlist_name = str(context_id or "").strip()
        if not playlist_name:
            return None

        config = getattr(self.xiaomusic, "config", None)
        raw_music_list_json = getattr(config, "music_list_json", "") if config else ""
        if not raw_music_list_json:
            return None

        try:
            import json

            music_lists = json.loads(raw_music_list_json)
        except Exception:
            return None

        if not isinstance(music_lists, list):
            return None

        for item in music_lists:
            if not isinstance(item, dict):
                continue
            if str(item.get("name") or "").strip() != playlist_name:
                continue
            return self._normalize_track_source_value(item.get("source"))
        return None

    def _infer_local_library_source(self, track_title: str) -> str | None:
        title = str(track_title or "").strip()
        if not title:
            return None

        music_library = getattr(self.xiaomusic, "music_library", None)
        all_music = getattr(music_library, "all_music", None)
        if not isinstance(all_music, dict) or title not in all_music:
            return None

        is_web_music = getattr(music_library, "is_web_music", None)
        if callable(is_web_music):
            try:
                if is_web_music(title):
                    return None
            except Exception:
                return None
        return "local_library"

    def _resolve_track_source(
        self,
        *,
        device_id: str,
        track_title: str,
        context_id: str,
        raw_source: Any,
        play_session_id: str = "",
    ) -> str | None:
        context_source = self._resolve_context_source(context_id)
        if context_source is not None:
            return context_source

        cached = self._device_track_source_hints.get(device_id)
        if isinstance(cached, dict):
            cached_source = self._normalize_track_source_value(cached.get("source"))
            cached_title = str(cached.get("track_title") or "").strip()
            cached_context_id = str(cached.get("context_id") or "").strip()
            cached_play_session_id = str(cached.get("play_session_id") or "").strip()
            if cached_source is not None and (
                (context_id and cached_context_id == context_id)
                or (track_title and cached_title == track_title)
                or (
                    play_session_id
                    and cached_play_session_id == play_session_id
                    and not self._normalize_track_source_value(raw_source)
                )
            ):
                return cached_source

        local_source = self._infer_local_library_source(track_title)
        if local_source is not None:
            return local_source

        normalized_raw = self._normalize_track_source_value(raw_source)
        if normalized_raw is not None:
            return normalized_raw

        raw_value = str(raw_source or "").strip()
        return raw_value or None

    @staticmethod
    def _validate_query(query: str) -> str:
        q = str(query or "").strip()
        if not q:
            raise InvalidRequestError("query is required")
        return q

    @staticmethod
    def _playlist_context(options: PlayOptions, query: str) -> tuple[str, str] | None:
        context_hint = (
            options.context_hint if isinstance(options.context_hint, dict) else {}
        )
        payload = (
            options.source_payload if isinstance(options.source_payload, dict) else {}
        )
        context_type = (
            str(context_hint.get("context_type") or payload.get("context_type") or "")
            .strip()
            .lower()
        )
        playlist_name = str(
            context_hint.get("context_name")
            or context_hint.get("context_id")
            or payload.get("playlist_name")
            or payload.get("context_name")
            or ""
        ).strip()
        music_name = str(
            payload.get("music_name")
            or payload.get("track_name")
            or options.title
            or query
            or ""
        ).strip()
        if context_type != "playlist" or not playlist_name or not music_name:
            return None
        return playlist_name, music_name

    async def play(
        self,
        *,
        device_id: str,
        query: str,
        source_hint: str = "auto",
        options: PlayOptions | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        did = self._validate_device_id(device_id)
        q = self._validate_query(query)
        opts = options or PlayOptions()
        normalized_hint = self._normalize_hint(source_hint)
        if not bool(getattr(self.xiaomusic, "did_exist", lambda _did: False)(did)):
            raise DeviceNotFoundError("device not found")

        request_id_value = str(request_id or uuid4().hex[:16])
        playlist_context = (
            self._playlist_context(opts, q)
            if normalized_hint == "local_library"
            else None
        )
        if playlist_context is not None:
            playlist_name, music_name = playlist_context
            merged_source_payload = (
                dict(opts.source_payload) if isinstance(opts.source_payload, dict) else {}
            )
            merged_source_payload.update(
                {
                    "source": "local_library",
                    "context_type": "playlist",
                    "playlist_name": playlist_name,
                    "context_name": playlist_name,
                    "music_name": music_name,
                    "track_name": music_name,
                }
            )
            merged_context_hint = (
                dict(opts.context_hint) if isinstance(opts.context_hint, dict) else {}
            )
            merged_context_hint.update(
                {
                    "context_type": "playlist",
                    "context_id": playlist_name,
                    "context_name": playlist_name,
                }
            )
            opts = PlayOptions(
                start_position=opts.start_position,
                shuffle=opts.shuffle,
                loop=opts.loop,
                volume=opts.volume,
                timeout=opts.timeout,
                resolve_timeout_seconds=opts.resolve_timeout_seconds,
                no_cache=opts.no_cache,
                prefer_proxy=opts.prefer_proxy,
                confirm_start=opts.confirm_start,
                confirm_start_delay_ms=opts.confirm_start_delay_ms,
                confirm_start_retries=opts.confirm_start_retries,
                confirm_start_interval_ms=opts.confirm_start_interval_ms,
                source_payload=merged_source_payload,
                context_hint=merged_context_hint,
                media_id=opts.media_id,
                title=music_name,
            )

        req = MediaRequest.from_payload(
            request_id=request_id_value,
            source_hint=normalized_hint,
            query=q,
            device_id=did,
            options=opts,
            include_prefer_proxy=True,
        )

        try:
            result = await self._core().play(req, device_id=did)
        except Exception as exc:
            self._record_playback_capability_verify(
                result="failed",
                verify_method="playback_dispatch",
                playback_capability_level="actual_playback_path",
                transport="mina",
                error_code=exc.__class__.__name__,
                error_message=str(exc),
            )
            raise
        prepared = result["prepared_stream"]
        resolved = result["resolved_media"]
        dispatch = result["dispatch"]
        outcome = result.get("outcome")
        accepted = (
            bool(getattr(outcome, "accepted", False)) if outcome is not None else False
        )
        started = (
            bool(getattr(outcome, "started", False)) if outcome is not None else False
        )
        verify_result = "ok" if started else "failed"
        self._record_playback_capability_verify(
            result=verify_result,
            verify_method="playback_dispatch",
            playback_capability_level="actual_playback_path",
            transport=dispatch.transport,
            error_code="" if started else "dispatch_not_started",
            error_message=f"accepted={accepted} started={started}",
        )
        playlist_context = self._playlist_context(opts, resolved.title)
        current_play_session_id = ""
        try:
            device_player = getattr(
                getattr(self.xiaomusic, "device_manager", None), "devices", {}
            ).get(did)
            if device_player is not None:
                sid = getattr(device_player, "_play_session_id", 0)
                current_play_session_id = f"sess_{sid}"
        except Exception:
            current_play_session_id = ""
        self._remember_device_track_source(
            device_id=did,
            source=resolved.source or prepared.source,
            track_title=resolved.title,
            context_id=playlist_context[0] if playlist_context is not None else "",
            play_session_id=current_play_session_id,
        )
        return {
            "status": "playing",
            DEVICE_ID: did,
            "source_plugin": prepared.source,
            "transport": dispatch.transport,
            REQUEST_ID: req.request_id,
            "media": {
                "media_id": resolved.media_id,
                "title": resolved.title,
                "stream_url": prepared.final_url,
                "is_live": bool(resolved.is_live),
            },
            "extra": {
                "dispatch": dispatch.data,
                "delivery_plan": self._serialize(result.get("delivery_plan")),
                "playback_outcome": self._serialize(result.get("outcome")),
            },
        }

    def _record_playback_capability_verify(
        self,
        *,
        result: str,
        verify_method: str,
        playback_capability_level: str,
        transport: str,
        error_code: str = "",
        error_message: str = "",
    ) -> None:
        auth_manager = getattr(self.xiaomusic, "auth_manager", None)
        recorder = (
            getattr(auth_manager, "record_playback_capability_verify", None)
            if auth_manager
            else None
        )
        if callable(recorder):
            recorder(
                result=result,
                verify_method=verify_method,
                playback_capability_level=playback_capability_level,
                transport=transport,
                error_code=error_code,
                error_message=error_message,
            )

    async def resolve(
        self,
        *,
        query: str,
        source_hint: str = "auto",
        options: PlayOptions | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        q = self._validate_query(query)
        opts = options or PlayOptions()
        normalized_hint = self._normalize_hint(source_hint)
        req = MediaRequest.from_payload(
            request_id=str(request_id or uuid4().hex[:16]),
            source_hint=normalized_hint,
            query=q,
            device_id=None,
            options=opts,
            include_prefer_proxy=False,
        )
        result = await self._core().resolve(req)
        resolved = result["resolved_media"]
        return {
            "resolved": True,
            "source_plugin": result["source_plugin"],
            REQUEST_ID: req.request_id,
            "media": {
                "media_id": resolved.media_id,
                "title": resolved.title,
                "stream_url": resolved.stream_url,
                "source": resolved.source,
                "is_live": bool(resolved.is_live),
            },
            "extra": {},
        }

    async def stop(
        self, device_id: str, request_id: str | None = None
    ) -> dict[str, Any]:
        did = self._validate_device_id(device_id)
        result = await self._core().stop(did)
        return {
            "status": "stopped",
            DEVICE_ID: did,
            "transport": result["transport"],
            REQUEST_ID: str(request_id or uuid4().hex[:16]),
            "extra": {"dispatch": result["dispatch"].data},
        }

    async def previous(
        self, device_id: str, request_id: str | None = None
    ) -> dict[str, Any]:
        did = self._validate_device_id(device_id)
        result = await self._core().previous(did)
        return {
            "status": "ok",
            DEVICE_ID: did,
            "transport": result["transport"],
            REQUEST_ID: str(request_id or uuid4().hex[:16]),
            "action": "previous",
            "extra": {"dispatch": result["dispatch"].data},
        }

    async def next(
        self, device_id: str, request_id: str | None = None
    ) -> dict[str, Any]:
        did = self._validate_device_id(device_id)
        result = await self._core().next(did)
        return {
            "status": "ok",
            DEVICE_ID: did,
            "transport": result["transport"],
            REQUEST_ID: str(request_id or uuid4().hex[:16]),
            "action": "next",
            "extra": {"dispatch": result["dispatch"].data},
        }

    async def pause(
        self, device_id: str, request_id: str | None = None
    ) -> dict[str, Any]:
        did = self._validate_device_id(device_id)
        result = await self._core().pause(did)
        return {
            "status": "paused",
            DEVICE_ID: did,
            "transport": result["transport"],
            REQUEST_ID: str(request_id or uuid4().hex[:16]),
            "extra": {"dispatch": result["dispatch"].data},
        }

    async def resume(
        self, device_id: str, request_id: str | None = None
    ) -> dict[str, Any]:
        did = self._validate_device_id(device_id)
        result = await self._core().resume(did)
        return {
            "status": "resumed",
            DEVICE_ID: did,
            "transport": result["transport"],
            REQUEST_ID: str(request_id or uuid4().hex[:16]),
            "extra": {"dispatch": result["dispatch"].data},
        }

    async def tts(
        self, device_id: str, text: str, request_id: str | None = None
    ) -> dict[str, Any]:
        did = self._validate_device_id(device_id)
        content = str(text or "").strip()
        if not content:
            raise InvalidRequestError("text is required")
        result = await self._core().tts(did, text=content)
        return {
            "status": "ok",
            DEVICE_ID: did,
            "transport": result["transport"],
            REQUEST_ID: str(request_id or uuid4().hex[:16]),
            "extra": {"dispatch": result["dispatch"].data},
        }

    async def set_volume(
        self,
        device_id: str,
        volume: int,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        did = self._validate_device_id(device_id)
        level = int(volume)
        if level < 0 or level > 100:
            raise InvalidRequestError("volume must be in range 0..100")
        result = await self._core().set_volume(did, volume=level)
        return {
            "status": "ok",
            DEVICE_ID: did,
            "transport": result["transport"],
            REQUEST_ID: str(request_id or uuid4().hex[:16]),
            "extra": {"volume": level, "dispatch": result["dispatch"].data},
        }

    async def probe(
        self, device_id: str, request_id: str | None = None
    ) -> dict[str, Any]:
        did = self._validate_device_id(device_id)
        result = await self._core().probe(did)
        reachability = result.get("reachability")
        return {
            "status": "ok",
            DEVICE_ID: did,
            "transport": result["transport"],
            REQUEST_ID: str(request_id or uuid4().hex[:16]),
            "reachable": bool(
                getattr(reachability, "local_reachable", False)
                or getattr(reachability, "cloud_reachable", False)
            ),
            "extra": {
                "dispatch": result["dispatch"].data,
                "reachability": {
                    "ip": getattr(reachability, "ip", ""),
                    "local_reachable": getattr(reachability, "local_reachable", False),
                    "cloud_reachable": getattr(reachability, "cloud_reachable", False),
                    "last_probe_ts": getattr(reachability, "last_probe_ts", 0),
                },
            },
        }

    async def player_state(
        self, device_id: str, request_id: str | None = None
    ) -> dict[str, Any]:
        """@deprecated Use build_player_state_snapshot() for all authoritative state output.

        This legacy compatibility projection is retained only for direct callers and
        tests that have not yet migrated. Public API paths MUST use
        build_player_state_snapshot().
        """
        did = self._validate_device_id(device_id)
        if not bool(getattr(self.xiaomusic, "did_exist", lambda _did: False)(did)):
            raise DeviceNotFoundError("device not found")

        is_playing = bool(getattr(self.xiaomusic, "isplaying", lambda _did: False)(did))
        cur_music = ""
        raw_offset, raw_duration = getattr(
            self.xiaomusic, "get_offset_duration", lambda _did: (0, 0)
        )(did)
        offset = float(raw_offset or 0)
        duration = float(raw_duration or 0)

        raw_status: dict[str, Any] = {}
        try:
            out = await self.xiaomusic.get_player_status(did=did)
            if isinstance(out, dict):
                raw_status = out
        except Exception:
            raw_status = {}

        if int(raw_status.get("status", 0) or 0) == 1:
            is_playing = True

        if is_playing:
            detail = raw_status.get("play_song_detail")
            if isinstance(detail, dict):
                song_title = str(
                    detail.get("audio_name")
                    or detail.get("title")
                    or detail.get("name")
                    or ""
                )
                if song_title:
                    cur_music = song_title
                else:
                    cur_music = str(
                        getattr(self.xiaomusic, "playingmusic", lambda _did: "")(did)
                        or ""
                    )
                try:
                    detail_pos = float(detail.get("position") or 0)
                except Exception:
                    detail_pos = 0.0
                try:
                    detail_dur = float(detail.get("duration") or 0)
                except Exception:
                    detail_dur = 0.0

                if detail_pos > 0 and offset <= 0:
                    offset = detail_pos / 1000.0 if detail_pos > 10000 else detail_pos
                if detail_dur > 0 and duration <= 0:
                    duration = detail_dur / 1000.0 if detail_dur > 10000 else detail_dur
            else:
                # detail 不存在时，尝试从 playingmusic 获取
                cur_music = str(
                    getattr(self.xiaomusic, "playingmusic", lambda _did: "")(did) or ""
                )

        safe_offset = max(0, int(offset))
        safe_duration = max(0, int(duration))
        if safe_duration > 0:
            safe_offset = min(safe_offset, safe_duration)

        # 获取播放上下文信息
        context_type = None
        context_id = None
        context_name = None
        current_index = None
        current_track_id = ""

        # 获取当前播放列表名称
        cur_playlist = ""
        try:
            cur_playlist = str(
                getattr(self.xiaomusic, "get_cur_play_list", lambda _did: "")(did) or ""
            )
        except Exception:
            cur_playlist = ""

        if cur_playlist:
            context_type = "playlist"
            context_id = cur_playlist
            context_name = cur_playlist

        # 获取当前歌曲在播放列表中的索引
        device_player = None
        try:
            device_player = getattr(
                getattr(self.xiaomusic, "device_manager", None), "devices", {}
            ).get(did)
        except Exception:
            device_player = None

        if device_player:
            # 优先使用设备播放器内部的真实当前索引字段
            try:
                real_index = getattr(device_player, "_current_index", -1)
                if real_index >= 0:
                    current_index = real_index
            except (ValueError, AttributeError):
                pass

            # 如果没有真实索引，使用兜底方案
            if current_index is None and cur_music:
                try:
                    play_list = getattr(device_player, "_play_list", [])
                    if play_list and cur_music in play_list:
                        current_index = play_list.index(cur_music)
                except (ValueError, AttributeError):
                    pass

        # 为 cur_music 增加最终兜底
        # 当 is_playing 为 true 但 cur_music 为空时，从 _play_list[current_index] 获取
        if is_playing and not cur_music and device_player and current_index is not None:
            try:
                play_list = getattr(device_player, "_play_list", [])
                if play_list and current_index >= 0 and current_index < len(play_list):
                    cur_music = str(play_list[current_index] or "")
            except (ValueError, AttributeError, IndexError):
                pass

        # 生成 current_track_id
        # 使用 context_id + current_index + cur_music 的组合作为稳定标识
        # 不依赖 cur_music 非空，只要 context_id 或 current_index 可用就生成
        track_key = f"{context_id or 'default'}:{current_index if current_index is not None else -1}:{cur_music or ''}"
        # 使用简单的哈希生成稳定 ID，不使用随机值

        current_track_id = hashlib.md5(track_key.encode()).hexdigest()[:16]

        return {
            "device_id": did,
            "is_playing": bool(is_playing),
            "cur_music": cur_music,
            "offset": safe_offset,
            "duration": safe_duration,
            "current_track_id": current_track_id,
            "current_index": current_index,
            "context_type": context_type,
            "context_id": context_id,
            "context_name": context_name,
            REQUEST_ID: str(request_id or uuid4().hex[:16]),
        }

    async def build_player_state_snapshot(self, device_id: str) -> dict[str, Any]:
        """Unified authoritative player state snapshot builder.

        All state output paths (GET /player/state, GET /player/stream SSE)
        MUST call this method exclusively. No other state assembly is allowed.
        """
        did = self._validate_device_id(device_id)
        if not bool(getattr(self.xiaomusic, "did_exist", lambda _did: False)(did)):
            raise DeviceNotFoundError("device not found")

        snapshot_at_ms = int(time.time() * 1000)
        device_player = None
        try:
            device_player = getattr(
                getattr(self.xiaomusic, "device_manager", None), "devices", {}
            ).get(did)
        except Exception:
            device_player = None

        is_playing = bool(getattr(self.xiaomusic, "isplaying", lambda _did: False)(did))
        raw_offset, raw_duration = getattr(
            self.xiaomusic, "get_offset_duration", lambda _did: (0, 0)
        )(did)
        offset_s = float(raw_offset or 0)
        duration_s = float(raw_duration or 0)

        raw_status: dict[str, Any] = {}
        try:
            out = await self.xiaomusic.get_player_status(did=did)
            if isinstance(out, dict):
                raw_status = out
        except Exception:
            raw_status = {}

        if int(raw_status.get("status", 0) or 0) == 1:
            is_playing = True

        transport_state = self._derive_transport_state(
            device_player, is_playing, raw_status
        )

        track_title = ""
        track_artist: str | None = None
        track_album: str | None = None
        raw_track_source: str | None = None
        track_source: str | None = None
        position_ms = 0
        duration_ms = 0

        if is_playing or transport_state in {"paused", "stopped"}:
            if is_playing:
                detail = raw_status.get("play_song_detail")
                if isinstance(detail, dict):
                    track_title = (
                        str(
                            detail.get("audio_name")
                            or detail.get("title")
                            or detail.get("name")
                            or ""
                        )
                        .strip('"')
                        .strip()
                    )
                    if not track_title:
                        track_title = (
                            str(
                                getattr(
                                    self.xiaomusic, "playingmusic", lambda _did: ""
                                )(did)
                                or ""
                            )
                            .strip('"')
                            .strip()
                        )

                    artist = detail.get("artist") or detail.get("singer")
                    if artist:
                        track_artist = str(artist)
                    album = detail.get("album")
                    if album:
                        track_album = str(album)
                    source = detail.get("source")
                    if source:
                        raw_track_source = str(source)

                    try:
                        detail_pos = float(detail.get("position") or 0)
                    except Exception:
                        detail_pos = 0.0
                    try:
                        detail_dur = float(detail.get("duration") or 0)
                    except Exception:
                        detail_dur = 0.0

                    if detail_pos > 0 and offset_s <= 0:
                        offset_s = (
                            detail_pos / 1000.0 if detail_pos > 10000 else detail_pos
                        )
                    if detail_dur > 0 and duration_s <= 0:
                        duration_s = (
                            detail_dur / 1000.0 if detail_dur > 10000 else detail_dur
                        )
                else:
                    track_title = (
                        str(
                            getattr(self.xiaomusic, "playingmusic", lambda _did: "")(
                                did
                            )
                            or ""
                        )
                        .strip('"')
                        .strip()
                    )

            if not track_title and device_player and transport_state != "idle":
                try:
                    cur_idx = getattr(device_player, "_current_index", -1)
                    play_list = getattr(device_player, "_play_list", [])
                    if (
                        cur_idx >= 0
                        and isinstance(play_list, list)
                        and cur_idx < len(play_list)
                    ):
                        track_title = str(play_list[cur_idx] or "")
                except Exception:
                    pass

        position_ms = max(0, int(offset_s * 1000))
        duration_ms = max(0, int(duration_s * 1000))
        if duration_ms > 0:
            position_ms = min(position_ms, duration_ms)

        context_obj: dict[str, Any] | None = None
        cur_playlist = ""
        try:
            cur_playlist = str(
                getattr(self.xiaomusic, "get_cur_play_list", lambda _did: "")(did) or ""
            )
        except Exception:
            cur_playlist = ""

        play_session_id = ""
        if device_player:
            try:
                sid = getattr(device_player, "_play_session_id", 0)
                play_session_id = f"sess_{sid}"
            except (ValueError, AttributeError):
                play_session_id = ""

        track_source = self._resolve_track_source(
            device_id=did,
            track_title=track_title,
            context_id=cur_playlist,
            raw_source=raw_track_source,
            play_session_id=play_session_id,
        )

        current_index: int | None = None
        if device_player:
            try:
                play_list = getattr(device_player, "_play_list", [])
            except (ValueError, AttributeError):
                play_list = []

            try:
                real_index = getattr(device_player, "_current_index", -1)
                if real_index >= 0 and play_list and track_title:
                    if real_index < len(play_list):
                        list_title = str(play_list[real_index] or "")
                        if list_title == track_title:
                            current_index = real_index
                        else:
                            current_index = None
                    else:
                        current_index = None
                elif real_index >= 0:
                    current_index = real_index
            except (ValueError, AttributeError):
                pass

            if current_index is None and track_title and play_list:
                try:
                    if track_title in play_list:
                        current_index = play_list.index(track_title)
                except (ValueError, AttributeError):
                    pass

        if cur_playlist or current_index is not None:
            context_obj = {
                "id": cur_playlist or "default",
                "name": cur_playlist or "播放列表",
                "current_index": current_index,
            }

        track_id = ""
        if track_title or current_index is not None or cur_playlist:
            track_id = build_track_id(cur_playlist, current_index, track_title)

        track_obj: dict[str, Any] | None = None
        if transport_state not in {"idle"} and (track_title or track_id):
            track_obj = {
                "id": track_id,
                "title": track_title,
            }
            if track_artist is not None:
                track_obj["artist"] = track_artist
            if track_album is not None:
                track_obj["album"] = track_album
            if track_source is not None:
                track_obj["source"] = track_source

        snapshot_key = self._make_snapshot_key(
            device_id=did,
            transport_state=transport_state,
            track_id=track_id,
            play_session_id=play_session_id,
            context_id=cur_playlist,
            current_index=current_index,
            duration_ms=duration_ms,
        )

        revision = self._compute_revision(did, snapshot_key)

        return {
            "device_id": did,
            "revision": revision,
            "play_session_id": play_session_id,
            "transport_state": transport_state,
            "track": track_obj,
            "context": context_obj,
            "position_ms": position_ms,
            "duration_ms": duration_ms,
            "snapshot_at_ms": snapshot_at_ms,
        }

    def _derive_transport_state(
        self,
        device_player: Any,
        is_playing: bool,
        raw_status: dict[str, Any],
    ) -> str:
        """Derive authoritative transport_state from device internals."""
        if device_player is None:
            if is_playing:
                return "playing"
            return "idle"

        try:
            play_fail_cnt = getattr(device_player, "_play_failed_cnt", 0)
            degraded = getattr(device_player, "_degraded", False)
        except (ValueError, AttributeError):
            play_fail_cnt = 0
            degraded = False

        if degraded or play_fail_cnt >= 3:
            return "error"

        if is_playing:
            try:
                last_cmd = getattr(device_player, "_last_cmd", "") or ""
            except (ValueError, AttributeError):
                last_cmd = ""
            if last_cmd in {"stop"}:
                return "switching"
            return "playing"

        try:
            last_cmd = getattr(device_player, "_last_cmd", "") or ""
        except (ValueError, AttributeError):
            last_cmd = ""

        if last_cmd == "stop":
            return "stopped"

        if last_cmd == "pause":
            return "paused"

        try:
            next_timer = getattr(device_player, "_next_timer", None)
            current_index = getattr(device_player, "_current_index", -1)
            play_list = getattr(device_player, "_play_list", [])
            cur_music = getattr(device_player, "get_cur_music", lambda: "")()
            if callable(cur_music):
                cur_music = cur_music() or ""
            if next_timer is not None:
                return "switching"
            if (
                current_index >= 0
                and isinstance(play_list, list)
                and current_index < len(play_list)
            ):
                next_name = play_list[current_index] or ""
                if next_name and next_name != cur_music:
                    return "switching"
        except Exception:
            pass

        if last_cmd in {"play", "playlocal", "play_next", "play_prev", "playmusic"}:
            return "starting"

        return "idle"

    def _make_snapshot_key(
        self,
        device_id: str,
        transport_state: str,
        track_id: str,
        play_session_id: str,
        context_id: str,
        current_index: int | None,
        duration_ms: int,
    ) -> str:
        """Build a deterministic key that captures discrete externally-visible state.

        This key is used for revision deduplication: when the key hasn't changed,
        the revision stays the same. Only includes fields that represent discrete
        state changes (not natural time progression like position_ms).
        """
        return "|".join(
            str(x)
            for x in [
                device_id,
                transport_state,
                track_id,
                play_session_id,
                context_id,
                current_index,
                duration_ms,
            ]
        )

    def _compute_revision(self, device_id: str, snapshot_key: str) -> int:
        """Increment revision only when snapshot key changes."""
        if not hasattr(self, "_revision_state"):
            self._revision_state: dict[str, dict[str, Any]] = {}

        if device_id not in self._revision_state:
            self._revision_state[device_id] = {
                "revision": 0,
                "last_key": "",
            }

        state = self._revision_state[device_id]
        if state["last_key"] != snapshot_key:
            state["revision"] += 1
            state["last_key"] = snapshot_key

        return state["revision"]
