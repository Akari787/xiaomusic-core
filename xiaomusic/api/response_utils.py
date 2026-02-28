from __future__ import annotations

from xiaomusic.api.models import ApiPlaybackResponse
from xiaomusic.network_audio.contracts import ERROR_CODES


def error_message(error_code: str | None, fallback: str | None = None) -> str | None:
    if error_code and error_code in ERROR_CODES:
        return ERROR_CODES[error_code]
    if error_code and not fallback:
        return error_code
    return fallback


def playback_response(
    *,
    ok: bool,
    sid: str = "",
    speaker_id: str = "",
    state: str = "unknown",
    title: str | None = None,
    stream_url: str = "",
    is_live: bool | None = None,
    uptime: int | None = None,
    reconnect_count: int | None = None,
    error_code: str | None = None,
    message: str | None = None,
    deprecated: bool | None = None,
) -> dict:
    payload = ApiPlaybackResponse(
        ok=ok,
        success=ok,
        error_code=error_code,
        message=error_message(error_code, message),
        sid=sid,
        speaker_id=speaker_id,
        state=state,
        title=title,
        stream_url=stream_url,
        is_live=is_live,
        uptime=uptime,
        reconnect_count=reconnect_count,
        deprecated=deprecated,
    )
    return payload.model_dump()
