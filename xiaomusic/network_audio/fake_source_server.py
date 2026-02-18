"""Local fake media source for network audio component tests."""

from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


class FakeSourceServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.host = host
        self.port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._request_count: dict[str, int] = {}
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._server is not None:
            return

        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                path = urlparse(self.path).path
                if path not in {"/fake/live", "/fake/vod", "/fake/flaky"}:
                    self.send_response(404)
                    self.end_headers()
                    return

                with outer._lock:
                    count = outer._request_count.get(path, 0) + 1
                    outer._request_count[path] = count

                self.send_response(200)
                self.send_header("Content-Type", "audio/mpeg")
                self.end_headers()

                chunk = (b"FAKE_AUDIO_FRAME" * 300)[:4096]
                if path == "/fake/live":
                    loops = 1000
                elif path == "/fake/vod":
                    loops = 10
                elif count == 1:
                    loops = 1
                else:
                    loops = 1000
                for _ in range(loops):
                    try:
                        self.wfile.write(chunk)
                        self.wfile.flush()
                        if path in {"/fake/live", "/fake/flaky"}:
                            time.sleep(0.02)
                    except (BrokenPipeError, ConnectionResetError):
                        break

            def log_message(self, format: str, *args) -> None:  # noqa: A003
                return

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        addr = self._server.server_address
        self.host = str(addr[0])
        self.port = int(addr[1])
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=1)
        self._thread = None
        self._server = None

    def url(self, path: str) -> str:
        return f"http://{self.host}:{self.port}{path}"

    def request_count(self, path: str) -> int:
        with self._lock:
            return self._request_count.get(path, 0)
