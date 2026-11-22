"""SpeakerClient: the only place that sends commands to the Xiaomi cloud
(ADR-0007 step 2).

Every speaker command goes through ``AuthManager.mina_call`` / ``miio_call``
(auth retry and recovery live there). This class names the commands the
project actually uses, so callers no longer pass raw MiNA method names
around and the cloud surface is visible in one file.

Behavior is intentionally identical to the previous direct calls: same MiNA
method, same arguments, ``retry=1``, and the same ``ctx`` strings in logs.
"""

from __future__ import annotations

import json
from typing import Any

from miservice import miio_command


class SpeakerClient:
    def __init__(self, auth_manager):
        self._auth = auth_manager

    async def _mina(self, method: str, *args, ctx: str, **kwargs) -> Any:
        return await self._auth.mina_call(method, *args, retry=1, ctx=ctx, **kwargs)

    # ── playback ────────────────────────────────────────────────────────

    async def play_url(self, device_id: str, url: str, *, ctx: str = "play_one_url"):
        """Single URL via ``play_by_url`` (hardware without music API)."""
        return await self._mina("play_by_url", device_id, url, ctx=ctx)

    async def play_music_url(
        self, device_id: str, url: str, *, audio_id: str, ctx: str = "play_one_url_music_api"
    ):
        """Single URL via ``play_by_music_url`` (replaces the speaker queue)."""
        return await self._mina("play_by_music_url", device_id, url, audio_id=audio_id, ctx=ctx)

    async def stop(self, device_id: str, *, ctx: str):
        return await self._mina("player_stop", device_id, ctx=ctx)

    async def pause(self, device_id: str, *, ctx: str):
        return await self._mina("player_pause", device_id, ctx=ctx)

    async def set_volume(self, device_id: str, volume: int, *, ctx: str = "set_volume"):
        return await self._mina("player_set_volume", device_id, volume, ctx=ctx)

    # ── status ──────────────────────────────────────────────────────────

    async def status_raw(self, device_id: str, *, ctx: str) -> Any:
        """Raw ``player_get_status`` response."""
        return await self._mina("player_get_status", device_id, ctx=ctx)

    @staticmethod
    def parse_status(raw: Any) -> dict:
        """``{"status", "volume", "loop_type"}`` from a raw status response.

        Raises on malformed responses, like the callers did before.
        """
        return json.loads(raw.get("data", {}).get("info", "{}"))

    # ── speech ──────────────────────────────────────────────────────────

    async def tts(self, device_id: str, text: str, *, ctx: str = "text_to_speech"):
        return await self._mina("text_to_speech", device_id, text, ctx=ctx)

    async def miio_command(self, did: str, command: str, *, ctx: str):
        """Raw MiIO command (used for TTS on hardware that needs it)."""
        return await self._auth.miio_call(
            lambda: miio_command(self._auth.miio_service, did, command),
            retry=1,
            ctx=ctx,
        )

    # ── low level ───────────────────────────────────────────────────────

    async def ubus(self, device_id: str, method: str, path: str, message: Any, *, ctx: str):
        return await self._mina("ubus_request", device_id, method, path, message, ctx=ctx)

    async def device_list(self, *, ctx: str = "getalldevices"):
        return await self._mina("device_list", ctx=ctx)
