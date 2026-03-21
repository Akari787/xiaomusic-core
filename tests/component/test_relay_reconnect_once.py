import http.client
import time

import pytest


@pytest.mark.component
def test_ct2_2_audio_streamer_reconnect_once_with_flaky_source():
    from xiaomusic.relay.audio_streamer import AudioStreamer  # noqa: PLC0415
    from xiaomusic.relay.fake_source_server import FakeSourceServer  # noqa: PLC0415
    from xiaomusic.relay.local_http_stream_server import LocalHttpStreamServer  # noqa: PLC0415
    from xiaomusic.relay.reconnect_policy import ReconnectPolicy  # noqa: PLC0415
    from xiaomusic.relay.session_manager import StreamSessionManager  # noqa: PLC0415

    sessions = StreamSessionManager()
    local = LocalHttpStreamServer(session_manager=sessions)
    local.start()

    fake = FakeSourceServer()
    fake.start()

    session = sessions.create_session(input_url="https://fake/flaky")
    streamer = AudioStreamer(
        session_manager=sessions,
        stream_server=local,
        reconnect_policy=ReconnectPolicy(base_delay_seconds=1, max_delay_seconds=1, max_retries=1),
    )

    conn = None
    try:
        ok = streamer.start_stream(session.sid, fake.url("/fake/flaky"))
        assert ok is True

        conn = http.client.HTTPConnection(local.host, local.port, timeout=5)
        conn.request("GET", f"/stream/{session.sid}")
        resp = conn.getresponse()
        assert resp.status == 200
        first = resp.read(4096)
        assert len(first) == 4096

        deadline = time.time() + 5
        reconnected = False
        while time.time() < deadline:
            s = sessions.get_session(session.sid)
            if s and s.reconnect_count >= 1:
                reconnected = True
                break
            time.sleep(0.1)

        assert reconnected is True
        second = resp.read(1024)
        assert len(second) > 0
        assert fake.request_count("/fake/flaky") >= 2
    finally:
        if conn is not None:
            conn.close()
        streamer.stop_all()
        fake.stop()
        local.stop()
