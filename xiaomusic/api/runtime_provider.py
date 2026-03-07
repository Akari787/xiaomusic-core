from __future__ import annotations

from xiaomusic.network_audio.runtime import NetworkAudioRuntime

_runtime: NetworkAudioRuntime | None = None


def get_runtime() -> NetworkAudioRuntime:
    global _runtime
    if _runtime is None:
        from xiaomusic.api.dependencies import xiaomusic

        _runtime = NetworkAudioRuntime(xiaomusic)
    return _runtime
