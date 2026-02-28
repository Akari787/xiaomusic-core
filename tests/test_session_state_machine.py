import pytest

from xiaomusic.network_audio.contracts import SESSION_STATES
from xiaomusic.network_audio.session_manager import StreamSessionManager


def test_update_state_updates_transition_and_error_fields():
    mgr = StreamSessionManager()
    s = mgr.create_session("https://a")

    mgr.update_state(s.sid, "failed", error_code="E_STREAM_START_FAILED", now_ts=100)
    cur = mgr.get_session(s.sid)
    assert cur is not None
    assert cur.state == "failed"
    assert cur.last_transition_at == 100
    assert cur.last_error_code == "E_STREAM_START_FAILED"


def test_stop_session_sets_stopped_at():
    mgr = StreamSessionManager()
    s = mgr.create_session("https://a")
    mgr.update_state(s.sid, "resolving", now_ts=110)
    mgr.update_state(s.sid, "streaming", now_ts=120)
    mgr.update_state(s.sid, "stopped", now_ts=130)

    cur = mgr.get_session(s.sid)
    assert cur is not None
    assert cur.state == "stopped"
    assert cur.stopped_at == 130


def test_unknown_state_rejected():
    mgr = StreamSessionManager()
    s = mgr.create_session("https://a")
    with pytest.raises(ValueError):
        mgr.update_state(s.sid, "unknown-new-state")


def test_state_set_matches_contract_enum():
    assert {
        "creating",
        "resolving",
        "streaming",
        "reconnecting",
        "stopped",
        "failed",
    } == SESSION_STATES
