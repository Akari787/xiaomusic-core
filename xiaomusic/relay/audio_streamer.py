"""Audio streamer that bridges source URL to local stream endpoint."""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections.abc import Callable
from urllib.request import urlopen

from xiaomusic.relay.local_http_stream_server import LocalHttpStreamServer
from xiaomusic.relay.reconnect_policy import ReconnectPolicy
from xiaomusic.relay.session_manager import StreamSessionManager

LOG = logging.getLogger("xiaomusic.relay")

# A push that waits longer than this means the speaker stopped reading for a
# while; before 2026-11-23 that audio was dropped (heard as a jump ahead).
BACKPRESSURE_REPORT_WAIT_SEC = 1.0
BACKPRESSURE_LOG_INTERVAL_SEC = 60.0


class AudioStreamer:
    def __init__(
        self,
        session_manager: StreamSessionManager,
        stream_server: LocalHttpStreamServer,
        reconnect_policy: ReconnectPolicy,
        source_read_timeout_seconds: int = 15,
        relay_mode: str = "http",
        on_stream_failed: Callable[[str, str], None] | None = None,
    ) -> None:
        self.session_manager = session_manager
        self.stream_server = stream_server
        self.reconnect_policy = reconnect_policy
        self.source_read_timeout_seconds = source_read_timeout_seconds
        self.relay_mode = relay_mode
        self.on_stream_failed = on_stream_failed
        self._backpressure: dict[str, dict] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._stop_flags: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def start_stream(self, sid: str, source_url: str) -> bool:
        if self.session_manager.get_session(sid) is None:
            return False
        with self._lock:
            if sid in self._threads and self._threads[sid].is_alive():
                return False

            stop_flag = threading.Event()
            self._stop_flags[sid] = stop_flag
            self.stream_server.open_stream_channel(sid)
            self.session_manager.set_source_url(sid, source_url)
            self.session_manager.set_stream_url(
                sid,
                f"http://{self.stream_server.host}:{self.stream_server.port}/stream/{sid}",
            )
            self.session_manager.set_state(sid, "running")

            thread = threading.Thread(
                target=self._run_pump,
                args=(sid, source_url, stop_flag),
                daemon=True,
            )
            self._threads[sid] = thread
            thread.start()
            return True

    def stop_stream(self, sid: str) -> None:
        with self._lock:
            stop_flag = self._stop_flags.get(sid)
            thread = self._threads.get(sid)
        if stop_flag is not None:
            stop_flag.set()
        self.stream_server.close_stream_channel(sid)
        if thread is not None:
            thread.join(timeout=2)
        self.session_manager.stop_session(sid)

    def stop_all(self) -> None:
        with self._lock:
            sids = list(self._threads.keys())
        for sid in sids:
            self.stop_stream(sid)

    def is_running(self, sid: str) -> bool:
        with self._lock:
            thread = self._threads.get(sid)
            return bool(thread and thread.is_alive())

    def _run_pump(self, sid: str, source_url: str, stop_flag: threading.Event) -> None:
        reconnect_attempt = 0
        failed = False
        try:
            while not stop_flag.is_set():
                if reconnect_attempt > 0:
                    self.session_manager.update_state(sid, "streaming")
                disconnected = False
                if self.relay_mode == "ffmpeg":
                    disconnected = self._pump_with_ffmpeg(sid, source_url, stop_flag)
                else:
                    disconnected = self._pump_with_http(sid, source_url, stop_flag)

                if stop_flag.is_set():
                    break
                if not disconnected:
                    break

                reconnect_attempt += 1
                delay = self.reconnect_policy.delay_for_attempt(reconnect_attempt)
                if delay is None:
                    failed = True
                    break
                self.session_manager.increment_reconnect(sid)
                self.session_manager.update_state(sid, "reconnecting")
                time.sleep(delay)
        except Exception:
            failed = True
            raise
        finally:
            self.stream_server.close_stream_channel(sid)
            stats = self._backpressure.pop(sid, None)
            if stats:
                LOG.info(
                    "relay_backpressure_total sid=%s waits=%d waited_s=%.1f",
                    sid,
                    stats["waits"],
                    stats["waited"],
                )
            if failed and not stop_flag.is_set():
                self.session_manager.update_state(sid, "failed", error_code="E_STREAM_START_FAILED")
                if self.on_stream_failed is not None:
                    try:
                        self.on_stream_failed(sid, "E_STREAM_START_FAILED")
                    except Exception:
                        pass
            else:
                self.session_manager.stop_session(sid)

    def _push(self, sid: str, data: bytes, stop_flag: threading.Event) -> bool:
        """Hand ``data`` to the stream channel without ever dropping it."""
        started = time.monotonic()
        ok = self.stream_server.push_bytes_wait(sid, data, stop_flag)
        waited = time.monotonic() - started
        if waited >= BACKPRESSURE_REPORT_WAIT_SEC:
            stats = self._backpressure.setdefault(
                sid, {"waits": 0, "waited": 0.0, "logged_at": 0.0}
            )
            stats["waits"] += 1
            stats["waited"] += waited
            now = time.monotonic()
            if now - stats["logged_at"] >= BACKPRESSURE_LOG_INTERVAL_SEC:
                LOG.info(
                    "relay_backpressure sid=%s waits=%d waited_s=%.1f last_wait_s=%.1f",
                    sid,
                    stats["waits"],
                    stats["waited"],
                    waited,
                )
                stats["logged_at"] = now
        return ok

    @staticmethod
    def _terminate_process(proc: subprocess.Popen) -> None:
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=1)
            return
        except Exception:
            pass
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.communicate(timeout=1)
        except Exception:
            pass

    def _pump_with_http(self, sid: str, source_url: str, stop_flag: threading.Event) -> bool:
        try:
            with urlopen(source_url, timeout=self.source_read_timeout_seconds) as resp:  # noqa: S310
                while not stop_flag.is_set():
                    data = resp.read(4096)
                    if not data:
                        return True
                    if not self._push(sid, data, stop_flag):
                        return False
            return False
        except Exception:
            return True

    def _pump_with_ffmpeg(self, sid: str, source_url: str, stop_flag: threading.Event) -> bool:
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-reconnect",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_delay_max",
            "2",
            "-i",
            source_url,
            "-vn",
            "-f",
            "mp3",
            "pipe:1",
        ]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            while not stop_flag.is_set():
                if proc.stdout is None:
                    return True
                data = proc.stdout.read(4096)
                if not data:
                    return True
                if not self._push(sid, data, stop_flag):
                    return False
            return False
        except Exception:
            return True
        finally:
            self._terminate_process(proc)
