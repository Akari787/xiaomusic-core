"""Runtime holder for network audio pipeline inside main app."""

from __future__ import annotations

import os
import time
from dataclasses import asdict
from threading import Lock
from urllib.parse import urlparse
from urllib.request import urlopen

from xiaomusic.network_audio.audio_streamer import AudioStreamer
from xiaomusic.network_audio.contracts import ERROR_CODES
from xiaomusic.network_audio.local_http_stream_server import LocalHttpStreamServer
from xiaomusic.network_audio.play_service import NetworkAudioPlayService
from xiaomusic.network_audio.reconnect_policy import ReconnectPolicy
from xiaomusic.network_audio.resolver import Resolver
from xiaomusic.network_audio.session_manager import StreamSessionManager
from xiaomusic.playback.link_strategy import LinkPlaybackStrategy


class NetworkAudioRuntime:
    def __init__(self, xiaomusic) -> None:
        self.xiaomusic = xiaomusic
        stream_port = os.getenv("XIAOMUSIC_NETWORK_AUDIO_STREAM_PORT")
        if not stream_port:
            stream_port = os.getenv("XIAOMUSIC_M1_STREAM_PORT", "18090")
        self.stream_port = int(stream_port)
        self._started_at = time.monotonic()
        self._last_cleanup_at: float | None = None
        self._lock = Lock()
        self._strategy = getattr(xiaomusic, "link_playback_strategy", None)
        if self._strategy is None:
            self._strategy = LinkPlaybackStrategy(xiaomusic.music_library, xiaomusic.log)

        self.session_manager = StreamSessionManager()
        self.stream_server = LocalHttpStreamServer(
            session_manager=self.session_manager,
            host="127.0.0.1",
            port=self.stream_port,
            max_clients_per_sid=6,
        )
        self.audio_streamer = AudioStreamer(
            session_manager=self.session_manager,
            stream_server=self.stream_server,
            reconnect_policy=ReconnectPolicy(base_delay_seconds=1, max_delay_seconds=3, max_retries=30),
            source_read_timeout_seconds=15,
            relay_mode="ffmpeg",
        )
        self.resolver = Resolver()
        self.play_service = NetworkAudioPlayService(
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
        return f"{scheme}://{host}:{self.xiaomusic.config.public_port}"

    def _internal_stream_url(self, sid: str) -> str:
        return f"http://127.0.0.1:{self.stream_port}/stream/{sid}"

    def _external_stream_url(self, sid: str) -> str:
        return f"{self._public_base()}/network_audio/stream/{sid}"

    async def play_and_cast(self, did: str, url: str) -> dict:
        self.ensure_started()
        info = self._strategy.classify(url)
        out = self.play_service.play_url(info.normalized_url)
        if not out.get("ok"):
            return out

        sid = out["session"]["sid"]
        public_stream_url = self._external_stream_url(sid)
        self.session_manager.set_stream_url(sid, public_stream_url)
        out["session"]["stream_url"] = public_stream_url
        out["url_info"] = asdict(info)

        cast_ret = await self.xiaomusic.play_url(did=did, arg1=public_stream_url)
        out["cast_ret"] = cast_ret
        return out

    async def play_link(self, did: str, url: str, prefer_proxy: bool = False) -> dict:
        info = self._strategy.classify(url)

        if prefer_proxy:
            proxy_url = self._strategy.build_proxy_url(url)
            cast_ret = await self.xiaomusic.play_url(did=did, arg1=proxy_url)
            return {
                "ok": True,
                "mode": "proxy",
                "url_info": asdict(info),
                "cast_ret": cast_ret,
                "stream_url": proxy_url,
            }

        if self._strategy.should_use_network_audio(url):
            out = await self.play_and_cast(did=did, url=info.normalized_url)
            out["mode"] = "network_audio"
            return out

        cast_ret = await self.xiaomusic.play_url(did=did, arg1=url)
        return {
            "ok": True,
            "mode": "direct",
            "url_info": asdict(info),
            "cast_ret": cast_ret,
            "stream_url": url,
        }

    def healthz(self) -> dict:
        return {
            "status": "ok",
            "uptime_seconds": int(time.monotonic() - self._started_at),
            "stream_port": self.stream_port,
            "session_count": len(self.session_manager.list_sessions()),
            "last_cleanup_at": self._last_cleanup_at,
        }

    def sessions(self) -> dict:
        sessions = [asdict(s) for s in self.session_manager.list_sessions()]
        return {
            "sessions": sessions,
            "session_count": len(sessions),
            "last_cleanup_at": self._last_cleanup_at,
        }

    def cleanup_sessions(self, max_sessions: int = 100, ttl_seconds: int | None = None) -> dict:
        ret = self.session_manager.cleanup(max_sessions=max_sessions, ttl_seconds=ttl_seconds)
        self._last_cleanup_at = int(time.time())
        return ret

    def stream_chunks(self, sid: str):
        self.ensure_started()
        if self.session_manager.get_session(sid) is None:
            raise KeyError(ERROR_CODES["E_STREAM_NOT_FOUND"])

        stream_url = self._internal_stream_url(sid)
        with urlopen(stream_url, timeout=60) as resp:  # noqa: S310
            while True:
                try:
                    chunk = resp.read(8192)
                except TimeoutError:
                    if self.session_manager.get_session(sid) is None:
                        break
                    continue
                if not chunk:
                    break
                yield chunk

    def stop_session(self, sid: str) -> dict:
        self.audio_streamer.stop_stream(sid)
        sess = self.session_manager.get_session(sid)
        return {"ret": "OK", "session": asdict(sess) if sess else None}
