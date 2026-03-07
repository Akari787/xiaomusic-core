from __future__ import annotations

from typing import Any

from xiaomusic.core.models.media import PreparedStream
from xiaomusic.core.transport.transport import Transport


class MiioTransport(Transport):
    """Miio transport adapter.

    Phase 2 status:
    - stop/pause/tts/set_volume/probe: direct device-path compatibility adapter
    - play_url: compatibility placeholder that still delegates to legacy play_url path
      (not a standalone local Miio media streaming implementation)
    """

    name = "miio"

    def __init__(self, xiaomusic: Any) -> None:
        self._xiaomusic = xiaomusic

    async def play_url(self, device_id: str, prepared: PreparedStream) -> dict[str, Any]:
        # Compatibility placeholder only. Keep explicit marker for follow-up phase.
        ret = await self._xiaomusic.play_url(did=device_id, arg1=prepared.final_url)
        return {
            "ret": ret,
            "url": prepared.final_url,
            "compat_mode": "delegated_legacy_play_url",
        }

    async def stop(self, device_id: str) -> dict[str, Any]:
        player = self._xiaomusic.device_manager.devices[device_id]
        await player.stop(arg1="notts")
        return {"ret": "OK"}

    async def pause(self, device_id: str) -> dict[str, Any]:
        player = self._xiaomusic.device_manager.devices[device_id]
        await player.pause()
        return {"ret": "OK"}

    async def tts(self, device_id: str, text: str) -> dict[str, Any]:
        ret = await self._xiaomusic.do_tts(device_id, text)
        return {"ret": ret}

    async def set_volume(self, device_id: str, volume: int) -> dict[str, Any]:
        ret = await self._xiaomusic.set_volume(device_id, volume)
        return {"ret": ret, "volume": volume}

    async def probe(self, device_id: str) -> dict[str, Any]:
        player = self._xiaomusic.device_manager.devices[device_id]
        status = await player.get_player_status()
        return {
            "local_reachable": True,
            "cloud_reachable": False,
            "status": status,
        }
