"""Relay must not lose audio when the speaker reads slower than the source.

The relay source (ffmpeg on a YouTube stream) runs about 2x realtime
(measured 2026-11-23: 32 KB/s of 128 kbps MP3). When the speaker pauses
reading, the relay queue (64 x 4 KB) fills; the pump used to drop every
chunk it could not queue, which is heard as the stream jumping ahead by
seconds to a minute.
"""

from __future__ import annotations

import http.client
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

PAYLOAD = bytes((i * 7 + i // 251) % 256 for i in range(2 * 1024 * 1024))


class _FastSource(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Content-Length", str(len(PAYLOAD)))
        self.end_headers()
        self.wfile.write(PAYLOAD)

    def log_message(self, *args):
        pass


@pytest.mark.component
def test_slow_reader_receives_every_byte_in_order():
    from xiaomusic.relay.audio_streamer import AudioStreamer  # noqa: PLC0415
    from xiaomusic.relay.local_http_stream_server import LocalHttpStreamServer  # noqa: PLC0415
    from xiaomusic.relay.reconnect_policy import ReconnectPolicy  # noqa: PLC0415
    from xiaomusic.relay.session_manager import StreamSessionManager  # noqa: PLC0415

    source = ThreadingHTTPServer(("127.0.0.1", 0), _FastSource)
    threading.Thread(target=source.serve_forever, daemon=True).start()
    sessions = StreamSessionManager()
    local = LocalHttpStreamServer(session_manager=sessions)
    local.start()
    session = sessions.create_session(input_url="https://fake/long-bgm")
    streamer = AudioStreamer(
        session_manager=sessions,
        stream_server=local,
        reconnect_policy=ReconnectPolicy(base_delay_seconds=1, max_delay_seconds=1, max_retries=0),
    )
    conn = None
    try:
        assert streamer.start_stream(session.sid, f"http://127.0.0.1:{source.server_address[1]}/a.mp3")
        conn = http.client.HTTPConnection(local.host, local.port, timeout=10)
        conn.request("GET", f"/stream/{session.sid}")
        resp = conn.getresponse()
        assert resp.status == 200
        time.sleep(1.5)  # the speaker's buffer is full: it stops reading
        received = resp.read()  # until the source ends and the channel closes
        assert len(received) == len(PAYLOAD)
        assert received == PAYLOAD
    finally:
        if conn is not None:
            conn.close()
        streamer.stop_all()
        local.stop()
        source.shutdown()
