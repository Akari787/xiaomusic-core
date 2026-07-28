"""Thin facade adapting API models to PlaybackCoordinator."""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from xiaomusic.adapters.miio import MiioTransport
from xiaomusic.adapters.mina import MinaTransport
from xiaomusic.adapters.sources import register_default_source_plugins
from xiaomusic.constants.api_fields import DEVICE_ID, REQUEST_ID
from xiaomusic.core.coordinator import PlaybackCoordinator
from xiaomusic.core.delivery import DeliveryAdapter
from xiaomusic.core.device import DeviceRegistry
from xiaomusic.core.errors import (
    DeviceNotFoundError,
    InvalidRequestError,
)
from xiaomusic.core.models import MediaRequest, PlayOptions
from xiaomusic.core.source.source_protocols import LinkPreparer
from xiaomusic.core.transport import TransportPolicy, TransportRouter
from xiaomusic.managers.source_plugin_manager import SourcePluginManager
from xiaomusic.playback.runtime_state import (
    PlaybackPhase,
    PlaybackRuntimeState,
    TrackReference,
)
from xiaomusic.security.redaction import redact_text

LOG = logging.getLogger("xiaomusic.playback.facade")

if TYPE_CHECKING:
    pass


def build_track_id(
    playlist_name: str,
    index: int | None,
    title: str,
    identity_hint: str | None = None,
) -> str:
    """Build stable track identity for playlist items.

    Prefer a stable media identity (media_id / file path / source URL) so track.id
    stays consistent even when backend runtime playlist order changes (for example,
    random playback mode). Fall back to title, then index only when no better
    identity is available.
    """
    stable_identity = str(identity_hint or "").strip() or str(title or "").strip()
    if not stable_identity:
        stable_identity = str(index if index is not None else -1)
    track_key = f"{playlist_name or 'default'}:{stable_identity}"
    return hashlib.md5(track_key.encode()).hexdigest()[:16]


