"""Xiaomi playback adapter contract for M1."""

from __future__ import annotations


class XiaomiPlaybackAdapter:
    def __init__(self, mina_service) -> None:
        self.mina_service = mina_service

    async def play(self, speaker_id: str, stream_url: str) -> dict:
        try:
            ret = await self.mina_service.play_by_music_url(
                device_id=speaker_id,
                url=stream_url,
                audio_id="m1_stream",
            )
        except Exception as exc:  # noqa: BLE001
            return {
                "accepted": False,
                "error_code": "E_XIAOMI_PLAY_FAILED",
                "reason": str(exc),
                "raw": None,
            }

        code = ret.get("code") if isinstance(ret, dict) else None
        if code == 0:
            return {
                "accepted": True,
                "error_code": None,
                "reason": None,
                "raw": ret,
            }

        return {
            "accepted": False,
            "error_code": "E_XIAOMI_PLAY_FAILED",
            "reason": ret.get("message") if isinstance(ret, dict) else "unknown",
            "raw": ret,
        }
