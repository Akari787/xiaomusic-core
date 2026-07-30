"""Shared helpers and handler logic for v1, admin, and diagnostics routers.

Contains:
- Shared facade/manager access (single source of truth — no duplicate facades)
- Shared source-service functions for sources/admin routes
- Shared diagnostics query functions for debug/diagnostics routes
- Shared auth-status query function
- Error mapping helpers
"""

from __future__ import annotations

import logging
import time
from typing import Any
from uuid import uuid4

from xiaomusic.api.api_error import ApiError
from xiaomusic.api.models import ApiResponse
from xiaomusic.core.errors import (
    DeliveryPrepareError,
    DeviceNotFoundError,
    InvalidRequestError,
    SourceResolveError,
    TransportError,
)
from xiaomusic.playback.facade import PlaybackFacade

LOG = logging.getLogger("xiaomusic.api.v1_shared")

# ── Single facade instance (no other module may create its own) ────────
_facade: PlaybackFacade | None = None


def _get_xiaomusic():
    from xiaomusic.api.dependencies import xiaomusic

    return xiaomusic


def _get_facade() -> PlaybackFacade:
    """Return the singleton PlaybackFacade.  Tests may monkeypatch this name."""
    global _facade
    if _facade is None:
        from xiaomusic.api.runtime_provider import get_runtime as _gr

        _facade = PlaybackFacade(_get_xiaomusic(), link_preparer=_gr)
    return _facade


def _get_source_plugin_manager():
    """Return the source plugin manager from the shared facade."""
    return _get_facade()._get_source_plugin_manager()


def _next_request_id(raw: str | None = None) -> str:
    return str(raw or uuid4().hex[:16])


# ── Response helpers ───────────────────────────────────────────────────

def _api_response(
    code: int, message: str, data: dict[str, Any], request_id: str
) -> dict[str, Any]:
    return ApiResponse(
        code=int(code), message=str(message), data=data, request_id=str(request_id)
    ).model_dump()


def _api_ok(data: dict[str, Any], request_id: str) -> dict[str, Any]:
    return _api_response(0, "ok", data, request_id)


def _bad_request(
    request_id: str, message: str, *, field: str = "", allowed: list[str] | None = None
) -> ApiError:
    data: dict[str, Any] = {"error_code": "E_INVALID_REQUEST", "stage": "request"}
    if field:
        data["field"] = field
    if allowed:
        data["allowed"] = allowed
    return ApiError(code=40001, message=message, data=data, request_id=request_id)


def _require_device(device_id: str, request_id: str):
    xm = _get_xiaomusic()
    if not xm.did_exist(device_id):
        raise ApiError(
            code=40004,
            message="device not found",
            data={"error_code": "E_DEVICE_NOT_FOUND", "stage": "request"},
            request_id=request_id,
        )
    return xm


# ── Error mapping ──────────────────────────────────────────────────────

def _map_api_exception(exc: Exception, request_id: str) -> dict[str, Any]:
    if isinstance(exc, ApiError):
        return _api_response(
            exc.code, exc.message, exc.data, str(exc.request_id or request_id)
        )
    if isinstance(exc, InvalidRequestError):
        return _api_response(
            40001, str(exc),
            {
                "error_type": exc.__class__.__name__,
                "error_code": "E_INVALID_REQUEST",
                "stage": "request",
            },
            request_id,
        )
    if isinstance(exc, SourceResolveError):
        return _api_response(
            20002, "source resolve failed",
            {
                "error_type": exc.__class__.__name__,
                "error_code": "E_RESOLVE_NONZERO_EXIT",
                "stage": "resolve",
            },
            request_id,
        )
    if isinstance(exc, DeliveryPrepareError):
        return _api_response(
            30001, "delivery prepare failed",
            {
                "error_type": exc.__class__.__name__,
                "error_code": "E_STREAM_NOT_FOUND",
                "stage": "prepare",
            },
            request_id,
        )
    if isinstance(exc, TransportError):
        return _api_response(
            40002, "transport dispatch failed",
            {
                "error_type": exc.__class__.__name__,
                "error_code": "E_XIAOMI_PLAY_FAILED",
                "stage": "dispatch",
            },
            request_id,
        )
    if isinstance(exc, DeviceNotFoundError):
        return _api_response(
            40004, "device not found",
            {
                "error_type": exc.__class__.__name__,
                "error_code": "E_DEVICE_NOT_FOUND",
                "stage": "request",
            },
            request_id,
        )
    return _api_response(
        10000, "internal error",
        {
            "error_type": exc.__class__.__name__,
            "error_code": "E_INTERNAL",
            "stage": "system",
        },
        request_id,
    )


