"""Xiaomi playback adapter contract for relay."""

from __future__ import annotations


class XiaomiPlaybackAdapter:
    def __init__(self, mina_service=None, auth_manager=None) -> None:
        self.mina_service = mina_service
        self.auth_manager = auth_manager

    async def play(self, speaker_id: str, stream_url: str) -> dict:
        try:
            if self.auth_manager is not None:
                ret = await self.auth_manager.mina_call(
                    "play_by_music_url",
                    speaker_id,
                    stream_url,
                    audio_id="network_audio_stream",
                    retry=1,
                    ctx="network_audio_adapter",
                )
            else:
                ret = await self.mina_service.play_by_music_url(
                    device_id=speaker_id,
                    url=stream_url,
                    audio_id="network_audio_stream",
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
