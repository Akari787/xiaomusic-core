"""Thin facade adapting API models to PlaybackCoordinator."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Callable
from uuid import uuid4

from xiaomusic.adapters.mina import MinaTransport
from xiaomusic.adapters.miio import MiioTransport
from xiaomusic.adapters.sources import register_default_source_plugins
from xiaomusic.constants.api_fields import DEVICE_ID, REQUEST_ID
from xiaomusic.core.coordinator import PlaybackCoordinator
from xiaomusic.core.delivery import DeliveryAdapter
from xiaomusic.core.device import DeviceRegistry
from xiaomusic.core.errors import DeviceNotFoundError, InvalidRequestError
from xiaomusic.core.models import MediaRequest, PlayOptions
from xiaomusic.core.source import SourceRegistry
from xiaomusic.core.transport import TransportPolicy, TransportRouter


class PlaybackFacade:
    """Keep API layer thin while exposing stable runtime methods."""

    def __init__(self, xiaomusic, runtime_provider: Callable[[], Any] | None = None) -> None:
        self.xiaomusic = xiaomusic
        self._runtime_provider = runtime_provider
        self._core_coordinator: PlaybackCoordinator | None = None

    def _core(self) -> PlaybackCoordinator:
        if self._core_coordinator is not None:
            return self._core_coordinator

        source_registry = SourceRegistry()
        register_default_source_plugins(source_registry, self.xiaomusic, runtime_provider=self._runtime_provider)
        device_registry = DeviceRegistry(self.xiaomusic)
        proxy_builder: Callable[[str, str], str] | None = None
        raw_proxy_builder = getattr(getattr(self.xiaomusic, "music_library", None), "get_proxy_url", None)
        if callable(raw_proxy_builder):
            proxy_builder = lambda origin_url, title: str(raw_proxy_builder(origin_url, name=title))
        delivery_adapter = DeliveryAdapter(proxy_url_builder=proxy_builder)
        router = TransportRouter(policy=TransportPolicy())
        router.register_transport(MinaTransport(self.xiaomusic))
        router.register_transport(MiioTransport(self.xiaomusic))
        self._core_coordinator = PlaybackCoordinator(
            source_registry=source_registry,
            device_registry=device_registry,
            delivery_adapter=delivery_adapter,
            transport_router=router,
            playback_status_provider=self.xiaomusic.get_player_status,
        )
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
    def _validate_query(query: str) -> str:
        q = str(query or "").strip()
        if not q:
            raise InvalidRequestError("query is required")
        return q

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
        req = MediaRequest.from_payload(
            request_id=str(request_id or uuid4().hex[:16]),
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
        accepted = bool(getattr(outcome, "accepted", False)) if outcome is not None else False
        started = bool(getattr(outcome, "started", False)) if outcome is not None else False
        verify_result = "ok" if started else "failed"
        self._record_playback_capability_verify(
            result=verify_result,
            verify_method="playback_dispatch",
            playback_capability_level="actual_playback_path",
            transport=dispatch.transport,
            error_code="" if started else "dispatch_not_started",
            error_message=f"accepted={accepted} started={started}",
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
        recorder = getattr(auth_manager, "record_playback_capability_verify", None) if auth_manager else None
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

    async def stop(self, device_id: str, request_id: str | None = None) -> dict[str, Any]:
        did = self._validate_device_id(device_id)
        result = await self._core().stop(did)
        return {
            "status": "stopped",
            DEVICE_ID: did,
            "transport": result["transport"],
            REQUEST_ID: str(request_id or uuid4().hex[:16]),
            "extra": {"dispatch": result["dispatch"].data},
        }

    async def previous(self, device_id: str, request_id: str | None = None) -> dict[str, Any]:
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

    async def next(self, device_id: str, request_id: str | None = None) -> dict[str, Any]:
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

    async def pause(self, device_id: str, request_id: str | None = None) -> dict[str, Any]:
        did = self._validate_device_id(device_id)
        result = await self._core().pause(did)
        return {
            "status": "paused",
            DEVICE_ID: did,
            "transport": result["transport"],
            REQUEST_ID: str(request_id or uuid4().hex[:16]),
            "extra": {"dispatch": result["dispatch"].data},
        }

    async def resume(self, device_id: str, request_id: str | None = None) -> dict[str, Any]:
        did = self._validate_device_id(device_id)
        result = await self._core().resume(did)
        return {
            "status": "resumed",
            DEVICE_ID: did,
            "transport": result["transport"],
            REQUEST_ID: str(request_id or uuid4().hex[:16]),
            "extra": {"dispatch": result["dispatch"].data},
        }

    async def tts(self, device_id: str, text: str, request_id: str | None = None) -> dict[str, Any]:
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

    async def probe(self, device_id: str, request_id: str | None = None) -> dict[str, Any]:
        did = self._validate_device_id(device_id)
        result = await self._core().probe(did)
        reachability = result.get("reachability")
        return {
            "status": "ok",
            DEVICE_ID: did,
            "transport": result["transport"],
            REQUEST_ID: str(request_id or uuid4().hex[:16]),
            "reachable": bool(getattr(reachability, "local_reachable", False) or getattr(reachability, "cloud_reachable", False)),
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

    async def player_state(self, device_id: str, request_id: str | None = None) -> dict[str, Any]:
        did = self._validate_device_id(device_id)
        if not bool(getattr(self.xiaomusic, "did_exist", lambda _did: False)(did)):
            raise DeviceNotFoundError("device not found")

        is_playing = bool(getattr(self.xiaomusic, "isplaying", lambda _did: False)(did))
        cur_music = str(getattr(self.xiaomusic, "playingmusic", lambda _did: "")(did) or "")
        raw_offset, raw_duration = getattr(self.xiaomusic, "get_offset_duration", lambda _did: (0, 0))(did)
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

        detail = raw_status.get("play_song_detail")
        if isinstance(detail, dict):
            if not cur_music:
                cur_music = str(
                    detail.get("audio_name")
                    or detail.get("title")
                    or detail.get("name")
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

        safe_offset = max(0, int(offset))
        safe_duration = max(0, int(duration))
        if safe_duration > 0:
            safe_offset = min(safe_offset, safe_duration)

        return {
            "device_id": did,
            "is_playing": bool(is_playing),
            "cur_music": cur_music,
            "offset": safe_offset,
            "duration": safe_duration,
            REQUEST_ID: str(request_id or uuid4().hex[:16]),
        }
