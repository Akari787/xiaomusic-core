from xiaomusic.relay.session_manager import StreamSessionManager


def test_illegal_transition_creating_to_streaming_rejected():
    mgr = StreamSessionManager()
    s = mgr.create_session("https://a")
    ret = mgr.update_state(s.sid, "streaming")
    cur = mgr.get_session(s.sid)
    assert ret is None
    assert cur is not None
    assert cur.state == "creating"


def test_illegal_transition_streaming_to_resolving_rejected():
    mgr = StreamSessionManager()
    s = mgr.create_session("https://a")
    mgr.update_state(s.sid, "resolving")
    mgr.update_state(s.sid, "streaming")
    ret = mgr.update_state(s.sid, "resolving")
    cur = mgr.get_session(s.sid)
    assert ret is None
    assert cur is not None
    assert cur.state == "streaming"


def test_legal_transition_resolving_to_streaming_allowed():
    mgr = StreamSessionManager()
    s = mgr.create_session("https://a")
    mgr.update_state(s.sid, "resolving")
    ret = mgr.update_state(s.sid, "streaming")
    assert ret is not None
    assert ret.state == "streaming"


def test_legal_transition_streaming_to_stopped_allowed():
    mgr = StreamSessionManager()
    s = mgr.create_session("https://a")
    mgr.update_state(s.sid, "resolving")
    mgr.update_state(s.sid, "streaming")
    ret = mgr.update_state(s.sid, "stopped")
    assert ret is not None
    assert ret.state == "stopped"
