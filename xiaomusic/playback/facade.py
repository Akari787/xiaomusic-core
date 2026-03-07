"""Thin facade adapting API models to PlaybackCoordinator."""

from __future__ import annotations

from typing import Any, Callable
from uuid import uuid4

from xiaomusic.adapters.mina import MinaTransport
from xiaomusic.adapters.miio import MiioTransport
from xiaomusic.adapters.sources import register_default_source_plugins
from xiaomusic.core.coordinator import PlaybackCoordinator
from xiaomusic.core.delivery import DeliveryAdapter
from xiaomusic.core.device import DeviceRegistry
from xiaomusic.core.errors import InvalidRequestError
from xiaomusic.core.models import MediaRequest
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
        register_default_source_plugins(source_registry, self.xiaomusic)
        device_registry = DeviceRegistry(self.xiaomusic)
        delivery_adapter = DeliveryAdapter()
        router = TransportRouter(policy=TransportPolicy())
        router.register_transport(MinaTransport(self.xiaomusic))
        router.register_transport(MiioTransport(self.xiaomusic))
        self._core_coordinator = PlaybackCoordinator(
            source_registry=source_registry,
            device_registry=device_registry,
            delivery_adapter=delivery_adapter,
            transport_router=router,
        )
        return self._core_coordinator

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

    @staticmethod
    def _build_context(query: str, hint: str | None, options: dict[str, Any], *, include_prefer_proxy: bool) -> dict[str, Any]:
        context: dict[str, Any] = {
            "resolve_timeout_seconds": float(options.get("resolve_timeout_seconds", 8)),
        }
        if include_prefer_proxy:
            context["prefer_proxy"] = bool(options.get("prefer_proxy", False))

        if hint == "jellyfin":
            payload = options.get("source_payload")
            if not isinstance(payload, dict):
                payload = {
                    "source": "jellyfin",
                    "url": query,
                    "id": str(options.get("media_id") or options.get("id") or ""),
                    "title": str(options.get("title") or ""),
                }
            context["source_payload"] = payload
            if payload.get("title"):
                context["title"] = str(payload.get("title"))

        if hint == "local_library" and options.get("title"):
            context["title"] = str(options.get("title"))

        return context

    async def play(
        self,
        *,
        device_id: str,
        query: str,
        source_hint: str = "auto",
        options: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        did = self._validate_device_id(device_id)
        q = self._validate_query(query)
        opts = options or {}
        normalized_hint = self._normalize_hint(source_hint)
        req = MediaRequest(
            request_id=str(request_id or uuid4().hex[:16]),
            source_hint=normalized_hint,
            query=q,
            device_id=did,
            context=self._build_context(q, normalized_hint, opts, include_prefer_proxy=True),
        )
        result = await self._core().play(req, device_id=did)
        prepared = result["prepared_stream"]
        resolved = result["resolved_media"]
        dispatch = result["dispatch"]
        return {
            "status": "playing",
            "device_id": did,
            "source_plugin": prepared.source,
            "transport": dispatch.transport,
            "request_id": req.request_id,
            "media": {
                "media_id": resolved.media_id,
                "title": resolved.title,
                "stream_url": prepared.final_url,
                "is_live": bool(resolved.is_live),
            },
            "extra": {"dispatch": dispatch.data},
        }

    async def resolve(
        self,
        *,
        query: str,
        source_hint: str = "auto",
        options: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        q = self._validate_query(query)
        opts = options or {}
        normalized_hint = self._normalize_hint(source_hint)
        req = MediaRequest(
            request_id=str(request_id or uuid4().hex[:16]),
            source_hint=normalized_hint,
            query=q,
            device_id=None,
            context=self._build_context(q, normalized_hint, opts, include_prefer_proxy=False),
        )
        result = await self._core().resolve(req)
        resolved = result["resolved_media"]
        return {
            "resolved": True,
            "source_plugin": result["source_plugin"],
            "request_id": req.request_id,
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
            "device_id": did,
            "transport": result["transport"],
            "request_id": str(request_id or uuid4().hex[:16]),
            "extra": {"dispatch": result["dispatch"].data},
        }

    async def pause(self, device_id: str, request_id: str | None = None) -> dict[str, Any]:
        did = self._validate_device_id(device_id)
        result = await self._core().pause(did)
        return {
            "status": "paused",
            "device_id": did,
            "transport": result["transport"],
            "request_id": str(request_id or uuid4().hex[:16]),
            "extra": {"dispatch": result["dispatch"].data},
        }

    async def resume(self, device_id: str, request_id: str | None = None) -> dict[str, Any]:
        did = self._validate_device_id(device_id)
        result = await self._core().resume(did)
        return {
            "status": "resumed",
            "device_id": did,
            "transport": result["transport"],
            "request_id": str(request_id or uuid4().hex[:16]),
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
            "device_id": did,
            "transport": result["transport"],
            "request_id": str(request_id or uuid4().hex[:16]),
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
            "device_id": did,
            "transport": result["transport"],
            "request_id": str(request_id or uuid4().hex[:16]),
            "extra": {"volume": level, "dispatch": result["dispatch"].data},
        }

    async def probe(self, device_id: str, request_id: str | None = None) -> dict[str, Any]:
        did = self._validate_device_id(device_id)
        result = await self._core().probe(did)
        reachability = result.get("reachability")
        return {
            "status": "ok",
            "device_id": did,
            "transport": result["transport"],
            "request_id": str(request_id or uuid4().hex[:16]),
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

    async def status(self, target: dict[str, Any]) -> dict[str, Any]:
        speaker_id = str(target.get("speaker_id") or "").strip()
        if not speaker_id:
            raise InvalidRequestError("speaker_id is required")
        raw = await self.xiaomusic.get_player_status(did=speaker_id)
        return {
            "sid": "",
            "speaker_id": speaker_id,
            "state": str(raw.get("status", "unknown")),
            "title": None,
            "stream_url": "",
            "error_code": None,
            "ok": True,
            "raw": raw,
        }

    async def stop_legacy(self, target: dict[str, Any]) -> dict[str, Any]:
        speaker_id = str(target.get("speaker_id") or "").strip()
        out = await self.stop(speaker_id)
        return {
            "sid": "",
            "speaker_id": speaker_id,
            "state": out.get("status"),
            "title": None,
            "stream_url": "",
            "error_code": None,
            "ok": True,
            "raw": {
                "ret": "OK",
                "transport": out.get("transport"),
                "dispatch": out.get("extra", {}).get("dispatch", {}),
            },
        }

    async def pause_legacy(self, speaker_id: str) -> dict[str, Any]:
        out = await self.pause(speaker_id)
        return {
            "speaker_id": speaker_id,
            "ok": True,
            "error_code": None,
            "raw": {
                "ret": "OK",
                "transport": out.get("transport"),
                "dispatch": out.get("extra", {}).get("dispatch", {}),
            },
        }

    async def tts_legacy(self, speaker_id: str, text: str) -> dict[str, Any]:
        out = await self.tts(speaker_id, text)
        return {
            "speaker_id": speaker_id,
            "ok": True,
            "error_code": None,
            "raw": {
                "ret": "OK",
                "transport": out.get("transport"),
                "dispatch": out.get("extra", {}).get("dispatch", {}),
            },
        }

    async def set_volume_legacy(self, speaker_id: str, volume: int) -> dict[str, Any]:
        out = await self.set_volume(speaker_id, volume)
        return {
            "speaker_id": speaker_id,
            "ok": True,
            "error_code": None,
            "raw": {
                "ret": "OK",
                "volume": int(volume),
                "transport": out.get("transport"),
                "dispatch": out.get("extra", {}).get("dispatch", {}),
            },
        }