class PlaybackFacade:
    """Keep API layer thin while exposing stable runtime methods."""

    def __init__(
        self,
        xiaomusic,
        link_preparer: LinkPreparer | None = None,
        source_plugin_manager: SourcePluginManager | None = None,
    ) -> None:
        self.xiaomusic = xiaomusic
        # link_preparer can be a direct LinkPreparer instance or a
        # zero-argument callable that returns one (lazy factory).
        self._link_preparer = link_preparer
        self._core_coordinator: PlaybackCoordinator | None = None
        self._core_registry_version: int | None = None
        self._device_track_source_hints: dict[str, dict[str, str]] = {}
        self._source_plugin_manager = source_plugin_manager or getattr(
            self.xiaomusic, "source_plugin_manager", None
        )

    def _resolve_link_preparer(self) -> LinkPreparer | None:
        """Resolve link_preparer lazily (supports factory callables)."""
        lp = self._link_preparer
        if callable(lp) and not isinstance(lp, type):
            return lp()
        return lp

    def _get_source_plugin_manager(self) -> SourcePluginManager:
        if self._source_plugin_manager is not None:
            return self._source_plugin_manager
        config = getattr(self.xiaomusic, "config", None)
        conf_path = getattr(config, "conf_path", ".") if config is not None else "."
        manager = SourcePluginManager(
            register_defaults=lambda registry: register_default_source_plugins(
                registry,
                self.xiaomusic,
                link_preparer=self._resolve_link_preparer(),
            ),
            plugins_dir=str(Path(conf_path) / "source_plugins"),
        )
        self.xiaomusic.source_plugin_manager = manager
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
            def proxy_builder(origin_url, title):
                return str(
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
    def _sanitize_public_value(obj: Any) -> Any:
        """Recursively redact sensitive values in public API responses.

        Processes dict, list, and dataclass/plain-object structures.
        Strings are passed through redact_text for api_key/token/password removal.
        Internal dispatch values are NOT affected — this is only for public output.
        """
        if isinstance(obj, str):
            return redact_text(obj)
        if isinstance(obj, dict):
            return {k: PlaybackFacade._sanitize_public_value(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [PlaybackFacade._sanitize_public_value(v) for v in obj]
        if is_dataclass(obj) and not isinstance(obj, type):
            raw = asdict(obj)
            return PlaybackFacade._sanitize_public_value(raw)
        if hasattr(obj, "__dict__") and not isinstance(obj, type):
            raw = {k: getattr(obj, k) for k in obj.__dict__ if not k.startswith("_")}
            return PlaybackFacade._sanitize_public_value(raw)
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

    def _get_config_music_lists(self) -> list[dict[str, Any]]:
        config = getattr(self.xiaomusic, "config", None)
        raw_music_list_json = getattr(config, "music_list_json", "") if config else ""
        if not raw_music_list_json:
            return []

        try:
            import json

            music_lists = json.loads(raw_music_list_json)
        except Exception:
            return []

        if not isinstance(music_lists, list):
            return []
        return [item for item in music_lists if isinstance(item, dict)]

    def _resolve_context_source(self, context_id: str) -> str | None:
        playlist_name = str(context_id or "").strip()
        if not playlist_name:
            return None

        for item in self._get_config_music_lists():
            if str(item.get("name") or "").strip() != playlist_name:
                continue
            return self._normalize_track_source_value(item.get("source"))
        return None

    def _resolve_track_identity_hint(
        self,
        *,
        context_id: str,
        track_title: str,
        detail: dict[str, Any] | None = None,
    ) -> str:
        playlist_name = str(context_id or "").strip()
        title = str(track_title or "").strip()
        detail = detail if isinstance(detail, dict) else {}

        for key in ("media_id", "audio_id", "audioID", "id"):
            value = str(detail.get(key) or "").strip()
            if value:
                return value

        for key in ("origin_url", "stream_url", "url", "audio_url", "play_url", "path"):
            value = str(detail.get(key) or "").strip()
            if value:
                return value

        music_library = getattr(self.xiaomusic, "music_library", None)
        if playlist_name and title and music_library is not None:
            resolver = getattr(music_library, "resolve_playlist_item_identity", None)
            if callable(resolver):
                try:
                    resolved_entity_id = str(
                        resolver(
                            playlist_name,
                            item_name=title,
                            item_id=str(detail.get("track_id") or detail.get("id") or "").strip(),
                        )
                        or ""
                    ).strip()
                    if resolved_entity_id:
                        return resolved_entity_id
                except Exception:
                    pass

        if playlist_name and title:
            for playlist in self._get_config_music_lists():
                if str(playlist.get("name") or "").strip() != playlist_name:
                    continue
                musics = playlist.get("musics")
                if not isinstance(musics, list):
                    continue
                for item in musics:
                    if not isinstance(item, dict):
                        continue
                    item_title = str(item.get("name") or item.get("title") or "").strip()
                    if item_title != title:
                        continue
                    for key in ("entity_id", "id", "media_id", "audio_id", "url", "path"):
                        value = str(item.get(key) or "").strip()
                        if value:
                            return value
                    break
                break

        if music_library is not None and title:
            resolver = getattr(music_library, "resolve_entity_id_by_name", None)
            if callable(resolver):
                try:
                    entity_id = str(resolver(title) or "").strip()
                    if entity_id:
                        return entity_id
                except Exception:
                    pass

        all_music = getattr(music_library, "all_music", None)
        if isinstance(all_music, dict) and title:
            value = all_music.get(title)
            if value is not None:
                identity = str(value).strip()
                if identity:
                    return identity

        return title

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

    def _playlist_context(self, options: PlayOptions, query: str) -> tuple[str, dict, dict, bool] | None:
        """Extract playlist context and resolve a random member for shuffle.

        Returns (playlist_name, member_info, entity_record, should_shuffle) or None.
        member_info: membership data (item_id, entity_id, display_name, etc.)
        entity_record: full entity from music_library.music_entities (url, duration, etc.)

        When options.shuffle=True and query is a playlist name, a member is randomly
        selected and its full entity record is looked up for source-aware routing.
        """
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
            or ""
        ).strip()

        should_shuffle = bool(options.shuffle)
        if should_shuffle and not music_name and not playlist_name and query:
            playlist_name = query
            context_type = "playlist"

        if context_type != "playlist" or not playlist_name:
            return None

        # Resolve a random member from the playlist for source-aware routing.
        music_library = getattr(self.xiaomusic, "music_library", None)
        member_info: dict = {}
        entity_record: dict = {}
        if should_shuffle:
            if music_library is None:
                raise InvalidRequestError(
                    f"playlist '{playlist_name}': music library not available"
                )
            getter = getattr(music_library, "get_playlist_items", None)
            if callable(getter):
                try:
                    members = list(getter(playlist_name) or [])
                except Exception:
                    members = []
                if not members:
                    raise InvalidRequestError(
                        f"playlist '{playlist_name}' is empty or not found"
                    )
                import random as _random
                member_info = dict(_random.choice(members))
                entity_id = str(member_info.get("entity_id") or "").strip()
                if entity_id:
                    music_entities = getattr(music_library, "music_entities", None)
                    if isinstance(music_entities, dict):
                        entity_record = dict(music_entities.get(entity_id) or {})
                music_name = str(
                    member_info.get("display_name")
                    or member_info.get("legacy_name")
                    or member_info.get("title")
                    or member_info.get("name")
                    or entity_record.get("canonical_name")
                    or entity_record.get("display_name")
                    or music_name
                    or ""
                ).strip()
            else:
                raise InvalidRequestError(
                    f"playlist '{playlist_name}' not found in music library"
                )

        if not music_name:
            music_name = query or ""

        return playlist_name, member_info, entity_record, should_shuffle

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
        # Only call _playlist_context when hint is auto or local_library.
        playlist_context = (
            self._playlist_context(opts, q)
            if normalized_hint in (None, "local_library")
            else None
        )
        if playlist_context is not None:
            playlist_name, member_info, entity_record, should_shuffle = playlist_context

            # Infer source from entity_record.
            entity_id = str(entity_record.get("entity_id") or member_info.get("entity_id") or "").strip()
            entity_source = str(entity_record.get("source") or "").strip().lower()

            music_name = str(
                entity_record.get("canonical_name")
                or entity_record.get("display_name")
                or member_info.get("display_name")
                or member_info.get("legacy_name")
                or member_info.get("title")
                or member_info.get("name")
                or opts.title
                or ""
            ).strip()

            # Route to correct source plugin based on entity_id prefix.
            if entity_id.startswith("jellyfin:") or entity_source == "jellyfin":
                normalized_hint = "jellyfin"
                q = music_name
            elif entity_id.startswith("local:") or entity_source == "local":
                normalized_hint = "local_library"
                q = music_name
            elif not entity_id and not entity_source:
                # No source info: keep original hint, use music_name.
                q = music_name
            else:
                q = music_name

            # Build source_payload from entity_record (includes url, duration, etc.)
            merged_source_payload = (
                dict(opts.source_payload) if isinstance(opts.source_payload, dict) else {}
            )
            if entity_record:
                merged_source_payload["entity_id"] = entity_id
                merged_source_payload["source"] = entity_source or "unknown"
                if entity_record.get("source_item_id"):
                    merged_source_payload["id"] = str(entity_record["source_item_id"])
                    merged_source_payload["media_id"] = str(entity_record["source_item_id"])
                if entity_record.get("canonical_name") or entity_record.get("display_name"):
                    merged_source_payload["title"] = music_name
                if entity_record.get("origin_url"):
                    merged_source_payload["url"] = str(entity_record["origin_url"])
                if entity_record.get("path"):
                    merged_source_payload["path"] = str(entity_record["path"])
                if entity_record.get("duration"):
                    try:
                        merged_source_payload["duration"] = float(entity_record["duration"])
                        merged_source_payload["duration_seconds"] = float(entity_record["duration"])
                    except (TypeError, ValueError):
                        pass
            else:
                merged_source_payload["entity_id"] = str(member_info.get("entity_id") or "")
            merged_source_payload.update(
                {
                    "context_type": "playlist",
                    "playlist_name": playlist_name,
                    "context_name": playlist_name,
                    "music_name": music_name,
                    "track_name": music_name,
                }
            )
            if member_info.get("item_id"):
                merged_source_payload.setdefault("item_id", str(member_info.get("item_id") or ""))
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
        return self._sanitize_public_value({
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
        })

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
        return self._sanitize_public_value({
            "status": "ok",
            DEVICE_ID: did,
            "transport": result["transport"],
            REQUEST_ID: str(request_id or uuid4().hex[:16]),
            "action": "previous",
            "extra": {"dispatch": result["dispatch"].data},
        })

    async def next(
        self, device_id: str, request_id: str | None = None
    ) -> dict[str, Any]:
        did = self._validate_device_id(device_id)
        result = await self._core().next(did)
        return self._sanitize_public_value({
            "status": "ok",
            DEVICE_ID: did,
            "transport": result["transport"],
            REQUEST_ID: str(request_id or uuid4().hex[:16]),
            "action": "next",
            "extra": {"dispatch": result["dispatch"].data},
        })

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

        # Deprecated direct-caller bridge. Real devices always use the pure
        # runtime snapshot; the narrow fallback below serves old test/legacy
        # objects that do not expose runtime state. Neither path mutates state
        # or owns tasks.
        device_player = getattr(
            getattr(self.xiaomusic, "device_manager", None), "devices", {}
        ).get(did)
        if device_player is not None and callable(
            getattr(device_player, "get_runtime_state", None)
        ):
            return await self.build_player_state_snapshot(did)

        is_playing = bool(getattr(self.xiaomusic, "isplaying", lambda _did: False)(did))
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
        is_playing = is_playing or int(raw_status.get("status", 0) or 0) == 1
        cur_music = ""
        if is_playing:
            detail = raw_status.get("play_song_detail")
            if isinstance(detail, dict):
                cur_music = str(
                    detail.get("audio_name")
                    or detail.get("title")
                    or detail.get("name")
                    or ""
                ).strip()
                if not cur_music:
                    cur_music = str(
                        getattr(self.xiaomusic, "playingmusic", lambda _did: "")(did)
                        or ""
                    )
                detail_pos = float(detail.get("position") or 0)
                detail_dur = float(detail.get("duration") or 0)
                if detail_pos > 0 and offset <= 0:
                    offset = detail_pos / 1000.0 if detail_pos > 10000 else detail_pos
                if detail_dur > 0 and duration <= 0:
                    duration = detail_dur / 1000.0 if detail_dur > 10000 else detail_dur
            else:
                cur_music = str(
                    getattr(self.xiaomusic, "playingmusic", lambda _did: "")(did)
                    or ""
                )
        safe_duration = max(0, int(duration))
        safe_offset = min(max(0, int(offset)), safe_duration) if safe_duration else max(0, int(offset))
        return {
            "device_id": did,
            "is_playing": bool(is_playing),
            "cur_music": cur_music,
            "offset": safe_offset,
            "duration": safe_duration,
            REQUEST_ID: str(request_id or uuid4().hex[:16]),
        }

    # ── runtime phase → transport_state mapping ───────────────────────────

    _PHASE_TO_TRANSPORT: dict[PlaybackPhase, str] = {
        PlaybackPhase.IDLE: "idle",
        PlaybackPhase.RESOLVING: "starting",
        PlaybackPhase.DISPATCHING: "starting",
        PlaybackPhase.CONFIRMING: "starting",
        PlaybackPhase.SWITCHING: "switching",
        PlaybackPhase.PLAYING: "playing",
        PlaybackPhase.PAUSED: "paused",
        PlaybackPhase.STOPPING: "stopping",
        PlaybackPhase.STOPPED: "stopped",
        PlaybackPhase.FAILED: "error",
    }

    # phases where desired_track is preferred (pre-confirmation)
    _DESIRED_TRACK_PHASES: frozenset[PlaybackPhase] = frozenset({
        PlaybackPhase.RESOLVING,
        PlaybackPhase.DISPATCHING,
        PlaybackPhase.CONFIRMING,
        PlaybackPhase.SWITCHING,
    })

    # phases where confirmed_track is preferred (post-confirmation, with
    # fallback to desired)
    _CONFIRMED_TRACK_PHASES: frozenset[PlaybackPhase] = frozenset({
        PlaybackPhase.PLAYING,
        PlaybackPhase.PAUSED,
        PlaybackPhase.STOPPING,
        PlaybackPhase.STOPPED,
        PlaybackPhase.FAILED,
    })

    @staticmethod
    def _project_runtime_snapshot(
        device_player: Any,
    ) -> dict[str, Any] | None:
        """Pure read-only projection of runtime state for snapshot use.

        Returns a dict with keys 'authoritative', 'transport_state',
        'track_ref', and 'is_playing' when the device_player exposes a valid
        PlaybackRuntimeState via ``get_runtime_state()``.

        Returns None when runtime is unavailable (no device_player, no
        get_runtime_state, wrong type, exception) so the caller can fall
        back to legacy logic.
        """
        if device_player is None:
            return None
        getter = getattr(device_player, "get_runtime_state", None)
        if not callable(getter):
            return None
        try:
            state = getter()
        except Exception:
            return None
        if not isinstance(state, PlaybackRuntimeState):
            return None

        phase: PlaybackPhase = state.phase
        transport_state = PlaybackFacade._PHASE_TO_TRANSPORT.get(
            phase, "idle"
        )

        # track selection: desired vs confirmed based on phase
        track_ref: TrackReference | None = None
        if phase in PlaybackFacade._DESIRED_TRACK_PHASES:
            track_ref = state.desired_track
        elif phase in PlaybackFacade._CONFIRMED_TRACK_PHASES:
            track_ref = state.confirmed_track or state.desired_track
        # IDLE → no track (track_ref stays None)

        track_dict: dict[str, Any] | None = None
        if track_ref is not None:
            track_dict = {
                "entity_id": track_ref.entity_id or "",
                "playlist_item_id": track_ref.playlist_item_id or "",
                "display_name": track_ref.display_name or "",
                "source": track_ref.source or "",
            }

        is_playing = phase == PlaybackPhase.PLAYING

        return {
            "authoritative": True,
            "transport_state": transport_state,
            "track_ref": track_dict,
            "is_playing": is_playing,
            "phase": phase,
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

        # ── try runtime projection ──────────────────────────────────────
        runtime_proj = self._project_runtime_snapshot(device_player)
        runtime_authoritative = bool(
            runtime_proj is not None and runtime_proj.get("authoritative")
        )

        # ── position / duration / volume: always from local compat ──────
        raw_offset, raw_duration = getattr(
            self.xiaomusic, "get_offset_duration", lambda _did: (0, 0)
        )(did)
        offset_s = float(raw_offset or 0)
        duration_s = float(raw_duration or 0)

        current_volume = 0
        if device_player is not None:
            try:
                current_volume = int(
                    getattr(device_player, "_last_volume", 0) or 0
                )
            except Exception:
                current_volume = 0
        current_volume = max(0, min(100, int(current_volume or 0)))

        # ── play_session_id: always from local compat ───────────────────
        play_session_id = ""
        if device_player:
            try:
                sid = getattr(device_player, "_play_session_id", 0)
                play_session_id = f"sess_{sid}"
            except (ValueError, AttributeError):
                play_session_id = ""

        # ── cur_playlist (context id/name): always from local compat ────
        cur_playlist = ""
        try:
            cur_playlist = str(
                getattr(self.xiaomusic, "get_cur_play_list", lambda _did: "")(did)
                or ""
            )
        except Exception:
            cur_playlist = ""

        # ── current_index: always from local compat ─────────────────────
        current_index: int | None = None
        if device_player:
            try:
                real_index = getattr(device_player, "_current_index", -1)
                if real_index >= 0:
                    current_index = real_index
            except (ValueError, AttributeError):
                pass

        # ── transport_state and track identity ──────────────────────────
        track_title = ""
        track_artist: str | None = None
        track_album: str | None = None
        raw_track_source: str | None = None
        track_source: str | None = None
        track_identity_hint = ""
        track_id = ""
        runtime_entity_id = ""
        runtime_playlist_item_id = ""
        detail: dict[str, Any] | None = None

        if runtime_authoritative:
            # ── authoritative path ──────────────────────────────────
            assert runtime_proj is not None
            transport_state = str(
                runtime_proj.get("transport_state") or "idle"
            )
            # is_playing strictly from phase==PLAYING; legacy isplaying
            # must never override pause / stop / error.
            is_playing = bool(runtime_proj.get("is_playing") or False)

            rt_track = runtime_proj.get("track_ref")
            if isinstance(rt_track, dict):
                runtime_entity_id = str(
                    rt_track.get("entity_id") or ""
                ).strip()
                runtime_playlist_item_id = str(
                    rt_track.get("playlist_item_id") or ""
                ).strip()
                track_title = str(
                    rt_track.get("display_name") or ""
                ).strip()
                raw_track_source = (
                    str(rt_track.get("source") or "").strip() or None
                )

            # Always route through _resolve_track_source for
            # normalization (e.g. 'external' → resolved contextual
            # source). The runtime raw source is passed as raw_source
            # so the resolver can use it as a hint but still apply
            # remembered / context fallback logic.
            track_source = self._resolve_track_source(
                device_id=did,
                track_title=track_title,
                context_id=cur_playlist,
                raw_source=raw_track_source,
                play_session_id=play_session_id,
            )
            # Guard: only accept normalized sources; raw runtime
            # strings like 'external' must never leak to API.
            if (
                track_source is not None
                and PlaybackFacade._normalize_track_source_value(
                    track_source
                )
                is None
            ):
                track_source = None

            # identity hint from runtime entity_id, fallback to resolver
            track_identity_hint = runtime_entity_id or self._resolve_track_identity_hint(
                context_id=cur_playlist,
                track_title=track_title,
                detail=None,
            )

            # track_id: prefer runtime playlist_item_id
            music_library = getattr(self.xiaomusic, "music_library", None)
            resolved_playlist_member = None
            if (
                music_library is not None
                and (runtime_playlist_item_id or cur_playlist)
            ):
                resolver = getattr(
                    music_library, "resolve_playlist_item_record", None
                )
                if callable(resolver):
                    try:
                        resolved_playlist_member = resolver(
                            cur_playlist,
                            item_name=track_title,
                            item_id=runtime_playlist_item_id,
                        )
                    except Exception:
                        resolved_playlist_member = None

            if runtime_playlist_item_id:
                track_id = runtime_playlist_item_id
            elif (
                resolved_playlist_member
                and str(
                    resolved_playlist_member.get("item_id") or ""
                ).strip()
            ):
                track_id = str(
                    resolved_playlist_member.get("item_id") or ""
                ).strip()
            elif track_title or current_index is not None or cur_playlist:
                track_id = build_track_id(
                    cur_playlist,
                    current_index,
                    track_title,
                    identity_hint=track_identity_hint,
                )

        else:
            # ── legacy fallback (unchanged behaviour) ───────────────
            is_playing = bool(
                getattr(self.xiaomusic, "isplaying", lambda _did: False)(did)
            )

            raw_status: dict[str, Any] = {}
            if device_player is not None:
                raw_status["status"] = 1 if is_playing else 0
            if int(raw_status.get("status", 0) or 0) == 1:
                is_playing = True

            transport_state = self._derive_transport_state(
                device_player, is_playing, raw_status
            )

            # legacy get_current_track_reference
            runtime_track_ref: dict[str, Any] = {}
            if device_player is not None:
                getter = getattr(
                    device_player, "get_current_track_reference", None
                )
                if callable(getter):
                    try:
                        runtime_track_ref = getter() or {}
                    except Exception:
                        runtime_track_ref = {}

            runtime_display_name = str(
                runtime_track_ref.get("display_name") or ""
            ).strip()
            runtime_entity_id = str(
                runtime_track_ref.get("entity_id") or ""
            ).strip()
            runtime_playlist_item_id = str(
                runtime_track_ref.get("playlist_item_id") or ""
            ).strip()

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
                                str(runtime_display_name)
                                or str(
                                    getattr(
                                        self.xiaomusic,
                                        "playingmusic",
                                        lambda _did: "",
                                    )(did)
                                    or ""
                                )
                            ).strip('"').strip()

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
                                detail_pos / 1000.0
                                if detail_pos > 10000
                                else detail_pos
                            )
                        if detail_dur > 0 and duration_s <= 0:
                            duration_s = (
                                detail_dur / 1000.0
                                if detail_dur > 10000
                                else detail_dur
                            )
                    else:
                        track_title = (
                            str(runtime_display_name)
                            or str(
                                getattr(
                                    self.xiaomusic,
                                    "playingmusic",
                                    lambda _did: "",
                                )(did)
                                or ""
                            )
                        ).strip('"').strip()

                if not track_title and runtime_display_name:
                    track_title = runtime_display_name

                if (
                    not track_title
                    and device_player
                    and transport_state != "idle"
                ):
                    try:
                        cur_idx = getattr(
                            device_player, "_current_index", -1
                        )
                        play_list = getattr(
                            device_player,
                            "_get_playlist_names",
                            lambda: [],
                        )()
                        if (
                            cur_idx >= 0
                            and isinstance(play_list, list)
                            and cur_idx < len(play_list)
                        ):
                            track_title = str(play_list[cur_idx] or "")
                    except Exception:
                        pass

            # legacy track_source lookup
            track_source = self._resolve_track_source(
                device_id=did,
                track_title=track_title,
                context_id=cur_playlist,
                raw_source=raw_track_source,
                play_session_id=play_session_id,
            )

            music_library = getattr(self.xiaomusic, "music_library", None)
            resolved_playlist_member = None
            if music_library is not None:
                resolver = getattr(
                    music_library, "resolve_playlist_item_record", None
                )
                if callable(resolver):
                    try:
                        resolved_playlist_member = resolver(
                            cur_playlist,
                            item_name=track_title,
                            item_id=str(
                                runtime_playlist_item_id
                                or (detail or {}).get("track_id")
                                or (detail or {}).get("id")
                                or ""
                            ).strip(),
                        )
                    except Exception:
                        resolved_playlist_member = None

            track_identity_hint = str(
                runtime_entity_id or ""
            ).strip() or self._resolve_track_identity_hint(
                context_id=cur_playlist,
                track_title=track_title,
                detail=detail,
            )

            if runtime_playlist_item_id:
                track_id = runtime_playlist_item_id
            elif (
                resolved_playlist_member
                and str(
                    resolved_playlist_member.get("item_id") or ""
                ).strip()
            ):
                track_id = str(
                    resolved_playlist_member.get("item_id") or ""
                ).strip()
            elif (
                track_title
                or current_index is not None
                or cur_playlist
            ):
                track_id = build_track_id(
                    cur_playlist,
                    current_index,
                    track_title,
                    identity_hint=track_identity_hint,
                )

            # current_index refinement (legacy path only)
            if device_player:
                try:
                    play_list = getattr(
                        device_player, "_get_playlist_names", lambda: []
                    )()
                except (ValueError, AttributeError):
                    play_list = []

                try:
                    real_index = getattr(
                        device_player, "_current_index", -1
                    )
                    if (
                        real_index >= 0
                        and play_list
                        and track_title
                    ):
                        if real_index < len(play_list):
                            list_title = str(
                                play_list[real_index] or ""
                            )
                            if list_title == track_title:
                                current_index = real_index
                            else:
                                current_index = None
                except (ValueError, AttributeError):
                    pass

                if current_index is None:
                    try:
                        finder = getattr(
                            device_player,
                            "_find_playlist_index",
                            None,
                        )
                        if callable(finder):
                            idx = finder(
                                item_id=runtime_playlist_item_id,
                                entity_id=track_identity_hint,
                                display_name=track_title,
                            )
                            if idx >= 0:
                                current_index = idx
                    except Exception:
                        pass

                if current_index is None and track_title and play_list:
                    try:
                        if track_title in play_list:
                            current_index = play_list.index(track_title)
                    except (ValueError, AttributeError):
                        pass

        # ── position_ms / duration_ms ───────────────────────────────────
        position_ms = max(0, int(offset_s * 1000))
        duration_ms = max(0, int(duration_s * 1000))
        if duration_ms > 0:
            position_ms = min(position_ms, duration_ms)

        # ── context object ──────────────────────────────────────────────
        context_obj: dict[str, Any] | None = None
        if cur_playlist or current_index is not None:
            context_obj = {
                "id": cur_playlist or "default",
                "name": cur_playlist or "播放列表",
                "current_index": current_index,
            }

        # ── track object ────────────────────────────────────────────────
        track_obj: dict[str, Any] | None = None
        if transport_state != "idle" and (track_title or track_id):
            track_obj = {
                "id": track_id,
                "entity_id": track_identity_hint or "",
                "title": track_title,
            }
            if track_artist is not None:
                track_obj["artist"] = track_artist
            if track_album is not None:
                track_obj["album"] = track_album
            if track_source is not None:
                track_obj["source"] = track_source

        # ── revision / snapshot ────────────────────────────────────────
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
            "volume": current_volume,
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

        # Legacy fallback is deliberately derived from read-only observations.
        # Command history is diagnostic only and cannot describe physical state.
        if is_playing:
            return "playing"

        # BUG-011: 非 playing 分支下的 switching 判定。
        # _next_timer 存在时通常表示正在等待自动切歌，属于正常的切换过渡状态。
        # 但如果本地 is_playing 为 False（设备已停止但定时器未被取消），
        # 说明是残留定时器，应跳过 switching 以免 UI 长期卡住。
        try:
            next_timer = getattr(device_player, "_next_timer", None)
            current_index = getattr(device_player, "_current_index", -1)
            play_list = getattr(device_player, "_get_playlist_names", lambda: [])()
            cur_music = getattr(device_player, "get_cur_music", lambda: "")()
            if callable(cur_music):
                cur_music = cur_music() or ""
            player_is_playing = bool(
                getattr(device_player, "is_playing", False)
            )
            if next_timer is not None:
                if not player_is_playing:
                    # BUG-011: timer exists but player stopped — stale timer.
                    # No side effects: pure projection must not cancel/clear timer.
                    pass
                else:
                    return "switching"
            if (
                current_index >= 0
                and isinstance(play_list, list)
                and current_index < len(play_list)
            ):
                next_name = play_list[current_index] or ""
                # BUG-011 / BUG-013: current_index 与 cur_music 不一致时
                # 返回 switching，但仅当本地状态为 playing 时（活跃切换中）。
                # 若本地未处于 playing，则跳过 switching 并回退到命令态推导。
                if next_name and next_name != cur_music:
                    if player_is_playing:
                        return "switching"
        except Exception:
            pass

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
