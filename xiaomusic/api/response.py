"""Unified API response helpers.

This module centralizes JSON response building while preserving existing
external response contracts.
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar
from typing import Any

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from xiaomusic.api import errors as api_errors

_LOG = logging.getLogger("xiaomusic.api.response")
_REQUEST_ID: ContextVar[str | None] = ContextVar("xiaomusic_request_id", default=None)
_DEFAULT_INCLUDE_REQUEST_ID = False


def set_default_include_request_id(enabled: bool) -> None:
    global _DEFAULT_INCLUDE_REQUEST_ID
    _DEFAULT_INCLUDE_REQUEST_ID = bool(enabled)


def get_request_id() -> str | None:
    return _REQUEST_ID.get()


def bind_request_id(request_id: str | None) -> None:
    _REQUEST_ID.set(request_id)


def _ensure_request_id(request: Request | None = None) -> str:
    rid = get_request_id()
    if rid:
        return rid
    if request is not None:
        hdr = request.headers.get("X-Request-ID")
        if hdr:
            bind_request_id(hdr)
            return hdr
    rid = uuid.uuid4().hex[:16]
    bind_request_id(rid)
    return rid


def _payload_dict(data: Any = None) -> dict[str, Any]:
    if data is None:
        return {}
    if isinstance(data, dict):
        return dict(data)
    return {"data": data}


def _with_status(body: dict[str, Any], http_status: int | None):
    if http_status is None:
        return body
    return JSONResponse(status_code=http_status, content=body)


def ok(data: Any = None, **extra):
    """Build a success payload while preserving current route contracts.

    extra keys:
    - contract: standard|ret|success_error|detail|raw
    - http_status: optional HTTP status code
    - message: optional message
    """

    contract = extra.pop("contract", "standard")
    http_status = extra.pop("http_status", None)
    message = extra.pop("message", None)
    ret_value = extra.pop("ret", "OK")

    if contract == "ret":
        body = {"ret": ret_value}
        body.update(_payload_dict(data))
        body.update(extra)
        return _with_status(body, http_status)

    if contract == "success_error":
        body = {"success": True}
        body.update(_payload_dict(data))
        if message is not None:
            body["message"] = message
        body.update(extra)
        return _with_status(body, http_status)

    if contract == "detail":
        detail = message if message is not None else data
        body = {"detail": detail}
        body.update(extra)
        return _with_status(body, http_status)

    if contract == "raw":
        if data is None:
            body = {}
        elif isinstance(data, dict):
            body = dict(data)
        elif not extra and http_status is None:
            return data
        else:
            body = {"data": data}
        body.update(extra)
        return _with_status(body, http_status)

    body = {
        "ok": True,
        "success": True,
        "error_code": None,
        "message": message,
    }
    body.update(_payload_dict(data))
    body.update(extra)
    return _with_status(body, http_status)


def fail(error_code: str, message: str, http_status: int | None = None, **extra):
    """Build a failed payload while preserving current route contracts."""

    contract = extra.pop("contract", "standard")
    request = extra.pop("request", None)
    exc = extra.pop("exc", None)
    include_request_id = extra.pop("include_request_id", _DEFAULT_INCLUDE_REQUEST_ID)
    headers = extra.pop("headers", None)
    detail_payload = extra.pop("detail_payload", None)
    ret_value = extra.pop("ret", message)

    request_id = _ensure_request_id(request)
    _LOG.warning(
        "api_fail error_code=%s method=%s path=%s request_id=%s exc=%s",
        error_code,
        getattr(request, "method", "-"),
        str(getattr(request, "url", "-")),
        request_id,
        exc.__class__.__name__ if exc else "-",
    )

    if contract == "ret":
        body = {"ret": ret_value}
    elif contract == "success_error":
        body = {"success": False, "error": message}
    elif contract == "detail":
        body = {"detail": detail_payload if detail_payload is not None else message}
    elif contract == "raw":
        body = _payload_dict(extra.pop("data", None))
    else:
        body = {
            "ok": False,
            "success": False,
            "error_code": error_code,
            "message": message,
        }

    body.update(extra)
    if include_request_id:
        body.setdefault("request_id", request_id)

    if http_status is None:
        return body
    return JSONResponse(status_code=http_status, content=body, headers=headers)


def from_exception(
    exc: Exception,
    error_code: str = api_errors.E_INTERNAL,
    message: str = "Internal server error",
    http_status: int | None = None,
    **extra,
):
    """Map exception objects to existing public response shapes."""

    request = extra.pop("request", None)

    if isinstance(exc, RequestValidationError):
        return fail(
            api_errors.E_VALIDATION,
            "Validation error",
            http_status=http_status or 422,
            contract="detail",
            detail_payload=exc.errors(),
            request=request,
            exc=exc,
            **extra,
        )

    if isinstance(exc, HTTPException):
        status_code = http_status or int(exc.status_code)
        if status_code == 401:
            mapped = api_errors.E_UNAUTHORIZED
        elif status_code == 403:
            mapped = api_errors.E_FORBIDDEN
        elif status_code == 404:
            mapped = api_errors.E_NOT_FOUND
        elif status_code == 400:
            mapped = api_errors.E_BAD_REQUEST
        else:
            mapped = error_code

        detail = exc.detail
        if isinstance(detail, (dict, list)):
            return fail(
                mapped,
                "HTTP error",
                http_status=status_code,
                contract="detail",
                detail_payload=detail,
                request=request,
                exc=exc,
                headers=exc.headers,
                **extra,
            )
        return fail(
            mapped,
            str(detail),
            http_status=status_code,
            contract="detail",
            request=request,
            exc=exc,
            headers=exc.headers,
            **extra,
        )

    _LOG.exception("unhandled_exception request_id=%s", _ensure_request_id(request), exc_info=exc)
    return fail(
        error_code,
        message,
        http_status=http_status or 500,
        request=request,
        exc=exc,
        **extra,
    )
