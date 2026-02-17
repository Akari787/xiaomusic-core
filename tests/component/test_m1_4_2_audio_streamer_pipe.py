import http.client
import time

import pytest


@pytest.mark.component
def test_ct2_1_audio_streamer_start_stop_and_pipe():
    from xiaomusic.m1.audio_streamer import AudioStreamer  # noqa: PLC0415
    from xiaomusic.m1.fake_source_server import FakeSourceServer  # noqa: PLC0415
    from xiaomusic.m1.local_http_stream_server import LocalHttpStreamServer  # noqa: PLC0415
    from xiaomusic.m1.reconnect_policy import ReconnectPolicy  # noqa: PLC0415
    from xiaomusic.m1.session_manager import StreamSessionManager  # noqa: PLC0415

    sessions = StreamSessionManager()
    local = LocalHttpStreamServer(session_manager=sessions)
    local.start()

    fake = FakeSourceServer()
    fake.start()

    session = sessions.create_session(input_url="https://fake/vod")
    streamer = AudioStreamer(
        session_manager=sessions,
        stream_server=local,
        reconnect_policy=ReconnectPolicy(base_delay_seconds=1, max_delay_seconds=2, max_retries=1),
    )

    try:
        ok = streamer.start_stream(session.sid, fake.url("/fake/live"))
        assert ok is True

        conn = http.client.HTTPConnection(local.host, local.port, timeout=3)
        conn.request("GET", f"/stream/{session.sid}")
        resp = conn.getresponse()
        assert resp.status == 200
        data = resp.read(8192)
        assert b"FAKE_AUDIO_FRAME" in data

        streamer.stop_stream(session.sid)
        time.sleep(0.2)
        assert streamer.is_running(session.sid) is False
    finally:
        try:
            conn.close()
        except Exception:
            pass
        streamer.stop_all()
        fake.stop()
        local.stop()
