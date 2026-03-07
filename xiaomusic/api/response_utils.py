from __future__ import annotations

import uuid

from typing import Any

from pydantic import BaseModel

from xiaomusic.api.models import ApiPlaybackResponse
from xiaomusic.network_audio.contracts import ERROR_CODES


OK_CODE = 0
DEFAULT_ERROR_CODE = 10000
ERROR_CODE_INT: dict[str, int] = {
    "E_URL_UNSUPPORTED": 10001,
    "E_RESOLVE_TIMEOUT": 10002,
    "E_RESOLVE_NONZERO_EXIT": 10003,
    "E_STREAM_START_FAILED": 10004,
    "E_STREAM_NOT_FOUND": 10005,
    "E_STREAM_SINGLE_CLIENT_ONLY": 10006,
    "E_XIAOMI_PLAY_FAILED": 10007,
    "E_TOO_MANY_SESSIONS": 10008,
    "E_INTERNAL": 10009,
}


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


def _code_to_int(error_code: str | None) -> int:
    if not error_code:
        return OK_CODE
    return ERROR_CODE_INT.get(error_code, DEFAULT_ERROR_CODE)


def _build_response(code: int, message: str, data: dict | None = None) -> dict[str, Any]:
    return {
        "code": int(code),
        "message": str(message),
        "data": data or {},
    }


def make_ok(payload: dict | BaseModel | None = None, message: str | None = None) -> Any:
    return _build_response(
        code=OK_CODE,
        message=message or "ok",
        data=_payload_to_dict(payload),
    )


def make_error(
    error_code: str,
    message: str | None = None,
    payload: dict | BaseModel | None = None,
) -> Any:
    body = _payload_to_dict(payload)
    request_id = uuid.uuid4().hex[:16]
    body["request_id"] = request_id
    body["error_code"] = error_code
    return _build_response(
        code=_code_to_int(error_code),
        message=error_message(error_code, message) or "Unknown error",
        data=body,
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
    resolved_error_code = None if ok else (error_code or "E_INTERNAL")
    resolved_message = message if ok else error_message(resolved_error_code, message)

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
    data = payload.model_dump()
    if not ok:
        data["request_id"] = uuid.uuid4().hex[:16]
        data["error_code"] = resolved_error_code
    return _build_response(
        code=_code_to_int(resolved_error_code),
        message=resolved_message or ("ok" if ok else "Unknown error"),
        data=data,
    )
