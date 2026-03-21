"""Relay play orchestration: classify -> resolve -> start stream."""

from __future__ import annotations

from dataclasses import asdict
import time

from xiaomusic.relay.audio_streamer import AudioStreamer
from xiaomusic.relay.resolver_cache import ResolverCache, normalize_cache_key
from xiaomusic.relay.session_manager import StreamSessionManager


class RelayPlayService:
    def __init__(
        self,
        session_manager: StreamSessionManager,
        resolver,
        audio_streamer: AudioStreamer,
        resolver_cache: ResolverCache | None = None,
    ) -> None:
        self.session_manager = session_manager
        self.resolver = resolver
        self.audio_streamer = audio_streamer
        self.resolver_cache = resolver_cache or ResolverCache()

    def play_url(self, url: str, *, no_cache: bool = False) -> dict:
        session = self.session_manager.create_session(input_url=url)
        self.session_manager.update_state(session.sid, "resolving")

        key = normalize_cache_key(url)
        cache_hit = False
        start = time.perf_counter()
        resolved = None
        if not no_cache:
            resolved = self.resolver_cache.get(key)
            cache_hit = resolved is not None
        if resolved is None:
            resolved = self.resolver.resolve(url, timeout_seconds=8)
            if not no_cache and resolved.ok and not resolved.error_code:
                self.resolver_cache.set(key, resolved)
        resolve_ms = int((time.perf_counter() - start) * 1000)

        if not resolved.ok:
            self.session_manager.update_state(
                session.sid,
                "failed",
                error_code=resolved.error_code or "E_INTERNAL",
                resolve_ms=resolve_ms,
            )
            return {
                "ok": False,
                "error_code": resolved.error_code,
                "error_message": resolved.error_message,
                "cache_hit": cache_hit,
                "resolve_ms": resolve_ms,
                "fail_stage": "resolve",
                "session": asdict(self.session_manager.get_session(session.sid)),
            }

        stream_start_begin = time.perf_counter()
        started = self.audio_streamer.start_stream(session.sid, resolved.source_url)
        stream_start_ms = int((time.perf_counter() - stream_start_begin) * 1000)
        if not started:
            self.session_manager.update_state(
                session.sid,
                "failed",
                error_code="E_STREAM_START_FAILED",
                resolve_ms=resolve_ms,
                stream_start_ms=stream_start_ms,
            )
            return {
                "ok": False,
                "error_code": "E_STREAM_START_FAILED",
                "error_message": "failed to start stream",
                "cache_hit": cache_hit,
                "resolve_ms": resolve_ms,
                "fail_stage": "ffmpeg",
                "session": asdict(self.session_manager.get_session(session.sid)),
            }

        self.session_manager.update_state(
            session.sid,
            "streaming",
            resolve_ms=resolve_ms,
            stream_start_ms=stream_start_ms,
        )

        current = self.session_manager.get_session(session.sid)
        return {
            "ok": True,
            "error_code": None,
            "error_message": None,
            "cache_hit": cache_hit,
            "resolve_ms": resolve_ms,
            "session": asdict(current),
        }