def _map_structured_endpoint_exception(
    exc: Exception,
    request_id: str,
    *,
    default_error_code: str,
    default_stage: str,
    default_message: str,
) -> dict[str, Any]:
    if isinstance(
        exc,
        (
            ApiError,
            InvalidRequestError,
            SourceResolveError,
            DeliveryPrepareError,
            TransportError,
            DeviceNotFoundError,
        ),
    ):
        return _map_api_exception(exc, request_id)
    if isinstance(exc, PermissionError):
        return _api_response(
            40301, str(exc),
            {"error_type": exc.__class__.__name__, "error_code": "E_FORBIDDEN", "stage": default_stage},
            request_id,
        )
    if isinstance(exc, FileNotFoundError):
        return _api_response(
            40401, str(exc),
            {"error_type": exc.__class__.__name__, "error_code": "E_NOT_FOUND", "stage": default_stage},
            request_id,
        )
    if isinstance(exc, ValueError):
        return _api_response(
            40001, str(exc),
            {"error_type": exc.__class__.__name__, "error_code": "E_INVALID_REQUEST", "stage": default_stage},
            request_id,
        )
    return _api_response(
        10000, default_message,
        {"error_type": exc.__class__.__name__, "error_code": default_error_code, "stage": default_stage},
        request_id,
    )


def _map_public_endpoint_exception(
    exc: Exception,
    request_id: str,
    *,
    default_error_code: str,
    default_stage: str,
    default_message: str,
) -> dict[str, Any]:
    return _map_structured_endpoint_exception(
        exc, request_id,
        default_error_code=default_error_code,
        default_stage=default_stage,
        default_message=default_message,
    )


async def _runtime_auth_ready_v1() -> bool:
    try:
        am = getattr(_get_xiaomusic(), "auth_manager", None)
        if am is None:
            return False
        return not await am.need_login()
    except Exception:
        return False


# ── Shared service functions (used by old AND new routes) ──────────────

def sources_list_svc() -> dict[str, Any]:
    """Core logic for GET /api/.../sources — returns data dict (not envelope)."""
    manager = _get_source_plugin_manager()
    return {"registry_version": int(manager.registry_version), "sources": manager.describe_plugins()}


def sources_reload_svc() -> dict[str, Any]:
    manager = _get_source_plugin_manager()
    return manager.reload_summary()


def sources_upload_svc(filename: str, content: bytes) -> dict[str, Any]:
    manager = _get_source_plugin_manager()
    return manager.upload_plugin(filename or "", content)


def sources_delete_svc(name: str) -> dict[str, Any]:
    manager = _get_source_plugin_manager()
    return manager.uninstall_plugin(name)


def sources_enable_svc(name: str) -> dict[str, Any]:
    manager = _get_source_plugin_manager()
    return manager.enable_plugin(name)


def sources_disable_svc(name: str) -> dict[str, Any]:
    manager = _get_source_plugin_manager()
    return manager.disable_plugin(name)


async def auth_status_v1_svc() -> dict[str, Any]:
    """Core logic for auth status — returns data dict (not envelope)."""
    runtime_auth_ready = await _runtime_auth_ready_v1()
    am = getattr(_get_xiaomusic(), "auth_manager", None)
    if am is not None and hasattr(am, "map_auth_public_status"):
        data = am.map_auth_public_status(runtime_auth_ready=runtime_auth_ready)
    else:
        data = {
            "status": "unknown",
            "auth_mode": "unknown",
            "status_reason": "unknown",
            "recovery_failure_count": 0,
        }
    data["generated_at_ms"] = int(time.time() * 1000)
    return data


# ── Diagnostics query helpers ──────────────────────────────────────────

_DIAG_METHOD_MAP: dict[str, tuple[str, dict[str, Any]]] = {
    "auth_state": (
        "auth_debug_state",
        {"auth_mode": "unknown", "login_at": None, "expires_at": None,
         "ttl_remaining_seconds": None, "last_refresh_trigger": "", "last_auth_error": ""},
    ),
    "auth_recovery_state": (
        "auth_recovery_debug_state",
        {"last_clear_short_session": {}, "last_login_exchange": {},
         "last_runtime_rebind": {}, "last_playback_capability_verify": {}},
    ),
    "miaccount_login_trace": (
        "miaccount_login_trace_debug_state",
        {"login_input_snapshot": {}, "login_http_exchange": {}, "login_response_parse": {},
         "token_writeback": {}, "post_login_runtime_seed": {}},
    ),
    "auth_rebuild_state": (
        "auth_rebuild_debug_state",
        {"last_clear_short_session": {}, "last_rebuild_short_session": {},
         "last_runtime_rebind": {}, "last_verify": {}},
    ),
    "auth_runtime_reload_state": (
        "auth_runtime_reload_debug_state",
        {"last_reload_runtime": {}},
    ),
    "auth_short_session_rebuild_state": (
        "auth_short_session_rebuild_debug_state",
        {"last_short_session_rebuild": {}, "last_persistent_auth_relogin": {},
         "last_runtime_rebind": {}, "last_verify": {},
         "last_auth_recovery_flow": {}, "last_locked_transition": {}},
    ),
}


def diag_query_svc(key: str) -> dict[str, Any]:
    """Core logic for a diagnostics query — returns data dict (not envelope)."""
    method_name, default = _DIAG_METHOD_MAP[key]
    am = getattr(_get_xiaomusic(), "auth_manager", None)
    if am is not None and hasattr(am, method_name):
        return getattr(am, method_name)()
    return dict(default)
