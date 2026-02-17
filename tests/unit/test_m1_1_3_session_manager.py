import pytest


@pytest.mark.unit
def test_session_manager_create_generates_unique_sid_and_initial_state():
    from xiaomusic.m1.session_manager import StreamSessionManager  # noqa: PLC0415

    mgr = StreamSessionManager()

    s1 = mgr.create_session(input_url="https://www.youtube.com/watch?v=iPnaF8Ngk3Q")
    s2 = mgr.create_session(input_url="https://www.bilibili.com/video/BV14EcazWEna")

    assert s1.sid != s2.sid
    assert s1.state == "creating"
    assert s2.state == "creating"


@pytest.mark.unit
def test_session_manager_stop_is_idempotent():
    from xiaomusic.m1.session_manager import StreamSessionManager  # noqa: PLC0415

    mgr = StreamSessionManager()
    session = mgr.create_session(input_url="https://www.youtube.com/watch?v=iPnaF8Ngk3Q")

    first = mgr.stop_session(session.sid)
    second = mgr.stop_session(session.sid)

    assert first is not None
    assert second is not None
    assert first.state == "stopped"
    assert second.state == "stopped"


@pytest.mark.unit
def test_session_manager_stop_unknown_sid_returns_none():
    from xiaomusic.m1.session_manager import StreamSessionManager  # noqa: PLC0415

    mgr = StreamSessionManager()
    assert mgr.stop_session("missing") is None
