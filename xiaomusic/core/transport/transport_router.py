from __future__ import annotations

import logging
from typing import Any

from xiaomusic.core.errors.transport_errors import TransportError
from xiaomusic.core.models.device import DeviceProfile
from xiaomusic.core.models.media import PreparedStream
from xiaomusic.core.models.transport import TransportCapabilityMatrix, TransportDispatchResult
from xiaomusic.core.transport.transport import Transport
from xiaomusic.core.transport.transport_policy import TransportPolicy


LOG = logging.getLogger("xiaomusic.core.transport_router")


class TransportRouter:
    """Route actions to transport by capability-policy intersection."""

    def __init__(self, policy: TransportPolicy | None = None) -> None:
        self._policy = policy or TransportPolicy()
        self._transports: dict[str, Transport] = {}

    def register_transport(self, transport: Transport) -> None:
        self._transports[transport.name] = transport

    async def dispatch_play_url(
        self,
        prepared: PreparedStream,
        profile: DeviceProfile,
        capability_matrix: TransportCapabilityMatrix,
    ) -> TransportDispatchResult:
        return await self.dispatch(
            action="play",
            device_id=profile.did,
            profile=profile,
            capability_matrix=capability_matrix,
            prepared=prepared,
        )

    async def dispatch(
        self,
        action: str,
        device_id: str,
        profile: DeviceProfile,
        capability_matrix: TransportCapabilityMatrix,
        prepared: PreparedStream | None = None,
        text: str | None = None,
        volume: int | None = None,
    ) -> TransportDispatchResult:
        _ = profile
        candidate_transports = self._candidate_transports(action, capability_matrix)
        LOG.debug(
            "transport_route action=%s candidate_transports=%s",
            action,
            candidate_transports,
        )
        if not candidate_transports:
            raise TransportError(f"no candidate transport for action={action}")

        errors: list[str] = []
        for transport_name in candidate_transports:
            transport = self._transports.get(transport_name)
            if transport is None:
                errors.append(f"transport not registered: {transport_name}")
                continue

            try:
                result = await self._invoke(
                    transport=transport,
                    action=action,
                    device_id=device_id,
                    prepared=prepared,
                    text=text,
                    volume=volume,
                )
                LOG.info(
                    "transport_route action=%s candidate_transports=%s selected_transport=%s fallback_triggered=%s",
                    action,
                    candidate_transports,
                    transport_name,
                    str(bool(errors)).lower(),
                )
                return TransportDispatchResult(
                    ok=True,
                    action=action,
                    transport=transport_name,
                    data=result,
                    errors=errors,
                )
            except Exception as exc:
                errors.append(f"{transport_name}: {exc}")
                LOG.warning(
                    "transport_route action=%s candidate_transports=%s selected_transport=%s fallback_triggered=true error=%s",
                    action,
                    candidate_transports,
                    transport_name,
                    exc,
                )
                continue

        raise TransportError(f"all candidate transports failed for action={action}: {errors}")

    def _candidate_transports(
        self,
        action: str,
        capability_matrix: TransportCapabilityMatrix,
    ) -> list[str]:
        policy_order = self._policy.get(action)
        supported = set(capability_matrix.by_action(action))
        return [name for name in policy_order if name in supported]

    async def _invoke(
        self,
        transport: Transport,
        action: str,
        device_id: str,
        prepared: PreparedStream | None,
        text: str | None,
        volume: int | None,
    ) -> dict[str, Any]:
        if action == "play":
            if prepared is None:
                raise TransportError("prepared stream is required for play")
            return await transport.play_url(device_id, prepared)
        if action == "stop":
            return await transport.stop(device_id)
        if action == "pause":
            return await transport.pause(device_id)
        if action == "tts":
            if text is None:
                raise TransportError("text is required for tts")
            return await transport.tts(device_id, text)
        if action == "volume":
            if volume is None:
                raise TransportError("volume is required for set_volume")
            return await transport.set_volume(device_id, volume)
        if action == "probe":
            return await transport.probe(device_id)
        raise TransportError(f"unsupported action: {action}")
