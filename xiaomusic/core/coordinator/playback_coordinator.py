from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from xiaomusic.core.delivery.delivery_adapter import DeliveryAdapter
from xiaomusic.core.device.device_registry import DeviceRegistry
from xiaomusic.core.errors.stream_errors import ExpiredStreamError, UndeliverableStreamError
from xiaomusic.core.errors.transport_errors import TransportError
from xiaomusic.core.models.media import DeliveryPlan, MediaRequest, PlaybackAttempt, PlaybackOutcome, PreparedStream
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
        playback_status_provider: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
    ) -> None:
        self._source_registry = source_registry
        self._device_registry = device_registry
        self._delivery_adapter = delivery_adapter
        self._transport_router = transport_router
        self._max_resolve_retry = max_resolve_retry
        self._playback_status_provider = playback_status_provider

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
                plan = self._delivery_adapter.prepare_plan(resolved, context=request.context)
            except UndeliverableStreamError:
                raise
            except ExpiredStreamError as exc:
                last_expired_error = exc
                continue

            dispatch_result, used_prepared, outcome = await self._dispatch_with_adaptive_delivery(
                plan=plan,
                profile=profile,
                capability=capability,
                device_id=target_device_id,
                request_context=request.context,
            )
            return {
                "ok": dispatch_result.ok,
                "request_id": request.request_id,
                "transport": dispatch_result.transport,
                "prepared_stream": used_prepared,
                "resolved_media": resolved,
                "dispatch": dispatch_result,
                "delivery_plan": plan,
                "outcome": outcome,
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

    async def resolve(self, request: MediaRequest) -> dict:
        plugin = self._source_registry.get_plugin(request.source_hint, request)
        LOG.info(
            "core_chain action=resolve request_id=%s source_hint=%s source_plugin=%s",
            request.request_id,
            request.source_hint,
            plugin.name,
        )
        resolved = await plugin.resolve(request)
        return {
            "ok": True,
            "request_id": request.request_id,
            "source_plugin": plugin.name,
            "resolved_media": resolved,
        }

    async def stop(self, device_id: str) -> dict:
        return await self._dispatch_action("stop", device_id)

    async def pause(self, device_id: str) -> dict:
        return await self._dispatch_action("pause", device_id)

    async def resume(self, device_id: str) -> dict:
        # compatibility_layer: current transports expose pause only.
        # Xiaomi device pause command behaves as pause/resume toggle on supported models.
        return await self._dispatch_action("resume", device_id, transport_action="pause")

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
        transport_action: str | None = None,
    ) -> dict:
        profile = self._device_registry.get_profile(device_id)
        capability = self._device_registry.get_capability_matrix(device_id)
        LOG.info("core_chain action=%s device_id=%s request_id=control-%s", action, device_id, device_id)
        routed_action = transport_action or action
        try:
            dispatch_result = await self._transport_router.dispatch(
                action=routed_action,
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

    async def _dispatch_with_adaptive_delivery(
        self,
        *,
        plan: DeliveryPlan,
        profile,
        capability,
        device_id: str,
        request_context: dict[str, Any],
    ) -> tuple[Any, PreparedStream, PlaybackOutcome]:
        attempts: list[PlaybackAttempt] = []
        first = await self._dispatch_single(plan.primary, profile, capability, device_id, request_context)
        attempts.append(first[2])
        if self._attempt_passed(first[2]):
            return first[0], first[1], PlaybackOutcome(
                accepted=True,
                started=first[2].started,
                final_path=first[2].path,
                fallback_triggered=False,
                attempts=attempts,
            )

        if plan.fallback is None:
            raise TransportError("playback command accepted but device did not start playing")

        second = await self._dispatch_single(plan.fallback, profile, capability, device_id, request_context)
        attempts.append(second[2])
        if self._attempt_passed(second[2]):
            return second[0], second[1], PlaybackOutcome(
                accepted=True,
                started=second[2].started,
                final_path=second[2].path,
                fallback_triggered=True,
                attempts=attempts,
            )

        raise TransportError("playback command accepted but both direct and proxy paths failed to start")

    async def _dispatch_single(
        self,
        prepared: PreparedStream,
        profile,
        capability,
        device_id: str,
        request_context: dict[str, Any],
    ) -> tuple[Any, PreparedStream, PlaybackAttempt]:
        dispatch_result = await self._transport_router.dispatch_play_url(
            prepared=prepared,
            profile=profile,
            capability_matrix=capability,
        )
        started = await self._confirm_playback_started(device_id, request_context)
        attempt = PlaybackAttempt(
            path="proxy" if prepared.is_proxy else "direct",
            transport=dispatch_result.transport,
            url=prepared.final_url,
            accepted=bool(dispatch_result.ok),
            started=started,
        )
        LOG.info(
            "core_chain prepared source=%s transport=%s final_url=%s path=%s started=%s",
            prepared.source,
            dispatch_result.transport,
            prepared.final_url,
            attempt.path,
            str(started),
        )
        return dispatch_result, prepared, attempt

    @staticmethod
    def _attempt_passed(attempt: PlaybackAttempt) -> bool:
        return bool(attempt.accepted and (attempt.started is True or attempt.started is None))

    async def _confirm_playback_started(self, device_id: str, request_context: dict[str, Any]) -> bool | None:
        if self._playback_status_provider is None:
            return None
        if request_context.get("confirm_start", True) is False:
            return None

        retries = max(0, int(request_context.get("confirm_start_retries", 2)))
        delay_ms = max(0, int(request_context.get("confirm_start_delay_ms", 1200)))
        interval_ms = max(100, int(request_context.get("confirm_start_interval_ms", 600)))

        await asyncio.sleep(delay_ms / 1000)
        saw_true = False
        saw_false = False
        saw_drop_after_true = False
        for idx in range(retries + 1):
            try:
                status = await self._playback_status_provider(device_id)
            except Exception as exc:
                LOG.warning("core_chain status_probe_failed device_id=%s error=%s", device_id, exc.__class__.__name__)
                return None
            started = self._status_started(status)
            if started is True:
                saw_true = True
            elif started is False:
                if saw_true:
                    saw_drop_after_true = True
                saw_false = True
            if idx < retries:
                await asyncio.sleep(interval_ms / 1000)
        if saw_drop_after_true:
            return False
        if saw_true:
            return True
        if saw_false:
            return False
        return None

    @staticmethod
    def _status_started(status: dict[str, Any]) -> bool | None:
        raw = status.get("status")
        if isinstance(raw, str):
            normalized = raw.strip().lower()
            if normalized in {"1", "playing", "streaming"}:
                return True
            if normalized in {"0", "paused", "stopped", "idle"}:
                return False
        if isinstance(raw, (int, float)):
            if int(raw) == 1:
                return True
            if int(raw) == 0:
                return False
        is_playing = status.get("is_playing")
        if isinstance(is_playing, bool):
            return is_playing
        return None
