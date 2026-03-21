import pytest


@pytest.mark.component
def test_ct4_1_healthz_and_sessions():
    from fastapi.testclient import TestClient  # noqa: PLC0415

    from xiaomusic.relay.api import build_relay_app  # noqa: PLC0415
    from xiaomusic.relay.audio_streamer import AudioStreamer  # noqa: PLC0415
    from xiaomusic.relay.fake_source_server import FakeSourceServer  # noqa: PLC0415
    from xiaomusic.relay.local_http_stream_server import LocalHttpStreamServer  # noqa: PLC0415
    from xiaomusic.relay.play_service import RelayPlayService  # noqa: PLC0415
    from xiaomusic.relay.reconnect_policy import ReconnectPolicy  # noqa: PLC0415
    from xiaomusic.relay.session_manager import StreamSessionManager  # noqa: PLC0415

    class _MockResolver:
        def __init__(self, source_url):
            self._source_url = source_url

        def resolve(self, url, timeout_seconds=8):  # noqa: ARG002
            from xiaomusic.relay.contracts import ResolveResult  # noqa: PLC0415

            return ResolveResult(
                ok=True,
                source_url=self._source_url,
                title="mock",
                is_live=True,
                container_hint="mpeg",
                error_code=None,
                error_message=None,
                meta={},
            )

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
    service = RelayPlayService(
        session_manager=sessions,
        resolver=_MockResolver(fake.url("/fake/live")),
        audio_streamer=streamer,
    )
    app = build_relay_app(play_service=service, session_manager=sessions)
    client = TestClient(app)

    try:
        r1 = client.get("/healthz")
        assert r1.status_code == 200
        payload = r1.json()
        assert payload["status"] == "ok"
        assert payload["uptime_seconds"] >= 0

        play = service.play_url("https://www.youtube.com/watch?v=iPnaF8Ngk3Q")
        assert play["ok"] is True

        r2 = client.get("/sessions")
        assert r2.status_code == 200
        rows = r2.json()["sessions"]
        assert len(rows) >= 1
        assert "state" in rows[0]
        assert "reconnect_count" in rows[0]
    finally:
        streamer.stop_all()
        fake.stop()
        local.stop()
