import http.client

import pytest


class _MockResolver:
    def __init__(self, source_url):
        self.source_url = source_url

    def resolve(self, url, timeout_seconds=8):  # noqa: ARG002
        from xiaomusic.m1.contracts import ResolveResult  # noqa: PLC0415

        return ResolveResult(
            ok=True,
            source_url=self.source_url,
            title="mocked",
            is_live=True,
            container_hint="mpeg",
            error_code=None,
            error_message=None,
            meta={"mock": True},
        )


@pytest.mark.component
def test_ct4_0_play_url_with_mock_resolver():
    from xiaomusic.m1.audio_streamer import AudioStreamer  # noqa: PLC0415
    from xiaomusic.m1.fake_source_server import FakeSourceServer  # noqa: PLC0415
    from xiaomusic.m1.local_http_stream_server import LocalHttpStreamServer  # noqa: PLC0415
    from xiaomusic.m1.play_service import M1PlayService  # noqa: PLC0415
    from xiaomusic.m1.reconnect_policy import ReconnectPolicy  # noqa: PLC0415
    from xiaomusic.m1.session_manager import StreamSessionManager  # noqa: PLC0415

    sessions = StreamSessionManager()
    local = LocalHttpStreamServer(session_manager=sessions)
    local.start()
    fake = FakeSourceServer()
    fake.start()

    streamer = AudioStreamer(
        session_manager=sessions,
        stream_server=local,
        reconnect_policy=ReconnectPolicy(base_delay_seconds=1, max_delay_seconds=1, max_retries=1),
    )
    resolver = _MockResolver(fake.url("/fake/live"))
    service = M1PlayService(
        session_manager=sessions,
        resolver=resolver,
        audio_streamer=streamer,
    )

    conn = None
    try:
        out = service.play_url("https://www.youtube.com/watch?v=iPnaF8Ngk3Q")
        assert out["ok"] is True
        sid = out["session"]["sid"]
        stream_url = out["session"]["stream_url"]
        assert sid
        assert stream_url.endswith(f"/stream/{sid}")

        conn = http.client.HTTPConnection(local.host, local.port, timeout=3)
        conn.request("GET", f"/stream/{sid}")
        resp = conn.getresponse()
        assert resp.status == 200
        data = resp.read(4096)
        assert len(data) > 0
    finally:
        if conn is not None:
            conn.close()
        streamer.stop_all()
        fake.stop()
        local.stop()
