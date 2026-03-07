"""Unified playback facade for play, stop and status."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from uuid import uuid4
from typing import Any, Callable

from xiaomusic.adapters.mina import MinaTransport
from xiaomusic.adapters.miio import MiioTransport
from xiaomusic.adapters.sources import HttpUrlSourcePlugin
from xiaomusic.core.coordinator import PlaybackCoordinator
from xiaomusic.core.delivery import DeliveryAdapter
from xiaomusic.core.device import DeviceRegistry
from xiaomusic.core.errors import ExpiredStreamError, SourceResolveError, TransportError
from xiaomusic.core.models import MediaRequest
from xiaomusic.core.source import SourceRegistry
from xiaomusic.core.transport import TransportPolicy, TransportRouter


class PlaybackFacade:
    """Facade entry for playback operations.

    This class keeps old endpoints compatible while converging all
    playback operations into a single internal play_url/stop/status API.
    """

    def __init__(self, xiaomusic, runtime_provider: Callable[[], Any] | None = None) -> None:
        self.xiaomusic = xiaomusic
        self._runtime_provider = runtime_provider
        self._core_coordinator: PlaybackCoordinator | None = None

    def _runtime(self):
        if self._runtime_provider is None:
            raise RuntimeError("network audio runtime provider is not configured")
        return self._runtime_provider()

    def _core(self) -> PlaybackCoordinator:
        if self._core_coordinator is not None:
            return self._core_coordinator

        source_registry = SourceRegistry()
        source_registry.register(HttpUrlSourcePlugin())
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
    def _to_state(ok: bool, raw: dict[str, Any], default_state: str = "playing") -> str:
        if not ok:
            return "error"
        session = raw.get("session")
        if isinstance(session, dict):
            state = session.get("state")
            if state:
                return str(state)
        state = raw.get("state")
        if state:
            return str(state)
        return default_state

    @staticmethod
    def _extract_sid(raw: dict[str, Any]) -> str:
        session = raw.get("session")
        if isinstance(session, dict):
            sid = session.get("sid")
            if sid:
                return str(sid)
        sid = raw.get("sid")
        if sid:
            return str(sid)
        return ""

    @staticmethod
    def _extract_stream_url(raw: dict[str, Any], fallback: str = "") -> str:
        session = raw.get("session")
        if isinstance(session, dict):
            stream_url = session.get("stream_url")
            if stream_url:
                return str(stream_url)
        stream_url = raw.get("stream_url")
        if stream_url:
            return str(stream_url)
        return fallback

    async def play_url(
        self,
        url: str,
        speaker_id: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        options = options or {}
        mode = options.get("mode", "direct")
        prefer_proxy = bool(options.get("prefer_proxy", False))
        no_cache = bool(options.get("no_cache", False))

        if mode == "network_audio_cast":
            raw = await self._runtime().play_and_cast(did=speaker_id, url=url, no_cache=no_cache)
            ok = bool(raw.get("ok", False))
        elif mode == "network_audio_link":
            raw = await self._runtime().play_link(
                did=speaker_id,
                url=url,
                prefer_proxy=prefer_proxy,
                no_cache=no_cache,
            )
            ok = bool(raw.get("ok", False))
        elif mode == "core_minimal":
            try:
                result = await self._core().play(
                    MediaRequest(
                        request_id=str(uuid4()),
                        source_hint="http_url",
                        query=url,
                        device_id=speaker_id,
                    ),
                    device_id=speaker_id,
                )
                prepared = result["prepared_stream"]
                dispatch = result["dispatch"]
                raw = {
                    "ok": True,
                    "mode": "core_minimal",
                    "stream_url": prepared.final_url,
                    "source": prepared.source,
                    "transport": dispatch.transport,
                    "dispatch": dispatch.data,
                }
                ok = True
            except (SourceResolveError, ExpiredStreamError, TransportError, KeyError, ValueError):
                raw = {
                    "ok": False,
                    "mode": "core_minimal",
                    "error_code": "E_XIAOMI_PLAY_FAILED",
                    "stream_url": url,
                }
                ok = False
        else:
            cast_ret = await self.xiaomusic.play_url(did=speaker_id, arg1=url)
            raw = {
                "ok": True,
                "mode": "direct",
                "cast_ret": cast_ret,
                "stream_url": url,
            }
            ok = True

        result = {
            "sid": self._extract_sid(raw),
            "speaker_id": speaker_id,
            "state": self._to_state(ok=ok, raw=raw),
            "title": raw.get("title"),
            "stream_url": self._extract_stream_url(raw, fallback=url),
            "error_code": raw.get("error_code"),
            "cache_hit": raw.get("cache_hit"),
            "resolve_ms": raw.get("resolve_ms"),
            "fail_stage": raw.get("fail_stage"),
            "ok": ok,
            "raw": raw,
        }
        return result

    async def stop(self, target: dict[str, Any]) -> dict[str, Any]:
        sid = str(target.get("sid") or "")
        speaker_id = str(target.get("speaker_id") or "")

        if sid:
            raw = self._runtime().stop_session(sid=sid)
            session = raw.get("session") or {}
            return {
                "sid": sid,
                "speaker_id": speaker_id,
                "state": str(session.get("state") or "stopped"),
                "title": None,
                "stream_url": str(session.get("stream_url") or ""),
                "error_code": None,
                "ok": True,
                "raw": raw,
            }

        if speaker_id:
            try:
                await self.xiaomusic.stop(did=speaker_id, arg1="notts")
            except Exception:
                return {
                    "sid": sid,
                    "speaker_id": speaker_id,
                    "state": "error",
                    "title": None,
                    "stream_url": "",
                    "error_code": "E_STREAM_NOT_FOUND",
                    "ok": False,
                    "raw": {"ret": "Did not exist"},
                }
            raw = {"ret": "OK"}
            return {
                "sid": "",
                "speaker_id": speaker_id,
                "state": "stopped",
                "title": None,
                "stream_url": "",
                "error_code": None,
                "ok": True,
                "raw": raw,
            }

        return {
            "sid": "",
            "speaker_id": "",
            "state": "error",
            "title": None,
            "stream_url": "",
            "error_code": "E_INVALID_TARGET",
            "ok": False,
            "raw": {"ret": "Invalid target"},
        }

    async def status(self, target: dict[str, Any]) -> dict[str, Any]:
        self._runtime().sweep_idle_sessions()
        sid = str(target.get("sid") or "")
        speaker_id = str(target.get("speaker_id") or "")

        if sid:
            session: Any = self._runtime().session_manager.get_session(sid)
            if session is None:
                return {
                    "sid": sid,
                    "speaker_id": speaker_id,
                    "state": "error",
                    "title": None,
                    "stream_url": "",
                    "error_code": "E_STREAM_NOT_FOUND",
                    "ok": False,
                    "raw": {"ret": "Not found"},
                }
            raw_session: Any = session
            if is_dataclass(session):
                raw_session = {f.name: getattr(session, f.name, None) for f in fields(session)}
            meta = raw_session.get("meta", {}) if isinstance(raw_session, dict) else {}
            return {
                "sid": sid,
                "speaker_id": speaker_id,
                "state": str(raw_session.get("state") if isinstance(raw_session, dict) else "unknown"),
                "title": str(meta.get("title") or "") or None,
                "stream_url": str(raw_session.get("stream_url") if isinstance(raw_session, dict) else ""),
                "error_code": None,
                "ok": True,
                "raw": {"session": raw_session},
            }

        if speaker_id:
            try:
                raw = await self.xiaomusic.get_player_status(did=speaker_id)
            except Exception:
                return {
                    "sid": "",
                    "speaker_id": speaker_id,
                    "state": "error",
                    "title": None,
                    "stream_url": "",
                    "error_code": "E_STREAM_NOT_FOUND",
                    "ok": False,
                    "raw": {"ret": "Did not exist"},
                }
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

        return {
            "sid": "",
            "speaker_id": "",
            "state": "error",
            "title": None,
            "stream_url": "",
            "error_code": "E_INVALID_TARGET",
            "ok": False,
            "raw": {"ret": "Invalid target"},
        }
