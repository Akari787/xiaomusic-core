"""In-memory stream session lifecycle manager for network audio."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from threading import Lock
from uuid import uuid4

from xiaomusic.network_audio.contracts import SESSION_STATES, Session


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


allowed_transitions: dict[str, list[str]] = {
    "creating": ["resolving", "failed"],
    "resolving": ["streaming", "failed"],
    "streaming": ["reconnecting", "stopped", "failed"],
    "reconnecting": ["streaming", "failed", "stopped"],
    "stopped": [],
    "failed": [],
}


_log = logging.getLogger(__name__)


class StreamSessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = Lock()

    def create_session(self, input_url: str, stream_url: str = "", source_url: str = "") -> Session:
        with self._lock:
            sid = f"s_{uuid4().hex[:12]}"
            now = _now_iso()
            now_ts = int(datetime.now(UTC).timestamp())
            session = Session(
                sid=sid,
                state="creating",
                input_url=input_url,
                stream_url=stream_url,
                source_url=source_url,
                reconnect_count=0,
                created_at=now,
                updated_at=now,
                last_transition_at=now_ts,
                started_at=None,
                stopped_at=None,
                last_error_code=None,
                resolve_ms=None,
                stream_start_ms=None,
                last_client_at=now_ts,
                meta={},
            )
            self._sessions[sid] = session
            return session

    def stop_session(self, sid: str) -> Session | None:
        return self.update_state(sid, "stopped", force=True)

    @staticmethod
    def _now_ts() -> int:
        return int(datetime.now(UTC).timestamp())

    def update_state(
        self,
        sid: str,
        new_state: str,
        *,
        error_code: str | None = None,
        now_ts: int | None = None,
        force: bool = False,
        **metrics,
    ) -> Session | None:
        if new_state == "running":
            new_state = "streaming"
        if new_state not in SESSION_STATES:
            raise ValueError(f"unsupported session state: {new_state}")

        with self._lock:
            session = self._sessions.get(sid)
            if session is None:
                return None

            ts = int(now_ts if now_ts is not None else self._now_ts())
            current_state = (session.state or "").lower()
            if current_state == new_state:
                pass
            elif (not force) and new_state not in allowed_transitions.get(current_state, []):
                _log.warning(
                    "reject_illegal_transition sid=%s from=%s to=%s",
                    sid,
                    current_state,
                    new_state,
                )
                return None
            session.state = new_state
            session.updated_at = _now_iso()
            session.last_transition_at = ts

            if new_state == "streaming" and session.started_at is None:
                session.started_at = ts
            if new_state == "stopped":
                session.stopped_at = ts
            if new_state == "failed":
                session.last_error_code = error_code or "E_INTERNAL"
            elif error_code is not None:
                session.last_error_code = error_code

            for key in ("resolve_ms", "stream_start_ms", "last_client_at"):
                if key in metrics and metrics[key] is not None:
                    setattr(session, key, int(metrics[key]))

            return session

    def set_state(self, sid: str, state: str) -> Session | None:
        if state == "running":
            cur = self.get_session(sid)
            if cur is not None and (cur.state or "").lower() == "creating":
                self.update_state(sid, "resolving")
                return self.update_state(sid, "streaming")
        if state == "stopped":
            return self.update_state(sid, "stopped", force=True)
        return self.update_state(sid, state)

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

    def touch_client(self, sid: str, now_ts: int | None = None) -> Session | None:
        with self._lock:
            session = self._sessions.get(sid)
            if session is None:
                return None
            session.last_client_at = int(now_ts if now_ts is not None else self._now_ts())
            session.updated_at = _now_iso()
            return session

    def count_active(self) -> int:
        active = {"creating", "resolving", "streaming", "reconnecting"}
        with self._lock:
            return sum(1 for s in self._sessions.values() if (s.state or "").lower() in active)

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
            return s in {"creating", "resolving", "streaming", "reconnecting"}

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
