from __future__ import annotations

import logging
import time
from typing import Any

from xiaomusic.core.errors.transport_errors import TransportError
from xiaomusic.core.models.media import PreparedStream
from xiaomusic.core.transport.transport import Transport


LOG = logging.getLogger("xiaomusic.transport.miio")


class MiioTransport(Transport):
    """Miio transport adapter.

    Phase 3 strategy:
    - stop/pause/tts/set_volume/probe: direct device-path compatibility adapter
    - play_url: explicitly unsupported as official play transport
    """

    name = "miio"

    def __init__(self, xiaomusic: Any) -> None:
        self._xiaomusic = xiaomusic

    async def play_url(
        self, device_id: str, prepared: PreparedStream
    ) -> dict[str, Any]:
        _ = (device_id, prepared)
        LOG.warning(
            "transport_action action=play_url success=false latency_ms=0 device_id=%s",
            device_id,
        )
        raise TransportError("miio play_url is not supported in phase3 strategy")

    async def stop(self, device_id: str) -> dict[str, Any]:
        start = time.perf_counter()
        try:
            player = self._xiaomusic.device_manager.devices[device_id]
            await player.stop(arg1="notts")
            LOG.info(
                "transport_action action=stop success=true latency_ms=%d device_id=%s",
                int((time.perf_counter() - start) * 1000),
                device_id,
            )
            return {"ret": "OK"}
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
            player = self._xiaomusic.device_manager.devices[device_id]
            status = await player.get_player_status()
            LOG.info(
                "transport_action action=probe success=true latency_ms=%d device_id=%s",
                int((time.perf_counter() - start) * 1000),
                device_id,
            )
            return {
                "local_reachable": True,
                "cloud_reachable": False,
                "status": status,
            }
        except Exception:
            LOG.warning(
                "transport_action action=probe success=false latency_ms=%d device_id=%s",
                int((time.perf_counter() - start) * 1000),
                device_id,
            )
            raise
