from __future__ import annotations

import logging

from xiaomusic.core.delivery.delivery_adapter import DeliveryAdapter
from xiaomusic.core.device.device_registry import DeviceRegistry
from xiaomusic.core.errors.stream_errors import ExpiredStreamError
from xiaomusic.core.models.media import MediaRequest
from xiaomusic.core.source.source_registry import SourceRegistry
from xiaomusic.core.transport.transport_router import TransportRouter


LOG = logging.getLogger("xiaomusic.core.playback_coordinator")


class PlaybackCoordinator:
    """Minimal playback chain orchestration."""

    def __init__(
        self,
        source_registry: SourceRegistry,
        device_registry: DeviceRegistry,
        delivery_adapter: DeliveryAdapter,
        transport_router: TransportRouter,
        max_resolve_retry: int = 1,
    ) -> None:
        self._source_registry = source_registry
        self._device_registry = device_registry
        self._delivery_adapter = delivery_adapter
        self._transport_router = transport_router
        self._max_resolve_retry = max_resolve_retry

    async def play(self, request: MediaRequest, device_id: str | None = None) -> dict:
        target_device_id = device_id or request.device_id
        if not target_device_id:
            raise ValueError("device_id is required")

        profile = self._device_registry.get_profile(target_device_id)
        capability = self._device_registry.get_capability_matrix(target_device_id)
        plugin = self._source_registry.get_plugin(request.source_hint, request)
        LOG.info(
            "core_chain action=play request_id=%s device_id=%s source_hint=%s source_plugin=%s",
            request.request_id,
            target_device_id,
            request.source_hint,
            plugin.name,
        )

        attempts = self._max_resolve_retry + 1
        last_expired_error: Exception | None = None
        for _ in range(attempts):
            resolved = await plugin.resolve(request)
            try:
                prepared = self._delivery_adapter.prepare(resolved)
            except ExpiredStreamError as exc:
                last_expired_error = exc
                continue

            dispatch_result = await self._transport_router.dispatch_play_url(
                prepared=prepared,
                profile=profile,
                capability_matrix=capability,
            )
            LOG.info(
                "core_chain prepared source=%s transport=%s final_url=%s",
                prepared.source,
                dispatch_result.transport,
                prepared.final_url,
            )
            return {
                "ok": dispatch_result.ok,
                "request_id": request.request_id,
                "transport": dispatch_result.transport,
                "prepared_stream": prepared,
                "resolved_media": resolved,
                "dispatch": dispatch_result,
            }

        if last_expired_error is not None:
            LOG.warning(
                "core_chain action=play request_id=%s device_id=%s error=ExpiredStreamError",
                request.request_id,
                target_device_id,
            )
            raise last_expired_error
        LOG.warning(
            "core_chain action=play request_id=%s device_id=%s error=RuntimeError",
            request.request_id,
            target_device_id,
        )
        raise RuntimeError("playback failed without transport dispatch")

    async def stop(self, device_id: str) -> dict:
        return await self._dispatch_action("stop", device_id)

    async def pause(self, device_id: str) -> dict:
        return await self._dispatch_action("pause", device_id)

    async def tts(self, device_id: str, text: str) -> dict:
        return await self._dispatch_action("tts", device_id, text=text)

    async def set_volume(self, device_id: str, volume: int) -> dict:
        return await self._dispatch_action("volume", device_id, volume=volume)

    async def probe(self, device_id: str) -> dict:
        result = await self._dispatch_action("probe", device_id)
        reachability = self._device_registry.update_reachability(
            device_id,
            probe_result=result["dispatch"].data,
        )
        result["reachability"] = reachability
        return result

    async def _dispatch_action(
        self,
        action: str,
        device_id: str,
        text: str | None = None,
        volume: int | None = None,
    ) -> dict:
        profile = self._device_registry.get_profile(device_id)
        capability = self._device_registry.get_capability_matrix(device_id)
        LOG.info("core_chain action=%s device_id=%s request_id=control-%s", action, device_id, device_id)
        try:
            dispatch_result = await self._transport_router.dispatch(
                action=action,
                device_id=device_id,
                profile=profile,
                capability_matrix=capability,
                text=text,
                volume=volume,
            )
        except Exception as exc:
            LOG.warning(
                "core_chain action=%s device_id=%s error=%s",
                action,
                device_id,
                exc.__class__.__name__,
            )
            raise
        return {
            "ok": dispatch_result.ok,
            "transport": dispatch_result.transport,
            "dispatch": dispatch_result,
        }
