"""M1 play_url orchestration: classify -> resolve -> start stream."""

from __future__ import annotations

from dataclasses import asdict

from xiaomusic.network_audio.audio_streamer import AudioStreamer
from xiaomusic.network_audio.session_manager import StreamSessionManager


class M1PlayService:
    def __init__(self, session_manager: StreamSessionManager, resolver, audio_streamer: AudioStreamer) -> None:
        self.session_manager = session_manager
        self.resolver = resolver
        self.audio_streamer = audio_streamer

    def play_url(self, url: str) -> dict:
        session = self.session_manager.create_session(input_url=url)
        resolved = self.resolver.resolve(url, timeout_seconds=8)
        if not resolved.ok:
            self.session_manager.set_state(session.sid, "stopped")
            return {
                "ok": False,
                "error_code": resolved.error_code,
                "error_message": resolved.error_message,
                "session": asdict(self.session_manager.get_session(session.sid)),
            }

        started = self.audio_streamer.start_stream(session.sid, resolved.source_url)
        if not started:
            self.session_manager.set_state(session.sid, "stopped")
            return {
                "ok": False,
                "error_code": "E_STREAM_START_FAILED",
                "error_message": "failed to start stream",
                "session": asdict(self.session_manager.get_session(session.sid)),
            }

        current = self.session_manager.get_session(session.sid)
        return {
            "ok": True,
            "error_code": None,
            "error_message": None,
            "session": asdict(current),
        }
