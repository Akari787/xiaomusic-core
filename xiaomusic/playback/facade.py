"""Unified playback facade for play, stop and status."""

from __future__ import annotations

from typing import Any, Callable


class PlaybackFacade:
    """Facade entry for playback operations.

    This class keeps old endpoints compatible while converging all
    playback operations into a single internal play_url/stop/status API.
    """

    def __init__(self, xiaomusic, runtime_provider: Callable[[], Any] | None = None) -> None:
        self.xiaomusic = xiaomusic
        self._runtime_provider = runtime_provider

    def _runtime(self):
        if self._runtime_provider is None:
            raise RuntimeError("network audio runtime provider is not configured")
        return self._runtime_provider()

    @staticmethod
    def _to_state(ok: bool, raw: dict[str, Any], default_state: str = "playing") -> str:
        if not ok:
            return "error"
        session = raw.get("session")
        if isinstance(session, dict):
            state = session.get("state")
            if state:
                return str(state)
        state = raw.get("state")
        if state:
            return str(state)
        return default_state

    @staticmethod
    def _extract_sid(raw: dict[str, Any]) -> str:
        session = raw.get("session")
        if isinstance(session, dict):
            sid = session.get("sid")
            if sid:
                return str(sid)
        sid = raw.get("sid")
        if sid:
            return str(sid)
        return ""

    @staticmethod
    def _extract_stream_url(raw: dict[str, Any], fallback: str = "") -> str:
        session = raw.get("session")
        if isinstance(session, dict):
            stream_url = session.get("stream_url")
            if stream_url:
                return str(stream_url)
        stream_url = raw.get("stream_url")
        if stream_url:
            return str(stream_url)
        return fallback

    async def play_url(
        self,
        url: str,
        speaker_id: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        options = options or {}
        mode = options.get("mode", "direct")
        prefer_proxy = bool(options.get("prefer_proxy", False))

        if mode == "network_audio_cast":
            raw = await self._runtime().play_and_cast(did=speaker_id, url=url)
            ok = bool(raw.get("ok", False))
        elif mode == "network_audio_link":
            raw = await self._runtime().play_link(
                did=speaker_id,
                url=url,
                prefer_proxy=prefer_proxy,
            )
            ok = bool(raw.get("ok", False))
        else:
            cast_ret = await self.xiaomusic.play_url(did=speaker_id, arg1=url)
            raw = {
                "ok": True,
                "mode": "direct",
                "cast_ret": cast_ret,
                "stream_url": url,
            }
            ok = True

        result = {
            "sid": self._extract_sid(raw),
            "speaker_id": speaker_id,
            "state": self._to_state(ok=ok, raw=raw),
            "title": raw.get("title"),
            "stream_url": self._extract_stream_url(raw, fallback=url),
            "error_code": raw.get("error_code"),
            "ok": ok,
            "raw": raw,
        }
        return result

    async def stop(self, target: dict[str, Any]) -> dict[str, Any]:
        sid = str(target.get("sid") or "")
        speaker_id = str(target.get("speaker_id") or "")

        if sid:
            raw = self._runtime().stop_session(sid=sid)
            session = raw.get("session") or {}
            return {
                "sid": sid,
                "speaker_id": speaker_id,
                "state": str(session.get("state") or "stopped"),
                "title": None,
                "stream_url": str(session.get("stream_url") or ""),
                "error_code": None,
                "ok": True,
                "raw": raw,
            }

        if speaker_id:
            try:
                await self.xiaomusic.stop(did=speaker_id, arg1="notts")
            except Exception:
                return {
                    "sid": sid,
                    "speaker_id": speaker_id,
                    "state": "error",
                    "title": None,
                    "stream_url": "",
                    "error_code": "E_STREAM_NOT_FOUND",
                    "ok": False,
                    "raw": {"ret": "Did not exist"},
                }
            raw = {"ret": "OK"}
            return {
                "sid": "",
                "speaker_id": speaker_id,
                "state": "stopped",
                "title": None,
                "stream_url": "",
                "error_code": None,
                "ok": True,
                "raw": raw,
            }

        return {
            "sid": "",
            "speaker_id": "",
            "state": "error",
            "title": None,
            "stream_url": "",
            "error_code": "E_INVALID_TARGET",
            "ok": False,
            "raw": {"ret": "Invalid target"},
        }

    async def status(self, target: dict[str, Any]) -> dict[str, Any]:
        sid = str(target.get("sid") or "")
        speaker_id = str(target.get("speaker_id") or "")

        if sid:
            session = self._runtime().session_manager.get_session(sid)
            if session is None:
                return {
                    "sid": sid,
                    "speaker_id": speaker_id,
                    "state": "error",
                    "title": None,
                    "stream_url": "",
                    "error_code": "E_STREAM_NOT_FOUND",
                    "ok": False,
                    "raw": {"ret": "Not found"},
                }
            return {
                "sid": sid,
                "speaker_id": speaker_id,
                "state": str(session.state),
                "title": str(session.meta.get("title") or "") or None,
                "stream_url": str(session.stream_url or ""),
                "error_code": None,
                "ok": True,
                "raw": {"session": session},
            }

        if speaker_id:
            try:
                raw = await self.xiaomusic.get_player_status(did=speaker_id)
            except Exception:
                return {
                    "sid": "",
                    "speaker_id": speaker_id,
                    "state": "error",
                    "title": None,
                    "stream_url": "",
                    "error_code": "E_STREAM_NOT_FOUND",
                    "ok": False,
                    "raw": {"ret": "Did not exist"},
                }
            return {
                "sid": "",
                "speaker_id": speaker_id,
                "state": str(raw.get("status", "unknown")),
                "title": None,
                "stream_url": "",
                "error_code": None,
                "ok": True,
                "raw": raw,
            }

        return {
            "sid": "",
            "speaker_id": "",
            "state": "error",
            "title": None,
            "stream_url": "",
            "error_code": "E_INVALID_TARGET",
            "ok": False,
            "raw": {"ret": "Invalid target"},
        }
