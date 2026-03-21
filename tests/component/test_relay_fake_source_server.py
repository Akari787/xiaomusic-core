import http.client

import pytest


@pytest.mark.component
def test_ct2_0_fake_source_available_for_stream_read():
    from xiaomusic.relay.fake_source_server import FakeSourceServer  # noqa: PLC0415

    server = FakeSourceServer()
    server.start()
    try:
        conn = http.client.HTTPConnection(server.host, server.port, timeout=3)
        conn.request("GET", "/fake/live")
        resp = conn.getresponse()
        assert resp.status == 200
        chunk = resp.read(4096)
        assert len(chunk) > 0
    finally:
        try:
            conn.close()
        except Exception:
            pass
        server.stop()
