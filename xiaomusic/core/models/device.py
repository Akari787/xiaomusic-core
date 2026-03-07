from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class DeviceProfile:
    did: str
    model: str
    name: str
    group: str


@dataclass(slots=True)
class DeviceReachability:
    ip: str
    local_reachable: bool
    cloud_reachable: bool
    last_probe_ts: int
