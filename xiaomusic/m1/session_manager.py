"""In-memory stream session lifecycle manager for M1."""

from __future__ import annotations

from datetime import UTC, datetime
from threading import Lock
from uuid import uuid4

from xiaomusic.m1.contracts import Session


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class StreamSessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = Lock()

    def create_session(self, input_url: str, stream_url: str = "", source_url: str = "") -> Session:
        with self._lock:
            sid = f"s_{uuid4().hex[:12]}"
            now = _now_iso()
            session = Session(
                sid=sid,
                state="creating",
                input_url=input_url,
                stream_url=stream_url,
                source_url=source_url,
                reconnect_count=0,
                created_at=now,
                updated_at=now,
                meta={},
            )
            self._sessions[sid] = session
            return session

    def stop_session(self, sid: str) -> Session | None:
        with self._lock:
            session = self._sessions.get(sid)
            if session is None:
                return None
            if session.state != "stopped":
                session.state = "stopped"
                session.updated_at = _now_iso()
            return session

    def set_state(self, sid: str, state: str) -> Session | None:
        with self._lock:
            session = self._sessions.get(sid)
            if session is None:
                return None
            session.state = state
            session.updated_at = _now_iso()
            return session

    def set_stream_url(self, sid: str, stream_url: str) -> Session | None:
        with self._lock:
            session = self._sessions.get(sid)
            if session is None:
                return None
            session.stream_url = stream_url
            session.updated_at = _now_iso()
            return session

    def set_source_url(self, sid: str, source_url: str) -> Session | None:
        with self._lock:
            session = self._sessions.get(sid)
            if session is None:
                return None
            session.source_url = source_url
            session.updated_at = _now_iso()
            return session

    def increment_reconnect(self, sid: str) -> Session | None:
        with self._lock:
            session = self._sessions.get(sid)
            if session is None:
                return None
            session.reconnect_count += 1
            session.updated_at = _now_iso()
            return session

    def get_session(self, sid: str) -> Session | None:
        with self._lock:
            return self._sessions.get(sid)

    def list_sessions(self) -> list[Session]:
        with self._lock:
            return list(self._sessions.values())
