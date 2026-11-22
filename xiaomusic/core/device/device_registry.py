from __future__ import annotations

import time
from typing import Any

from xiaomusic.core.errors.device_errors import DeviceNotFoundError
from xiaomusic.core.models.device import DeviceProfile, DeviceReachability
from xiaomusic.core.models.transport import TransportCapabilityMatrix


class DeviceRegistry:
    """In-memory device registry with optional hydration from legacy runtime."""

    def __init__(self, xiaomusic: Any | None = None) -> None:
        self._xiaomusic = xiaomusic
        self._profiles: dict[str, DeviceProfile] = {}
        self._reachability: dict[str, DeviceReachability] = {}
        self._capability: dict[str, TransportCapabilityMatrix] = {}

    def register_device(
        self,
        profile: DeviceProfile,
        reachability: DeviceReachability,
        capability_matrix: TransportCapabilityMatrix,
    ) -> None:
        did = profile.did
        self._profiles[did] = profile
        self._reachability[did] = reachability
        self._capability[did] = capability_matrix

    def invalidate_device(self, device_id: str) -> None:
        self._profiles.pop(device_id, None)
        self._reachability.pop(device_id, None)
        self._capability.pop(device_id, None)

    # Alias retained for callers that treat registry invalidation as unregister.
    unregister_device = invalidate_device

    def get_profile(self, device_id: str) -> DeviceProfile:
        self._ensure_device(device_id)
        return self._profiles[device_id]

    def get_reachability(self, device_id: str) -> DeviceReachability:
        self._ensure_device(device_id)
        return self._reachability[device_id]

    def get_capability_matrix(self, device_id: str) -> TransportCapabilityMatrix:
        self._ensure_device(device_id)
        return self._capability[device_id]

    def update_reachability(self, device_id: str, probe_result: dict[str, Any]) -> DeviceReachability:
        self._ensure_device(device_id)
        current = self._reachability[device_id]
        updated = DeviceReachability(
            ip=str(probe_result.get("ip", current.ip)),
            local_reachable=bool(probe_result.get("local_reachable", current.local_reachable)),
            cloud_reachable=bool(probe_result.get("cloud_reachable", current.cloud_reachable)),
            last_probe_ts=int(probe_result.get("last_probe_ts", int(time.time()))),
        )
        self._reachability[device_id] = updated
        return updated

    def _ensure_device(self, device_id: str) -> None:
        if self._xiaomusic is not None:
            player = self._legacy_player(device_id)
            if player is None:
                self.invalidate_device(device_id)
                raise DeviceNotFoundError(f"unknown device_id: {device_id}")
            if device_id in self._profiles:
                self._refresh_profile_from_legacy(device_id, player)
                return
        elif device_id in self._profiles:
            return
        self._hydrate_from_legacy(device_id)

    def _legacy_player(self, device_id: str) -> Any | None:
        devices = getattr(getattr(self._xiaomusic, "device_manager", None), "devices", {})
        return devices.get(device_id)

    def _refresh_profile_from_legacy(self, device_id: str, device_player: Any) -> None:
        device = getattr(device_player, "device", None)
        self._profiles[device_id] = DeviceProfile(
            did=device_id,
            model=str(getattr(device, "hardware", "unknown")),
            name=str(getattr(device, "name", device_id)),
            group=str(getattr(device_player, "group_name", "default")),
        )
        # Before any probe, the runtime host is the only IP evidence.  A probe
        # result becomes authoritative afterwards and must survive profile reads.
        reachability = self._reachability.get(device_id)
        if reachability is not None and reachability.last_probe_ts == 0:
            reachability.ip = str(getattr(device, "host", ""))

    def _hydrate_from_legacy(self, device_id: str) -> None:
        if self._xiaomusic is None:
            raise DeviceNotFoundError(f"unknown device_id: {device_id}")
        device_player = self._legacy_player(device_id)
        if device_player is None:
            raise DeviceNotFoundError(f"unknown device_id: {device_id}")

        device = getattr(device_player, "device", None)
        profile = DeviceProfile(
            did=device_id,
            model=str(getattr(device, "hardware", "unknown")),
            name=str(getattr(device, "name", device_id)),
            group=str(getattr(device_player, "group_name", "default")),
        )
        reachability = DeviceReachability(
            ip=str(getattr(device, "host", "")),
            local_reachable=False,
            cloud_reachable=False,
            last_probe_ts=0,
        )
        capability = TransportCapabilityMatrix(
            play=["mina"],
            previous=["miio", "mina"],
            next=["miio", "mina"],
            volume=["miio", "mina"],
            stop=["miio", "mina"],
            pause=["miio", "mina"],
            resume=["miio", "mina"],
            probe=["miio", "mina"],
        )
        self.register_device(profile, reachability, capability)
