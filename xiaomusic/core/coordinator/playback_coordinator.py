from __future__ import annotations

from xiaomusic.core.delivery.delivery_adapter import DeliveryAdapter
from xiaomusic.core.device.device_registry import DeviceRegistry
from xiaomusic.core.errors.stream_errors import ExpiredStreamError
from xiaomusic.core.models.media import MediaRequest
from xiaomusic.core.source.source_registry import SourceRegistry
from xiaomusic.core.transport.transport_router import TransportRouter


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
            return {
                "ok": dispatch_result.ok,
                "transport": dispatch_result.transport,
                "prepared_stream": prepared,
                "resolved_media": resolved,
                "dispatch": dispatch_result,
            }

        if last_expired_error is not None:
            raise last_expired_error
        raise RuntimeError("playback failed without transport dispatch")
