"""Unified playback facade for play, stop and status."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
import logging
from urllib.parse import urlparse
from uuid import uuid4
from typing import Any, Callable

from xiaomusic.adapters.mina import MinaTransport
from xiaomusic.adapters.miio import MiioTransport
from xiaomusic.adapters.sources import (
    LegacyPayloadSourcePlugin,
    register_default_source_plugins,
)
from xiaomusic.core.coordinator import PlaybackCoordinator
from xiaomusic.core.delivery import DeliveryAdapter
from xiaomusic.core.device import DeviceRegistry
from xiaomusic.core.errors import (
    ExpiredStreamError,
    SourceResolveError,
    TransportError,
    UndeliverableStreamError,
)
from xiaomusic.core.models import MediaRequest
from xiaomusic.core.source import SourceRegistry
from xiaomusic.core.transport import TransportPolicy, TransportRouter


LOG = logging.getLogger("xiaomusic.playback.facade")


class PlaybackFacade:
    """Facade entry for playback operations.

    This class keeps old endpoints compatible while converging all
    playback operations into a single internal play_url/stop/status API.
    """

    def __init__(self, xiaomusic, runtime_provider: Callable[[], Any] | None = None) -> None:
        self.xiaomusic = xiaomusic
        self._runtime_provider = runtime_provider
        self._core_coordinator: PlaybackCoordinator | None = None
        self._legacy_adapter = LegacyPayloadSourcePlugin()

    def _runtime(self):
        if self._runtime_provider is None:
            raise RuntimeError("network audio runtime provider is not configured")
        return self._runtime_provider()

    def _should_use_network_audio(self, url: str) -> bool:
        strategy = getattr(self.xiaomusic, "link_playback_strategy", None)
        if strategy is None:
            return False
        try:
            return bool(strategy.should_use_network_audio(url))
        except Exception:
            return False

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
    def _source_hint_from_payload(payload: dict[str, Any]) -> str:
        source = str(payload.get("source") or "").strip().lower()
        if source:
            canonical = {"jellyfin", "direct_url", "site_media", "local_library"}
            if source in canonical or source in SourceRegistry.LEGACY_HINT_MAP:
                return source
        url = str(payload.get("url") or "")
        if url.startswith(("http://", "https://")):
            return "direct_url"
        if str(payload.get("music_name") or payload.get("track_id") or payload.get("path") or payload.get("name") or ""):
            return "local_library"
        return "direct_url"

    @staticmethod
    def _is_http_url(url: str) -> bool:
        parsed = urlparse(url)
        return parsed.scheme in {"http", "https"}

    @staticmethod
    def _normalize_mode(mode: Any) -> str:
        value = str(mode or "core").strip().lower()
        if value == "core_minimal":
            return "core"
        return value

    def _source_hint_from_url(self, url: str) -> str:
        if self._should_use_network_audio(url):
            return "site_media"
        return "direct_url"

    @staticmethod
    def _as_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _core_error_result(speaker_id: str, error_code: str = "E_XIAOMI_PLAY_FAILED") -> dict[str, Any]:
        return {
            "sid": "",
            "speaker_id": speaker_id,
            "state": "error",
            "title": None,
            "stream_url": "",
            "error_code": error_code,
            "ok": False,
            "raw": {"ret": "Failed"},
        }

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
        mode = self._normalize_mode(options.get("mode", "core"))
        prefer_proxy = bool(options.get("prefer_proxy", False))
        no_cache = bool(options.get("no_cache", False))

        if mode == "network_audio_cast":
            # compatibility_layer: legacy runtime mode, planned removal after v2 API freeze.
            raw = await self._runtime().play_and_cast(did=speaker_id, url=url, no_cache=no_cache)
            ok = bool(raw.get("ok", False))
        elif mode == "network_audio_link":
            # compatibility_layer: legacy runtime mode, planned removal after v2 API freeze.
            raw = await self._runtime().play_link(
                did=speaker_id,
                url=url,
                prefer_proxy=prefer_proxy,
                no_cache=no_cache,
            )
            ok = bool(raw.get("ok", False))
        elif mode == "core":
            if not self._is_http_url(url):
                # compatibility_layer: support historical non-http inputs, planned removal in next major release.
                cast_ret = await self.xiaomusic.play_url(did=speaker_id, arg1=url)
                raw = {
                    "ok": True,
                    "mode": "legacy_direct_fallback",
                    "cast_ret": cast_ret,
                    "stream_url": url,
                }
                ok = True
            else:
                try:
                    result = await self._core().play(
                        MediaRequest(
                            request_id=str(uuid4()),
                            source_hint=self._source_hint_from_url(url),
                            query=url,
                            device_id=speaker_id,
                            context={
                                "resolve_timeout_seconds": 8,
                                "prefer_proxy": prefer_proxy,
                            },
                        ),
                        device_id=speaker_id,
                    )
                    prepared = result["prepared_stream"]
                    dispatch = result["dispatch"]
                    raw = {
                        "ok": True,
                        "mode": "core",
                        "stream_url": prepared.final_url,
                        "source": prepared.source,
                        "transport": dispatch.transport,
                        "dispatch": dispatch.data,
                    }
                    ok = True
                except (
                    SourceResolveError,
                    ExpiredStreamError,
                    UndeliverableStreamError,
                    TransportError,
                    KeyError,
                    ValueError,
                ):
                    # Compatibility fallback only for existing network_audio runtime chain.
                    if self._runtime_provider is not None and self._should_use_network_audio(url):
                        raw = await self._runtime().play_link(
                            did=speaker_id,
                            url=url,
                            prefer_proxy=prefer_proxy,
                            no_cache=no_cache,
                        )
                        ok = bool(raw.get("ok", False))
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
                    raw = {
                        "ok": False,
                        "mode": "core",
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

    async def play(
        self,
        *,
        device_id: str,
        query: str,
        source_hint: str = "auto",
        options: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        options = options or {}
        normalized_hint = str(source_hint or "auto").strip().lower()
        hint = None if normalized_hint in {"", "auto"} else normalized_hint
        media_request = MediaRequest(
            request_id=str(request_id or uuid4()),
            source_hint=hint,
            query=str(query),
            device_id=device_id,
            context={
                "resolve_timeout_seconds": float(options.get("resolve_timeout_seconds", 8)),
                "prefer_proxy": bool(options.get("prefer_proxy", False)),
            },
        )
        try:
            result = await self._core().play(media_request, device_id=device_id)
        except (
            SourceResolveError,
            ExpiredStreamError,
            UndeliverableStreamError,
            TransportError,
            KeyError,
            ValueError,
        ):
            list_name = str(options.get("list_name") or "").strip()
            if list_name:
                # compatibility_layer: keep playlist semantic playback for mixed web/local items.
                # removal_condition: remove after playlist source plugin fully supports mixed catalog entries.
                await self.xiaomusic.do_play_music_list(
                    did=device_id,
                    list_name=list_name,
                    music_name=str(query or ""),
                )
                return {
                    "status": "playing",
                    "device_id": device_id,
                    "source_plugin": "legacy_playlist",
                    "transport": "mina",
                    "request_id": media_request.request_id,
                    "media": {
                        "title": str(query or ""),
                        "stream_url": "",
                        "is_live": False,
                    },
                    "extra": {
                        "fallback": "playlist_legacy",
                        "list_name": list_name,
                    },
                }
            raise
        prepared = result["prepared_stream"]
        dispatch = result["dispatch"]
        resolved = result["resolved_media"]
        return {
            "status": "playing",
            "device_id": device_id,
            "source_plugin": prepared.source,
            "transport": dispatch.transport,
            "request_id": media_request.request_id,
            "media": {
                "title": resolved.title,
                "stream_url": prepared.final_url,
                "is_live": bool(resolved.is_live),
            },
            "extra": {
                "dispatch": dispatch.data,
            },
        }

    async def resolve(
        self,
        *,
        query: str,
        source_hint: str = "auto",
        options: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        options = options or {}
        normalized_hint = str(source_hint or "auto").strip().lower()
        hint = None if normalized_hint in {"", "auto"} else normalized_hint
        media_request = MediaRequest(
            request_id=str(request_id or uuid4()),
            source_hint=hint,
            query=str(query),
            device_id=None,
            context={
                "resolve_timeout_seconds": float(options.get("resolve_timeout_seconds", 8)),
            },
        )
        result = await self._core().resolve(media_request)
        resolved = result["resolved_media"]
        return {
            "resolved": True,
            "source_plugin": result["source_plugin"],
            "request_id": media_request.request_id,
            "media": {
                "media_id": resolved.media_id,
                "title": resolved.title,
                "stream_url": resolved.stream_url,
                "source": resolved.source,
                "is_live": bool(resolved.is_live),
            },
            "extra": {},
        }

    async def control_stop(self, device_id: str, request_id: str | None = None) -> dict[str, Any]:
        result = await self._core().stop(device_id)
        return {
            "status": "stopped",
            "device_id": device_id,
            "transport": result["transport"],
            "request_id": str(request_id or uuid4()),
            "extra": {"dispatch": result["dispatch"].data},
        }

    async def control_pause(self, device_id: str, request_id: str | None = None) -> dict[str, Any]:
        result = await self._core().pause(device_id)
        return {
            "status": "paused",
            "device_id": device_id,
            "transport": result["transport"],
            "request_id": str(request_id or uuid4()),
            "extra": {"dispatch": result["dispatch"].data},
        }

    async def control_resume(self, device_id: str, request_id: str | None = None) -> dict[str, Any]:
        result = await self._core().resume(device_id)
        return {
            "status": "resumed",
            "device_id": device_id,
            "transport": result["transport"],
            "request_id": str(request_id or uuid4()),
            "extra": {"dispatch": result["dispatch"].data},
        }

    async def control_tts(self, device_id: str, text: str, request_id: str | None = None) -> dict[str, Any]:
        result = await self._core().tts(device_id, text=text)
        return {
            "status": "ok",
            "device_id": device_id,
            "transport": result["transport"],
            "request_id": str(request_id or uuid4()),
            "extra": {"dispatch": result["dispatch"].data},
        }

    async def control_set_volume(
        self,
        device_id: str,
        volume: int,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        result = await self._core().set_volume(device_id, volume=volume)
        return {
            "status": "ok",
            "device_id": device_id,
            "transport": result["transport"],
            "request_id": str(request_id or uuid4()),
            "extra": {"volume": int(volume), "dispatch": result["dispatch"].data},
        }

    async def play_payload(self, payload: dict[str, Any], speaker_id: str) -> dict[str, Any]:
        source_hint = self._source_hint_from_payload(payload)
        try:
            media_request = self._legacy_adapter.adapt_request(
                request_id=str(uuid4()),
                speaker_id=speaker_id,
                payload=payload,
                fallback_hint=source_hint,
            )
            result = await self._core().play(
                media_request,
                device_id=speaker_id,
            )
        except (
            SourceResolveError,
            ExpiredStreamError,
            UndeliverableStreamError,
            TransportError,
            KeyError,
            ValueError,
        ) as exc:
            LOG.warning(
                "core_play_payload_failed source_hint=%s speaker_id=%s error=%s",
                source_hint,
                speaker_id,
                exc,
            )
            return self._core_error_result(speaker_id)

        prepared = result["prepared_stream"]
        dispatch = result["dispatch"]
        return {
            "sid": "",
            "speaker_id": speaker_id,
            "state": "playing",
            "title": result["resolved_media"].title,
            "stream_url": prepared.final_url,
            "error_code": None,
            "ok": True,
            "raw": {
                "ok": True,
                "mode": "core",
                "source": prepared.source,
                "transport": dispatch.transport,
                "dispatch": dispatch.data,
                "stream_url": prepared.final_url,
            },
        }

    async def play_local_library(self, speaker_id: str, music_name: str, search_key: str = "") -> dict[str, Any]:
        query = str(music_name or search_key or "").strip()
        if not query:
            return self._core_error_result(speaker_id, error_code="E_STREAM_NOT_FOUND")

        try:
            result = await self._core().play(
                MediaRequest(
                    request_id=str(uuid4()),
                    source_hint="local_library",
                    query=query,
                    device_id=speaker_id,
                    context={
                        "search_key": search_key,
                        "title": music_name,
                    },
                ),
                device_id=speaker_id,
            )
        except (
            SourceResolveError,
            ExpiredStreamError,
            UndeliverableStreamError,
            TransportError,
            KeyError,
            ValueError,
        ):
            return self._core_error_result(speaker_id, error_code="E_XIAOMI_PLAY_FAILED")

        prepared = result["prepared_stream"]
        dispatch = result["dispatch"]
        return {
            "sid": "",
            "speaker_id": speaker_id,
            "state": "playing",
            "title": result["resolved_media"].title,
            "stream_url": prepared.final_url,
            "error_code": None,
            "ok": True,
            "raw": {
                "ok": True,
                "mode": "core",
                "source": prepared.source,
                "transport": dispatch.transport,
                "dispatch": dispatch.data,
                "stream_url": prepared.final_url,
            },
        }

    async def play_local_music(self, speaker_id: str, music_name: str, search_key: str = "") -> dict[str, Any]:
        # compatibility_layer: keep legacy method name until all call-sites migrate.
        # removal_condition: remove after next minor release when /api/v1/play_music callers migrate.
        return await self.play_local_library(speaker_id, music_name, search_key)

    async def stop(self, target: dict[str, Any]) -> dict[str, Any]:
        sid = str(target.get("sid") or "")
        speaker_id = str(target.get("speaker_id") or "")

        if sid and self._runtime_provider is not None:
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
                result = await self._core().stop(speaker_id)
            except Exception:
                return self._core_error_result(speaker_id, error_code="E_STREAM_NOT_FOUND")
            raw = {
                "ret": "OK",
                "transport": result["transport"],
                "dispatch": result["dispatch"].data,
            }
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

    async def pause(self, speaker_id: str) -> dict[str, Any]:
        try:
            result = await self._core().pause(speaker_id)
        except Exception:
            return self._core_error_result(speaker_id, error_code="E_XIAOMI_PLAY_FAILED")
        return {
            "speaker_id": speaker_id,
            "ok": True,
            "error_code": None,
            "raw": {
                "ret": "OK",
                "transport": result["transport"],
                "dispatch": result["dispatch"].data,
            },
        }

    async def tts(self, speaker_id: str, text: str) -> dict[str, Any]:
        try:
            result = await self._core().tts(speaker_id, text=text)
        except Exception:
            return self._core_error_result(speaker_id, error_code="E_XIAOMI_PLAY_FAILED")
        return {
            "speaker_id": speaker_id,
            "ok": True,
            "error_code": None,
            "raw": {
                "ret": "OK",
                "transport": result["transport"],
                "dispatch": result["dispatch"].data,
            },
        }

    async def set_volume(self, speaker_id: str, volume: int) -> dict[str, Any]:
        level = self._as_int(volume, default=0)
        try:
            result = await self._core().set_volume(speaker_id, volume=level)
        except Exception:
            return self._core_error_result(speaker_id, error_code="E_XIAOMI_PLAY_FAILED")
        return {
            "speaker_id": speaker_id,
            "ok": True,
            "error_code": None,
            "raw": {
                "ret": "OK",
                "volume": level,
                "transport": result["transport"],
                "dispatch": result["dispatch"].data,
            },
        }

    async def probe(self, speaker_id: str) -> dict[str, Any]:
        try:
            result = await self._core().probe(speaker_id)
        except Exception:
            return self._core_error_result(speaker_id, error_code="E_XIAOMI_PLAY_FAILED")
        reachability = result.get("reachability")
        return {
            "speaker_id": speaker_id,
            "ok": True,
            "error_code": None,
            "raw": {
                "ret": "OK",
                "transport": result["transport"],
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
        if self._runtime_provider is not None:
            self._runtime().sweep_idle_sessions()
        sid = str(target.get("sid") or "")
        speaker_id = str(target.get("speaker_id") or "")

        if sid and self._runtime_provider is not None:
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
