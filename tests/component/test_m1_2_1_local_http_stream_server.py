import http.client

import pytest


@pytest.mark.component
def test_ct3_stream_endpoint_reads_placeholder_bytes():
    from xiaomusic.network_audio.local_http_stream_server import LocalHttpStreamServer  # noqa: PLC0415
    from xiaomusic.network_audio.session_manager import StreamSessionManager  # noqa: PLC0415

    sessions = StreamSessionManager()
    session = sessions.create_session(input_url="https://www.youtube.com/watch?v=iPnaF8Ngk3Q")

    server = LocalHttpStreamServer(session_manager=sessions)
    server.start()

    try:
        conn = http.client.HTTPConnection(server.host, server.port, timeout=3)
        conn.request("GET", f"/stream/{session.sid}")
        resp = conn.getresponse()
        assert resp.status == 200
        chunk = resp.read(8192)
        assert len(chunk) >= 1024
    finally:
        try:
            conn.close()
        except Exception:
            pass
        server.stop()


@pytest.mark.component
def test_ct3_stream_endpoint_returns_404_for_unknown_sid():
    from xiaomusic.network_audio.local_http_stream_server import LocalHttpStreamServer  # noqa: PLC0415
    from xiaomusic.network_audio.session_manager import StreamSessionManager  # noqa: PLC0415

    sessions = StreamSessionManager()
    server = LocalHttpStreamServer(session_manager=sessions)
    server.start()

    try:
        conn = http.client.HTTPConnection(server.host, server.port, timeout=3)
        conn.request("GET", "/stream/missing")
        resp = conn.getresponse()
        assert resp.status == 404
    finally:
        try:
            conn.close()
        except Exception:
            pass
        server.stop()
