"""Local HTTP stream endpoint for network audio sessions."""

from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from queue import Empty, Queue
from urllib.parse import urlparse

from xiaomusic.network_audio.session_manager import StreamSessionManager


class LocalHttpStreamServer:
    def __init__(
        self,
        session_manager: StreamSessionManager,
        host: str = "127.0.0.1",
        port: int = 0,
        max_clients_per_sid: int = 1,
    ) -> None:
        self.session_manager = session_manager
        self.host = host
        self.port = port
        self.max_clients_per_sid = max_clients_per_sid
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._active_by_sid: dict[str, int] = {}
        self._channels: dict[str, Queue[bytes | None]] = {}
        self._closed_sids: set[str] = set()
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._server is not None:
            return

        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                parts = parsed.path.strip("/").split("/")
                if len(parts) != 2 or parts[0] != "stream":
                    self.send_response(404)
                    self.end_headers()
                    return

                sid = parts[1]
                session = outer.session_manager.get_session(sid)
                if session is None:
                    self.send_response(404)
                    self.end_headers()
                    return

                with outer._lock:
                    active = outer._active_by_sid.get(sid, 0)
                    if active >= outer.max_clients_per_sid:
                        self.send_response(409)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(b'{"error":"E_STREAM_SINGLE_CLIENT_ONLY"}')
                        return
                    outer._active_by_sid[sid] = active + 1

                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "audio/mpeg")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()

                    queue = outer._get_channel(sid)
                    if queue is None:
                        chunk = (b"M1_PLACEHOLDER_AUDIO" * 256)[:4096]
                        while True:
                            self.wfile.write(chunk)
                            self.wfile.flush()
                            time.sleep(0.02)
                    else:
                        while True:
                            if outer._is_sid_closed(sid):
                                break
                            try:
                                data = queue.get(timeout=0.2)
                            except Empty:
                                continue
                            if data is None:
                                break
                            self.wfile.write(data)
                            self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return
                finally:
                    with outer._lock:
                        current = outer._active_by_sid.get(sid, 0)
                        if current <= 1:
                            outer._active_by_sid.pop(sid, None)
                        else:
                            outer._active_by_sid[sid] = current - 1

            def log_message(self, format: str, *args) -> None:  # noqa: A003
                return

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        addr = self._server.server_address
        self.host = str(addr[0])
        self.port = int(addr[1])
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def open_stream_channel(self, sid: str) -> None:
        with self._lock:
            self._channels[sid] = Queue(maxsize=64)
            self._closed_sids.discard(sid)

    def push_bytes(self, sid: str, data: bytes) -> bool:
        with self._lock:
            queue = self._channels.get(sid)
            if queue is None or sid in self._closed_sids:
                return False
        try:
            queue.put_nowait(data)
            return True
        except Exception:
            return False

    def close_stream_channel(self, sid: str) -> None:
        with self._lock:
            queue = self._channels.get(sid)
            self._closed_sids.add(sid)
        if queue is not None:
            try:
                queue.put_nowait(None)
            except Exception:
                pass

    def _get_channel(self, sid: str) -> Queue[bytes | None] | None:
        with self._lock:
            return self._channels.get(sid)

    def _is_sid_closed(self, sid: str) -> bool:
        with self._lock:
            return sid in self._closed_sids

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=1)
        self._thread = None
        self._server = None
