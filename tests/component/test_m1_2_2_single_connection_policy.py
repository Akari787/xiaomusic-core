import http.client

import pytest


@pytest.mark.component
def test_ct3_1_single_connection_policy_rejects_second_client():
    from xiaomusic.m1.local_http_stream_server import LocalHttpStreamServer  # noqa: PLC0415
    from xiaomusic.m1.session_manager import StreamSessionManager  # noqa: PLC0415

    sessions = StreamSessionManager()
    session = sessions.create_session(input_url="https://www.youtube.com/watch?v=vNG3-GRjrAo")

    server = LocalHttpStreamServer(session_manager=sessions)
    server.start()

    conn1 = http.client.HTTPConnection(server.host, server.port, timeout=3)
    conn2 = http.client.HTTPConnection(server.host, server.port, timeout=3)
    try:
        conn1.request("GET", f"/stream/{session.sid}")
        resp1 = conn1.getresponse()
        assert resp1.status == 200
        _ = resp1.read(1024)

        conn2.request("GET", f"/stream/{session.sid}")
        resp2 = conn2.getresponse()
        assert resp2.status in (409, 429)

        _ = resp1.read(1024)
    finally:
        conn1.close()
        conn2.close()
        server.stop()
