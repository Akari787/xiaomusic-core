"""Runtime holder for M1 pipeline inside main app."""

from __future__ import annotations

import os
import time
from dataclasses import asdict
from threading import Lock
from urllib.parse import urlparse

from xiaomusic.m1.audio_streamer import AudioStreamer
from xiaomusic.m1.local_http_stream_server import LocalHttpStreamServer
from xiaomusic.m1.play_service import M1PlayService
from xiaomusic.m1.reconnect_policy import ReconnectPolicy
from xiaomusic.m1.resolver import Resolver
from xiaomusic.m1.session_manager import StreamSessionManager


class M1Runtime:
    def __init__(self, xiaomusic) -> None:
        self.xiaomusic = xiaomusic
        self.stream_port = int(os.getenv("XIAOMUSIC_M1_STREAM_PORT", "18090"))
        self._started_at = time.monotonic()
        self._lock = Lock()

        self.session_manager = StreamSessionManager()
        self.stream_server = LocalHttpStreamServer(
            session_manager=self.session_manager,
            host="0.0.0.0",
            port=self.stream_port,
        )
        self.audio_streamer = AudioStreamer(
            session_manager=self.session_manager,
            stream_server=self.stream_server,
            reconnect_policy=ReconnectPolicy(base_delay_seconds=1, max_delay_seconds=8, max_retries=3),
        )
        self.resolver = Resolver()
        self.play_service = M1PlayService(
            session_manager=self.session_manager,
            resolver=self.resolver,
            audio_streamer=self.audio_streamer,
        )

    def ensure_started(self) -> None:
        with self._lock:
            self.stream_server.start()

    def _public_base(self) -> str:
        parsed = urlparse(self.xiaomusic.config.hostname)
        host = parsed.hostname or "127.0.0.1"
        scheme = parsed.scheme or "http"
        return f"{scheme}://{host}:{self.stream_port}"

    async def play_and_cast(self, did: str, url: str) -> dict:
        self.ensure_started()
        out = self.play_service.play_url(url)
        if not out.get("ok"):
            return out

        sid = out["session"]["sid"]
        public_stream_url = f"{self._public_base()}/stream/{sid}"
        self.session_manager.set_stream_url(sid, public_stream_url)
        out["session"]["stream_url"] = public_stream_url

        cast_ret = await self.xiaomusic.play_url(did=did, arg1=public_stream_url)
        out["cast_ret"] = cast_ret
        return out

    def healthz(self) -> dict:
        return {
            "status": "ok",
            "uptime_seconds": int(time.monotonic() - self._started_at),
            "stream_port": self.stream_port,
        }

    def sessions(self) -> dict:
        return {"sessions": [asdict(s) for s in self.session_manager.list_sessions()]}

    def stop_session(self, sid: str) -> dict:
        self.audio_streamer.stop_stream(sid)
        sess = self.session_manager.get_session(sid)
        return {"ret": "OK", "session": asdict(sess) if sess else None}
