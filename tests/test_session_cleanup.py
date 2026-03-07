import pytest

from xiaomusic.network_audio.session_manager import StreamSessionManager


@pytest.mark.unit
def test_session_cleanup_max_sessions_keeps_active():
    mgr = StreamSessionManager()
    # 1 active + 200 stopped
    active = mgr.create_session("https://a")
    mgr.set_state(active.sid, "running")
    for i in range(200):
        s = mgr.create_session(f"https://x/{i}")
        mgr.set_state(s.sid, "stopped")

    ret = mgr.cleanup(max_sessions=100, ttl_seconds=None)
    sessions = mgr.list_sessions()
    assert ret["remaining"] <= 100
    assert any(s.sid == active.sid for s in sessions)


@pytest.mark.unit
def test_session_cleanup_ttl_removes_old_non_active_only():
    mgr = StreamSessionManager()
    old_stopped = mgr.create_session("https://old")
    mgr.set_state(old_stopped.sid, "stopped")
    running = mgr.create_session("https://live")
    mgr.set_state(running.sid, "running")

    # Force timestamps
    mgr._sessions[old_stopped.sid].updated_at = "2000-01-01T00:00:00Z"
    mgr._sessions[running.sid].updated_at = "2000-01-01T00:00:00Z"

    ret = mgr.cleanup(max_sessions=100, ttl_seconds=60)
    assert ret["removed"] >= 1
    assert mgr.get_session(old_stopped.sid) is None
    assert mgr.get_session(running.sid) is not None
