from __future__ import annotations

from xiaomusic.relay.runtime import RelayRuntime

_runtime: RelayRuntime | None = None


def get_runtime() -> RelayRuntime:
    global _runtime
    if _runtime is None:
        from xiaomusic.api.dependencies import xiaomusic

        _runtime = RelayRuntime(xiaomusic)
    return _runtime
