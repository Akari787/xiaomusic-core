from __future__ import annotations

from pydantic import BaseModel

from xiaomusic.api import response as api_response
from xiaomusic.api.models import ApiPlaybackResponse
from xiaomusic.network_audio.contracts import ERROR_CODES


def error_message(error_code: str | None, fallback: str | None = None) -> str | None:
    if fallback is not None:
        return fallback
    if not error_code:
        return None
    return ERROR_CODES.get(error_code, "Unknown error")


def _payload_to_dict(payload: dict | BaseModel | None) -> dict:
    if payload is None:
        return {}
    if isinstance(payload, BaseModel):
        return payload.model_dump()
    return dict(payload)


def make_ok(payload: dict | BaseModel | None = None, message: str | None = None) -> dict:
    return api_response.ok(_payload_to_dict(payload), message=message)


def make_error(
    error_code: str,
    message: str | None = None,
    payload: dict | BaseModel | None = None,
) -> dict:
    body = _payload_to_dict(payload)
    return api_response.fail(
        error_code,
        error_message(error_code, message) or "Unknown error",
        **body,
    )


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
    stage: str | None = None,
    last_transition_at: int | None = None,
    last_error_code: str | None = None,
    cache_hit: bool | None = None,
    resolve_ms: int | None = None,
    error_code: str | None = None,
    message: str | None = None,
    deprecated: bool | None = None,
) -> dict:
    if ok:
        resolved_error_code = None
        resolved_message = message
    else:
        resolved_error_code = error_code or "E_INTERNAL"
        resolved_message = error_message(resolved_error_code, message)

    payload = ApiPlaybackResponse(
        ok=ok,
        success=ok,
        error_code=resolved_error_code,
        message=resolved_message,
        sid=sid,
        speaker_id=speaker_id,
        state=state,
        title=title,
        stream_url=stream_url,
        is_live=is_live,
        uptime=uptime,
        reconnect_count=reconnect_count,
        stage=stage,
        last_transition_at=last_transition_at,
        last_error_code=last_error_code,
        cache_hit=cache_hit,
        resolve_ms=resolve_ms,
        deprecated=deprecated,
    )
    return payload.model_dump()
