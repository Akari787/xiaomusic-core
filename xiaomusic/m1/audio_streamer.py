"""Audio streamer that bridges source URL to local stream endpoint."""

from __future__ import annotations

import threading
import time
from urllib.request import urlopen

from xiaomusic.m1.local_http_stream_server import LocalHttpStreamServer
from xiaomusic.m1.reconnect_policy import ReconnectPolicy
from xiaomusic.m1.session_manager import StreamSessionManager


class AudioStreamer:
    def __init__(
        self,
        session_manager: StreamSessionManager,
        stream_server: LocalHttpStreamServer,
        reconnect_policy: ReconnectPolicy,
    ) -> None:
        self.session_manager = session_manager
        self.stream_server = stream_server
        self.reconnect_policy = reconnect_policy
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
        try:
            while not stop_flag.is_set():
                disconnected = False
                try:
                    with urlopen(source_url, timeout=3) as resp:  # noqa: S310
                        while not stop_flag.is_set():
                            data = resp.read(4096)
                            if not data:
                                disconnected = True
                                break
                            if not self.stream_server.push_bytes(sid, data):
                                time.sleep(0.01)
                except Exception:
                    disconnected = True

                if stop_flag.is_set():
                    break
                if not disconnected:
                    break

                reconnect_attempt += 1
                delay = self.reconnect_policy.delay_for_attempt(reconnect_attempt)
                if delay is None:
                    break
                self.session_manager.increment_reconnect(sid)
                time.sleep(delay)
        finally:
            self.stream_server.close_stream_channel(sid)
            self.session_manager.stop_session(sid)
