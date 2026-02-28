"""In-memory stream session lifecycle manager for network audio."""

from __future__ import annotations

from datetime import UTC, datetime
from threading import Lock
from uuid import uuid4

from xiaomusic.network_audio.contracts import Session


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

    def delete_session(self, sid: str) -> bool:
        with self._lock:
            return self._sessions.pop(sid, None) is not None

    @staticmethod
    def _to_epoch(ts: str) -> float:
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except Exception:
            return 0.0

    def cleanup(self, max_sessions: int = 100, ttl_seconds: int | None = None) -> dict[str, int]:
        max_sessions = max(1, int(max_sessions or 100))
        now = datetime.now(UTC).timestamp()
        removed = 0

        def _is_active(state: str) -> bool:
            s = (state or "").lower()
            return s in {"running", "streaming"}

        with self._lock:
            if ttl_seconds is not None and int(ttl_seconds) > 0:
                ttl = int(ttl_seconds)
                to_remove = []
                for sid, session in self._sessions.items():
                    if _is_active(session.state):
                        continue
                    updated = self._to_epoch(session.updated_at or session.created_at)
                    if updated > 0 and now - updated >= ttl:
                        to_remove.append(sid)
                for sid in to_remove:
                    self._sessions.pop(sid, None)
                    removed += 1

            if len(self._sessions) > max_sessions:
                # Remove oldest non-active sessions first.
                inactive = [
                    s
                    for s in self._sessions.values()
                    if not _is_active(s.state)
                ]
                inactive.sort(key=lambda s: self._to_epoch(s.updated_at or s.created_at))
                overflow = len(self._sessions) - max_sessions
                for sess in inactive[:overflow]:
                    if self._sessions.pop(sess.sid, None) is not None:
                        removed += 1

            remaining = len(self._sessions)
        return {"removed": removed, "remaining": remaining}
