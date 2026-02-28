"""Audio streamer that bridges source URL to local stream endpoint."""

from __future__ import annotations

import threading
import time
import subprocess
from urllib.request import urlopen

from xiaomusic.network_audio.local_http_stream_server import LocalHttpStreamServer
from xiaomusic.network_audio.reconnect_policy import ReconnectPolicy
from xiaomusic.network_audio.session_manager import StreamSessionManager


class AudioStreamer:
    def __init__(
        self,
        session_manager: StreamSessionManager,
        stream_server: LocalHttpStreamServer,
        reconnect_policy: ReconnectPolicy,
        source_read_timeout_seconds: int = 15,
        relay_mode: str = "http",
    ) -> None:
        self.session_manager = session_manager
        self.stream_server = stream_server
        self.reconnect_policy = reconnect_policy
        self.source_read_timeout_seconds = source_read_timeout_seconds
        self.relay_mode = relay_mode
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
            if failed and not stop_flag.is_set():
                self.session_manager.update_state(sid, "failed", error_code="E_STREAM_START_FAILED")
            else:
                self.session_manager.stop_session(sid)

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
                    if not self.stream_server.push_bytes(sid, data):
                        time.sleep(0.01)
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
                if not self.stream_server.push_bytes(sid, data):
                    time.sleep(0.01)
            return False
        except Exception:
            return True
        finally:
            self._terminate_process(proc)
