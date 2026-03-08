"""Runtime holder for network audio pipeline inside main app."""

from __future__ import annotations

import os
import time
from dataclasses import asdict
from threading import Lock
from urllib.request import urlopen

from xiaomusic.network_audio.audio_streamer import AudioStreamer
from xiaomusic.network_audio.contracts import ERROR_CODES
from xiaomusic.network_audio.local_http_stream_server import LocalHttpStreamServer
from xiaomusic.network_audio.play_service import NetworkAudioPlayService
from xiaomusic.network_audio.reconnect_policy import ReconnectPolicy
from xiaomusic.network_audio.resolver import Resolver
from xiaomusic.network_audio.resolver_cache import ResolverCache
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
        self.max_active_sessions = int(os.getenv("XIAOMUSIC_NETWORK_AUDIO_MAX_ACTIVE_SESSIONS", "3"))
        self.idle_timeout_seconds = int(os.getenv("XIAOMUSIC_NETWORK_AUDIO_IDLE_TIMEOUT_SECONDS", "120"))
        self.resolve_timeout_seconds = int(os.getenv("XIAOMUSIC_NETWORK_AUDIO_RESOLVE_TIMEOUT_SECONDS", "15"))
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
            on_stream_failed=self._on_stream_failed,
        )
        self.resolver = Resolver()
        self.resolver_cache = ResolverCache(
            live_ttl_seconds=int(os.getenv("XIAOMUSIC_RESOLVER_CACHE_LIVE_TTL_SECONDS", "30")),
            vod_ttl_seconds=int(os.getenv("XIAOMUSIC_RESOLVER_CACHE_VOD_TTL_SECONDS", "300")),
        )
        self.play_service = NetworkAudioPlayService(
            session_manager=self.session_manager,
            resolver=self.resolver,
            audio_streamer=self.audio_streamer,
            resolver_cache=self.resolver_cache,
        )

    def ensure_started(self) -> None:
        with self._lock:
            self.stream_server.start()

    def _public_base(self) -> str:
        return self.xiaomusic.config.get_public_base_url()

    def _internal_stream_url(self, sid: str) -> str:
        return f"http://127.0.0.1:{self.stream_port}/stream/{sid}"

    def _external_stream_url(self, sid: str) -> str:
        return f"{self._public_base()}/network_audio/stream/{sid}"

    async def play_and_cast(self, did: str, url: str, *, no_cache: bool = False) -> dict:
        self.sweep_idle_sessions()
        active_limit = self._active_session_limit()
        safety = 0
        while self.session_manager.count_active() >= active_limit and safety < 8:
            if not self._stop_oldest_active_session():
                break
            safety += 1
        if self.session_manager.count_active() >= active_limit:
            return {
                "ok": False,
                "error_code": "E_TOO_MANY_SESSIONS",
                "error_message": ERROR_CODES["E_TOO_MANY_SESSIONS"],
                "fail_stage": "stream",
            }
        self.ensure_started()
        strategy = self._strategy
        if strategy is None:
            return {
                "ok": False,
                "error_code": "E_INTERNAL",
                "error_message": ERROR_CODES["E_INTERNAL"],
                "fail_stage": "stream",
            }
        assert strategy is not None
        info = strategy.classify(url)
        out = self.play_service.play_url(info.normalized_url, no_cache=no_cache)
        if not out.get("ok"):
            self._invalidate_cache_on_stream_failure(info.normalized_url, out.get("error_code") or "")
            return out

        sid = out["session"]["sid"]
        public_stream_url = self._external_stream_url(sid)
        self.session_manager.set_stream_url(sid, public_stream_url)
        out["session"]["stream_url"] = public_stream_url
        out["url_info"] = asdict(info)

        cast_ret = await self.xiaomusic.play_url(did=did, arg1=public_stream_url)
        out["cast_ret"] = cast_ret
        return out

    async def play_link(
        self,
        did: str,
        url: str,
        prefer_proxy: bool = False,
        *,
        no_cache: bool = False,
    ) -> dict:
        strategy = self._strategy
        if strategy is None:
            return {
                "ok": False,
                "error_code": "E_INTERNAL",
                "error_message": ERROR_CODES["E_INTERNAL"],
                "fail_stage": "stream",
            }
        assert strategy is not None
        info = strategy.classify(url)

        if prefer_proxy:
            proxy_url = strategy.build_proxy_url(url)
            cast_ret = await self.xiaomusic.play_url(did=did, arg1=proxy_url)
            return {
                "ok": True,
                "mode": "proxy",
                "url_info": asdict(info),
                "cast_ret": cast_ret,
                "stream_url": proxy_url,
            }

        if strategy.should_use_network_audio(url):
            out = await self.play_and_cast(did=did, url=info.normalized_url, no_cache=no_cache)
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

    def prepare_link(
        self,
        url: str,
        prefer_proxy: bool = False,
        *,
        no_cache: bool = False,
    ) -> dict:
        strategy = self._strategy
        if strategy is None:
            return {
                "ok": False,
                "error_code": "E_INTERNAL",
                "error_message": ERROR_CODES["E_INTERNAL"],
                "fail_stage": "stream",
            }
        assert strategy is not None
        info = strategy.classify(url)

        if prefer_proxy:
            proxy_url = strategy.build_proxy_url(url)
            return {
                "ok": True,
                "mode": "proxy",
                "url_info": asdict(info),
                "stream_url": proxy_url,
            }

        if strategy.should_use_network_audio(url):
            self.sweep_idle_sessions()
            self.ensure_started()
            out = self.play_service.play_url(info.normalized_url, no_cache=no_cache)
            out["mode"] = "network_audio"
            out["url_info"] = asdict(info)
            if not out.get("ok"):
                return out
            sid = str((out.get("session") or {}).get("sid") or "")
            public_stream_url = self._external_stream_url(sid)
            self.session_manager.set_stream_url(sid, public_stream_url)
            if isinstance(out.get("session"), dict):
                out["session"]["stream_url"] = public_stream_url
            out["stream_url"] = public_stream_url
            return out

        return {
            "ok": True,
            "mode": "direct",
            "url_info": asdict(info),
            "stream_url": url,
        }

    def healthz(self) -> dict:
        self.sweep_idle_sessions()
        return {
            "status": "ok",
            "uptime_seconds": int(time.monotonic() - self._started_at),
            "stream_port": self.stream_port,
            "active_sessions": self.session_manager.count_active(),
            "session_count": len(self.session_manager.list_sessions()),
            "cache_stats": self.resolver_cache.stats(),
            "last_cleanup_at": self._last_cleanup_at,
        }

    def sessions(self) -> dict:
        self.sweep_idle_sessions()
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

        def _iter_chunks():
            self.session_manager.touch_client(sid)
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
                    self.session_manager.touch_client(sid)
                    yield chunk

        return _iter_chunks()

    def stop_session(self, sid: str) -> dict:
        self.audio_streamer.stop_stream(sid)
        sess = self.session_manager.get_session(sid)
        return {"ret": "OK", "session": asdict(sess) if sess else None}

    def sweep_idle_sessions(self, now_ts: int | None = None) -> dict[str, int]:
        timeout = int(self.idle_timeout_seconds or 0)
        resolve_timeout = int(self.resolve_timeout_seconds or 0)
        if timeout <= 0:
            timeout = 0

        now = int(now_ts if now_ts is not None else time.time())
        active_states = {"creating", "resolving", "streaming", "reconnecting"}
        stopped = 0
        resolve_timeouts = 0
        for sess in self.session_manager.list_sessions():
            if (sess.state or "").lower() not in active_states:
                continue
            if (sess.state or "").lower() == "resolving" and resolve_timeout > 0:
                last = int(sess.last_transition_at or 0)
                if last > 0 and now - last > resolve_timeout:
                    changed = self.session_manager.update_state(
                        sess.sid,
                        "failed",
                        error_code="E_RESOLVE_TIMEOUT",
                        now_ts=now,
                    )
                    if changed is not None:
                        resolve_timeouts += 1
                    continue
            if timeout <= 0:
                continue
            last_client_at = int(sess.last_client_at or 0)
            if last_client_at <= 0:
                continue
            if now - last_client_at < timeout:
                continue
            self.audio_streamer.stop_stream(sess.sid)
            stopped += 1
        return {"stopped": stopped, "resolve_timeouts": resolve_timeouts}

    @staticmethod
    def _is_stream_level_error(error_code: str | None) -> bool:
        ec = str(error_code or "")
        if not ec:
            return False
        if ec in {"E_TOO_MANY_SESSIONS", "E_INTERNAL"}:
            return False
        if ec.startswith("E_STREAM") or ec.startswith("E_FFMPEG"):
            return True
        return False

    def _invalidate_cache_on_stream_failure(self, normalized_url: str, error_code: str | None) -> None:
        if not self._is_stream_level_error(error_code):
            return
        self.resolver_cache.invalidate(normalized_url)

    def _on_stream_failed(self, sid: str, error_code: str) -> None:
        sess = self.session_manager.get_session(sid)
        if sess is None:
            return
        key = str(sess.input_url or "")
        if not key:
            return
        self._invalidate_cache_on_stream_failure(key, error_code)

    def _active_session_limit(self) -> int:
        """Determine active session limit from selected devices first."""
        mi_did = str(getattr(self.xiaomusic.config, "mi_did", "") or "")
        selected = [x.strip() for x in mi_did.split(",") if x.strip()]
        if selected:
            return max(1, len(selected))
        return max(1, int(self.max_active_sessions or 3))

    def _stop_oldest_active_session(self) -> bool:
        active_states = {"creating", "resolving", "streaming", "reconnecting"}
        candidates = [
            s
            for s in self.session_manager.list_sessions()
            if (s.state or "").lower() in active_states
        ]
        if not candidates:
            return False

        oldest = min(
            candidates,
            key=lambda s: (
                int(s.started_at or s.last_transition_at or 0),
                str(s.created_at or ""),
            ),
        )
        self.audio_streamer.stop_stream(oldest.sid)
        return True
