from __future__ import annotations

import logging
import time
from typing import Any

from xiaomusic.core.models.media import PreparedStream
from xiaomusic.core.transport.transport import Transport


LOG = logging.getLogger("xiaomusic.transport.mina")


class MinaTransport(Transport):
    """Mina transport adapter.

    Real action coverage in Phase 2:
    - play_url, stop, pause, tts, set_volume, probe
    """

    name = "mina"

    def __init__(self, xiaomusic: Any) -> None:
        self._xiaomusic = xiaomusic

    async def play_url(
        self, device_id: str, prepared: PreparedStream
    ) -> dict[str, Any]:
        start = time.perf_counter()
        try:
            ret = await self._xiaomusic.play_url(did=device_id, arg1=prepared.final_url)
            out = {"ret": ret, "url": prepared.final_url}
            LOG.info(
                "transport_action action=play_url success=true latency_ms=%d device_id=%s",
                int((time.perf_counter() - start) * 1000),
                device_id,
            )
            return out
        except Exception:
            LOG.warning(
                "transport_action action=play_url success=false latency_ms=%d device_id=%s",
                int((time.perf_counter() - start) * 1000),
                device_id,
            )
            raise

    async def stop(self, device_id: str) -> dict[str, Any]:
        start = time.perf_counter()
        try:
            ret = await self._xiaomusic.stop(did=device_id, arg1="notts")
            LOG.info(
                "transport_action action=stop success=true latency_ms=%d device_id=%s",
                int((time.perf_counter() - start) * 1000),
                device_id,
            )
            return {"ret": ret}
        except Exception:
            LOG.warning(
                "transport_action action=stop success=false latency_ms=%d device_id=%s",
                int((time.perf_counter() - start) * 1000),
                device_id,
            )
            raise

    async def previous(self, device_id: str) -> dict[str, Any]:
        start = time.perf_counter()
        try:
            player = self._xiaomusic.device_manager.devices[device_id]
            await player.play_prev()
            LOG.info(
                "transport_action action=previous success=true latency_ms=%d device_id=%s",
                int((time.perf_counter() - start) * 1000),
                device_id,
            )
            return {"ret": "OK"}
        except Exception:
            LOG.warning(
                "transport_action action=previous success=false latency_ms=%d device_id=%s",
                int((time.perf_counter() - start) * 1000),
                device_id,
            )
            raise

    async def next(self, device_id: str) -> dict[str, Any]:
        start = time.perf_counter()
        try:
            player = self._xiaomusic.device_manager.devices[device_id]
            await player.play_next()
            LOG.info(
                "transport_action action=next success=true latency_ms=%d device_id=%s",
                int((time.perf_counter() - start) * 1000),
                device_id,
            )
            return {"ret": "OK"}
        except Exception:
            LOG.warning(
                "transport_action action=next success=false latency_ms=%d device_id=%s",
                int((time.perf_counter() - start) * 1000),
                device_id,
            )
            raise

    async def pause(self, device_id: str) -> dict[str, Any]:
        start = time.perf_counter()
        try:
            player = self._xiaomusic.device_manager.devices[device_id]
            await player.pause()
            LOG.info(
                "transport_action action=pause success=true latency_ms=%d device_id=%s",
                int((time.perf_counter() - start) * 1000),
                device_id,
            )
            if player.event_bus is not None:
                try:
                    status_info = await player.get_player_status()
                    if int(status_info.get("status", 0) or 0) == 1:
                        player.is_playing = True
                    else:
                        player.is_playing = False
                    from xiaomusic.events import PLAYER_STATE_CHANGED

                    player.event_bus.publish(PLAYER_STATE_CHANGED, device_id=device_id)
                except Exception:
                    pass
            return {"ret": "OK"}
        except Exception:
            LOG.warning(
                "transport_action action=pause success=false latency_ms=%d device_id=%s",
                int((time.perf_counter() - start) * 1000),
                device_id,
            )
            raise

    async def tts(self, device_id: str, text: str) -> dict[str, Any]:
        start = time.perf_counter()
        try:
            ret = await self._xiaomusic.do_tts(device_id, text)
            LOG.info(
                "transport_action action=tts success=true latency_ms=%d device_id=%s",
                int((time.perf_counter() - start) * 1000),
                device_id,
            )
            return {"ret": ret}
        except Exception:
            LOG.warning(
                "transport_action action=tts success=false latency_ms=%d device_id=%s",
                int((time.perf_counter() - start) * 1000),
                device_id,
            )
            raise

    async def set_volume(self, device_id: str, volume: int) -> dict[str, Any]:
        start = time.perf_counter()
        try:
            ret = await self._xiaomusic.set_volume(device_id, volume)
            LOG.info(
                "transport_action action=set_volume success=true latency_ms=%d device_id=%s",
                int((time.perf_counter() - start) * 1000),
                device_id,
            )
            return {"ret": ret, "volume": volume}
        except Exception:
            LOG.warning(
                "transport_action action=set_volume success=false latency_ms=%d device_id=%s",
                int((time.perf_counter() - start) * 1000),
                device_id,
            )
            raise

    async def probe(self, device_id: str) -> dict[str, Any]:
        start = time.perf_counter()
        try:
            status = await self._xiaomusic.get_player_status(did=device_id)
            LOG.info(
                "transport_action action=probe success=true latency_ms=%d device_id=%s",
                int((time.perf_counter() - start) * 1000),
                device_id,
            )
            return {
                "local_reachable": True,
                "cloud_reachable": True,
                "status": status,
            }
        except Exception:
            LOG.warning(
                "transport_action action=probe success=false latency_ms=%d device_id=%s",
                int((time.perf_counter() - start) * 1000),
                device_id,
            )
            raise
