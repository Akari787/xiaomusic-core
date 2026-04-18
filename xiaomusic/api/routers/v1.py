from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import asdict
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, File, Query, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from xiaomusic import __version__
from xiaomusic.api.api_error import ApiError
from xiaomusic.constants.api_fields import (
    DEVICE_ID,
    OPTIONS,
    QUERY,
    REQUEST_ID,
    SOURCE_HINT,
)
from xiaomusic.api.models import (
    ApiResponse,
    ControlRequest,
    FavoritesRequest,
    LibraryRefreshRequest,
    PlayModeRequest,
    PlayRequest,
    ResolveRequest,
    ShutdownTimerRequest,
    SystemSettingItemUpdateRequest,
    SystemSettingsSaveRequest,
    TtsRequest,
    VolumeRequest,
)
from xiaomusic.api.runtime_provider import get_runtime
from xiaomusic.core.errors import (
    DeliveryPrepareError,
    DeviceNotFoundError,
    InvalidRequestError,
    SourceResolveError,
    TransportError,
)
from xiaomusic.core.models import PlayOptions
from xiaomusic.playback.facade import PlaybackFacade

router = APIRouter()
_facade: PlaybackFacade | None = None
LOG = logging.getLogger("xiaomusic.api.v1")
_PLAY_MODE_HANDLERS = {
    "one": "set_play_type_one",
    "all": "set_play_type_all",
    "random": "set_play_type_rnd",
    "single": "set_play_type_sin",
    "sequence": "set_play_type_seq",
}


def _get_xiaomusic():
    from xiaomusic.api.dependencies import xiaomusic

    return xiaomusic


def _get_facade() -> PlaybackFacade:
    global _facade
    if _facade is None:
        _facade = PlaybackFacade(_get_xiaomusic(), runtime_provider=get_runtime)
    return _facade


def _get_source_plugin_manager():
    return _get_facade()._get_source_plugin_manager()


def _next_request_id(raw: str | None = None) -> str:
    return str(raw or uuid4().hex[:16])


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


def _map_api_exception(exc: Exception, request_id: str) -> dict[str, Any]:
    if isinstance(exc, ApiError):
        return _api_response(
            exc.code, exc.message, exc.data, str(exc.request_id or request_id)
        )
    if isinstance(exc, InvalidRequestError):
        return _api_response(
            40001,
            str(exc),
            {
                "error_type": exc.__class__.__name__,
                "error_code": "E_INVALID_REQUEST",
                "stage": "request",
            },
            request_id,
        )
    if isinstance(exc, SourceResolveError):
        return _api_response(
            20002,
            "source resolve failed",
            {
                "error_type": exc.__class__.__name__,
                "error_code": "E_RESOLVE_NONZERO_EXIT",
                "stage": "resolve",
            },
            request_id,
        )
    if isinstance(exc, DeliveryPrepareError):
        return _api_response(
            30001,
            "delivery prepare failed",
            {
                "error_type": exc.__class__.__name__,
                "error_code": "E_STREAM_NOT_FOUND",
                "stage": "prepare",
            },
            request_id,
        )
    if isinstance(exc, TransportError):
        return _api_response(
            40002,
            "transport dispatch failed",
            {
                "error_type": exc.__class__.__name__,
                "error_code": "E_XIAOMI_PLAY_FAILED",
                "stage": "dispatch",
            },
            request_id,
        )
    if isinstance(exc, DeviceNotFoundError):
        return _api_response(
            40004,
            "device not found",
            {
                "error_type": exc.__class__.__name__,
                "error_code": "E_DEVICE_NOT_FOUND",
                "stage": "request",
            },
            request_id,
        )
    return _api_response(
        10000,
        "internal error",
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
            40301,
            str(exc),
            {
                "error_type": exc.__class__.__name__,
                "error_code": "E_FORBIDDEN",
                "stage": default_stage,
            },
            request_id,
        )
    if isinstance(exc, FileNotFoundError):
        return _api_response(
            40401,
            str(exc),
            {
                "error_type": exc.__class__.__name__,
                "error_code": "E_NOT_FOUND",
                "stage": default_stage,
            },
            request_id,
        )
    if isinstance(exc, ValueError):
        return _api_response(
            40001,
            str(exc),
            {
                "error_type": exc.__class__.__name__,
                "error_code": "E_INVALID_REQUEST",
                "stage": default_stage,
            },
            request_id,
        )
    return _api_response(
        10000,
        default_message,
        {
            "error_type": exc.__class__.__name__,
            "error_code": default_error_code,
            "stage": default_stage,
        },
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
        exc,
        request_id,
        default_error_code=default_error_code,
        default_stage=default_stage,
        default_message=default_message,
    )


def _normalize_device(device: dict[str, Any]) -> dict[str, Any]:
    device_id = str(
        device.get("miotDID") or device.get("did") or device.get("deviceID") or ""
    )
    return {
        DEVICE_ID: device_id,
        "name": str(device.get("name") or device.get("alias") or ""),
        "model": str(device.get("hardware") or ""),
        "online": bool(device.get("isOnline") or device.get("online") or False),
    }


async def _runtime_auth_ready_v1() -> bool:
    try:
        am = getattr(_get_xiaomusic(), "auth_manager", None)
        if am is None:
            return False
        return not await am.need_login()
    except Exception:
        return False


async def _settings_snapshot(include_devices: bool) -> dict[str, Any]:
    xm = _get_xiaomusic()
    config_data = asdict(xm.getconfig())
    config_data["httpauth_password"] = "******"
    if config_data.get("jellyfin_api_key"):
        config_data["jellyfin_api_key"] = "******"

    token_available = False
    try:
        ts = getattr(xm, "token_store", None)
        token = ts.get() if ts is not None else {}
        st = token.get("serviceToken") or token.get("yetAnotherServiceToken")
        token_available = bool(
            token.get("userId")
            and token.get("passToken")
            and token.get("ssecurity")
            and st
        )
    except Exception:
        token_available = False
    config_data["auth_token_available"] = token_available
    config_data["auth_runtime_ready"] = await _runtime_auth_ready_v1()

    device_ids = [
        part.strip()
        for part in str(config_data.get("mi_did") or "").split(",")
        if part.strip()
    ]
    devices: list[dict[str, Any]] = []
    if include_devices:
        raw_devices = await xm.getalldevices()
        devices = [
            _normalize_device(item)
            for item in (raw_devices or [])
            if isinstance(item, dict)
        ]

    return {
        "settings": config_data,
        "device_ids": device_ids,
        "devices": devices,
    }


@router.post("/api/v1/play")
async def api_v1_play(data: PlayRequest):
    request_id = _next_request_id(data.request_id)
    options = PlayOptions.from_payload(getattr(data, OPTIONS, None))
    try:
        facade = _get_facade()
        out = await facade.play(
            device_id=getattr(data, DEVICE_ID),
            query=getattr(data, QUERY),
            source_hint=getattr(data, SOURCE_HINT),
            options=options,
            request_id=request_id,
        )
        snapshot: dict[str, Any] | None = None
        try:
            snapshot = await facade.build_player_state_snapshot(
                device_id=out.get(DEVICE_ID, getattr(data, DEVICE_ID))
            )
        except Exception:
            LOG.warning(
                "api_warn endpoint=/api/v1/play request_id=%s device_id=%s snapshot=build_failed",
                request_id,
                out.get(DEVICE_ID, getattr(data, DEVICE_ID)),
                exc_info=True,
            )
        return _api_ok(
            {
                "status": out.get("status", "playing"),
                DEVICE_ID: out.get(DEVICE_ID, getattr(data, DEVICE_ID)),
                "source_plugin": out.get("source_plugin", ""),
                "transport": out.get("transport", ""),
                "sid": request_id,
                "media": out.get("media", {}),
                "state": snapshot,
                "extra": out.get("extra", {}),
            },
            request_id=request_id,
        )
    except Exception as exc:
        LOG.exception(
            "api_fail endpoint=/api/v1/play request_id=%s device_id=%s source_hint=%s error=%s",
            request_id,
            getattr(data, DEVICE_ID),
            getattr(data, SOURCE_HINT),
            exc.__class__.__name__,
        )
        return _map_public_endpoint_exception(
            exc,
            request_id,
            default_error_code="E_PLAY_OPERATION_FAILED",
            default_stage="dispatch",
            default_message="play operation failed",
        )


@router.post("/api/v1/resolve")
async def api_v1_resolve(data: ResolveRequest):
    request_id = _next_request_id(data.request_id)
    options = PlayOptions.from_payload(getattr(data, OPTIONS, None))
    try:
        out = await _get_facade().resolve(
            query=getattr(data, QUERY),
            source_hint=getattr(data, SOURCE_HINT),
            options=options,
            request_id=request_id,
        )
        return _api_ok(
            {
                "resolved": bool(out.get("resolved", False)),
                "source_plugin": out.get("source_plugin", ""),
                "media": out.get("media", {}),
                "extra": out.get("extra", {}),
            },
            request_id=request_id,
        )
    except Exception as exc:
        return _map_public_endpoint_exception(
            exc,
            request_id,
            default_error_code="E_RESOLVE_OPERATION_FAILED",
            default_stage="resolve",
            default_message="resolve operation failed",
        )


@router.post("/api/v1/control/stop")
async def api_v1_control_stop(data: ControlRequest):
    request_id = _next_request_id(data.request_id)
    try:
        out = await _get_facade().stop(data.device_id, request_id=request_id)
        return _api_ok(
            {k: v for k, v in out.items() if k != REQUEST_ID}, request_id=request_id
        )
    except Exception as exc:
        return _map_public_endpoint_exception(
            exc,
            request_id,
            default_error_code="E_STOP_OPERATION_FAILED",
            default_stage="dispatch",
            default_message="stop operation failed",
        )


@router.post("/api/v1/control/pause")
async def api_v1_control_pause(data: ControlRequest):
    request_id = _next_request_id(data.request_id)
    try:
        out = await _get_facade().pause(data.device_id, request_id=request_id)
        return _api_ok(
            {k: v for k, v in out.items() if k != REQUEST_ID}, request_id=request_id
        )
    except Exception as exc:
        return _map_public_endpoint_exception(
            exc,
            request_id,
            default_error_code="E_PAUSE_OPERATION_FAILED",
            default_stage="dispatch",
            default_message="pause operation failed",
        )


@router.post("/api/v1/control/resume")
async def api_v1_control_resume(data: ControlRequest):
    request_id = _next_request_id(data.request_id)
    try:
        out = await _get_facade().resume(data.device_id, request_id=request_id)
        return _api_ok(
            {k: v for k, v in out.items() if k != REQUEST_ID}, request_id=request_id
        )
    except Exception as exc:
        return _map_public_endpoint_exception(
            exc,
            request_id,
            default_error_code="E_RESUME_OPERATION_FAILED",
            default_stage="dispatch",
            default_message="resume operation failed",
        )


@router.post("/api/v1/control/tts")
async def api_v1_control_tts(data: TtsRequest):
    request_id = _next_request_id(data.request_id)
    try:
        out = await _get_facade().tts(data.device_id, data.text, request_id=request_id)
        return _api_ok(
            {k: v for k, v in out.items() if k != REQUEST_ID}, request_id=request_id
        )
    except Exception as exc:
        return _map_public_endpoint_exception(
            exc,
            request_id,
            default_error_code="E_TTS_OPERATION_FAILED",
            default_stage="dispatch",
            default_message="tts operation failed",
        )


@router.post("/api/v1/control/volume")
async def api_v1_control_volume(data: VolumeRequest):
    request_id = _next_request_id(data.request_id)
    try:
        out = await _get_facade().set_volume(
            data.device_id, int(data.volume), request_id=request_id
        )
        return _api_ok(
            {k: v for k, v in out.items() if k != REQUEST_ID}, request_id=request_id
        )
    except Exception as exc:
        return _map_public_endpoint_exception(
            exc,
            request_id,
            default_error_code="E_VOLUME_OPERATION_FAILED",
            default_stage="dispatch",
            default_message="volume operation failed",
        )


@router.post("/api/v1/control/probe")
async def api_v1_control_probe(data: ControlRequest):
    request_id = _next_request_id(data.request_id)
    try:
        out = await _get_facade().probe(data.device_id, request_id=request_id)
        return _api_ok(
            {k: v for k, v in out.items() if k != REQUEST_ID}, request_id=request_id
        )
    except Exception as exc:
        return _map_public_endpoint_exception(
            exc,
            request_id,
            default_error_code="E_PROBE_OPERATION_FAILED",
            default_stage="dispatch",
            default_message="probe operation failed",
        )


@router.get("/api/v1/devices")
async def api_v1_devices():
    request_id = _next_request_id(None)
    try:
        devices = await _get_xiaomusic().getalldevices()
        rows = [
            _normalize_device(item)
            for item in (devices or [])
            if isinstance(item, dict)
        ]
        return _api_ok({"devices": rows}, request_id=request_id)
    except Exception as exc:
        return _map_structured_endpoint_exception(
            exc,
            request_id,
            default_error_code="E_DEVICES_QUERY_FAILED",
            default_stage="system",
            default_message="devices query failed",
        )


@router.get("/api/v1/sources")
async def api_v1_sources(request_id: str | None = None):
    rid = _next_request_id(request_id)
    try:
        manager = _get_source_plugin_manager()
        return _api_ok(
            {
                "registry_version": int(manager.registry_version),
                "sources": manager.describe_plugins(),
            },
            request_id=rid,
        )
    except Exception as exc:
        return _map_structured_endpoint_exception(
            exc,
            rid,
            default_error_code="E_SOURCES_QUERY_FAILED",
            default_stage="system",
            default_message="sources query failed",
        )


@router.post("/api/v1/sources/reload")
async def api_v1_sources_reload(request_id: str | None = None):
    rid = _next_request_id(request_id)
    try:
        manager = _get_source_plugin_manager()
        return _api_ok(manager.reload_summary(), request_id=rid)
    except Exception as exc:
        return _map_structured_endpoint_exception(
            exc,
            rid,
            default_error_code="E_SOURCES_RELOAD_FAILED",
            default_stage="system",
            default_message="sources reload failed",
        )


@router.post("/api/v1/sources/upload")
async def api_v1_sources_upload(
    file: UploadFile = File(...), request_id: str | None = None
):
    rid = _next_request_id(request_id)
    try:
        content = await file.read()
        manager = _get_source_plugin_manager()
        item = manager.upload_plugin(file.filename or "", content)
        return _api_ok(item, request_id=rid)
    except Exception as exc:
        return _map_structured_endpoint_exception(
            exc,
            rid,
            default_error_code="E_SOURCES_UPLOAD_FAILED",
            default_stage="system",
            default_message="sources upload failed",
        )


@router.delete("/api/v1/sources/{name}")
async def api_v1_sources_delete(name: str, request_id: str | None = None):
    rid = _next_request_id(request_id)
    try:
        manager = _get_source_plugin_manager()
        result = manager.uninstall_plugin(name)
        return _api_ok(result, request_id=rid)
    except Exception as exc:
        return _map_structured_endpoint_exception(
            exc,
            rid,
            default_error_code="E_SOURCES_DELETE_FAILED",
            default_stage="system",
            default_message="sources delete failed",
        )


@router.put("/api/v1/sources/{name}/enable")
async def api_v1_sources_enable(name: str, request_id: str | None = None):
    rid = _next_request_id(request_id)
    try:
        manager = _get_source_plugin_manager()
        item = manager.enable_plugin(name)
        return _api_ok(item, request_id=rid)
    except Exception as exc:
        return _map_structured_endpoint_exception(
            exc,
            rid,
            default_error_code="E_SOURCES_ENABLE_FAILED",
            default_stage="system",
            default_message="sources enable failed",
        )


@router.put("/api/v1/sources/{name}/disable")
async def api_v1_sources_disable(name: str, request_id: str | None = None):
    rid = _next_request_id(request_id)
    try:
        manager = _get_source_plugin_manager()
        item = manager.disable_plugin(name)
        return _api_ok(item, request_id=rid)
    except Exception as exc:
        return _map_structured_endpoint_exception(
            exc,
            rid,
            default_error_code="E_SOURCES_DISABLE_FAILED",
            default_stage="system",
            default_message="sources disable failed",
        )


@router.get("/api/v1/system/status")
async def api_v1_system_status():
    request_id = _next_request_id(None)
    try:
        devices = await _get_xiaomusic().getalldevices()
        return _api_ok(
            {
                "status": "ok",
                "version": __version__,
                "devices_count": len(devices or []),
            },
            request_id=request_id,
        )
    except Exception as exc:
        return _map_structured_endpoint_exception(
            exc,
            request_id,
            default_error_code="E_SYSTEM_STATUS_FAILED",
            default_stage="system",
            default_message="system status query failed",
        )


@router.get("/api/v1/system/settings")
async def api_v1_system_settings(request_id: str | None = None):
    rid = _next_request_id(request_id)
    try:
        return _api_ok(await _settings_snapshot(include_devices=True), request_id=rid)
    except Exception as exc:
        return _map_structured_endpoint_exception(
            exc,
            rid,
            default_error_code="E_SYSTEM_SETTINGS_QUERY_FAILED",
            default_stage="system",
            default_message="system settings query failed",
        )


@router.post("/api/v1/system/settings")
async def api_v1_system_settings_save(data: SystemSettingsSaveRequest):
    rid = _next_request_id(data.request_id)
    try:
        settings = dict(data.settings or {})
        settings["mi_did"] = ",".join(
            [str(item).strip() for item in data.device_ids if str(item).strip()]
        )

        config_obj = _get_xiaomusic().getconfig()
        if settings.get("httpauth_password") in {"******", ""}:
            settings["httpauth_password"] = config_obj.httpauth_password
        if settings.get("jellyfin_api_key") in {"******", ""}:
            settings["jellyfin_api_key"] = config_obj.jellyfin_api_key

        await _get_xiaomusic().saveconfig(settings)

        from xiaomusic.api.app import app
        from xiaomusic.api.dependencies import reset_http_server

        reset_http_server(app)
        return _api_ok({"status": "ok", "saved": True}, request_id=rid)
    except Exception as exc:
        return _map_structured_endpoint_exception(
            exc,
            rid,
            default_error_code="E_SYSTEM_SETTINGS_SAVE_FAILED",
            default_stage="system",
            default_message="system settings save failed",
        )


@router.post("/api/v1/system/settings/item")
async def api_v1_system_settings_item(data: SystemSettingItemUpdateRequest):
    rid = _next_request_id(data.request_id)
    try:
        key = str(data.key or "").strip()
        if not key:
            raise _bad_request(rid, "key is required", field="key")

        config_obj = _get_xiaomusic().getconfig()
        update_data = {key: data.value}
        has_http_config_changed = config_obj.is_http_server_config(key)
        config_obj.update_config(update_data)
        _get_xiaomusic().save_cur_config()
        if has_http_config_changed:
            from xiaomusic.api.app import app
            from xiaomusic.api.dependencies import reset_http_server

            reset_http_server(app)

        return _api_ok({"status": "ok", "updated": True, "key": key}, request_id=rid)
    except Exception as exc:
        return _map_structured_endpoint_exception(
            exc,
            rid,
            default_error_code="E_SYSTEM_SETTING_UPDATE_FAILED",
            default_stage="system",
            default_message="system setting update failed",
        )


@router.get("/api/v1/auth/status")
async def api_v1_auth_status(request_id: str | None = None):
    rid = _next_request_id(request_id)
    try:
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
        return _api_ok(data, request_id=rid)
    except Exception as exc:
        return _map_api_exception(exc, rid)


# diagnostic endpoint - not in v1 whitelist, exclude from public schema
@router.get("/api/v1/debug/auth_state", include_in_schema=False)
async def api_v1_debug_auth_state():
    request_id = _next_request_id(None)
    try:
        am = getattr(_get_xiaomusic(), "auth_manager", None)
        if am is not None and hasattr(am, "auth_debug_state"):
            data = am.auth_debug_state()
        else:
            data = {
                "auth_mode": "unknown",
                "login_at": None,
                "expires_at": None,
                "ttl_remaining_seconds": None,
                "last_refresh_trigger": "",
                "last_auth_error": "",
            }
        return _api_ok(data, request_id=request_id)
    except Exception as exc:
        return _map_api_exception(exc, request_id)


# diagnostic endpoint - not in v1 whitelist, exclude from public schema
@router.get("/api/v1/debug/auth_recovery_state", include_in_schema=False)
async def api_v1_debug_auth_recovery_state():
    request_id = _next_request_id(None)
    try:
        am = getattr(_get_xiaomusic(), "auth_manager", None)
        if am is not None and hasattr(am, "auth_recovery_debug_state"):
            data = am.auth_recovery_debug_state()
        else:
            data = {
                "last_clear_short_session": {},
                "last_login_exchange": {},
                "last_runtime_rebind": {},
                "last_playback_capability_verify": {},
            }
        return _api_ok(data, request_id=request_id)
    except Exception as exc:
        return _map_api_exception(exc, request_id)


# diagnostic endpoint - not in v1 whitelist, exclude from public schema
@router.get("/api/v1/debug/miaccount_login_trace", include_in_schema=False)
async def api_v1_debug_miaccount_login_trace():
    request_id = _next_request_id(None)
    try:
        am = getattr(_get_xiaomusic(), "auth_manager", None)
        if am is not None and hasattr(am, "miaccount_login_trace_debug_state"):
            data = am.miaccount_login_trace_debug_state()
        else:
            data = {
                "login_input_snapshot": {},
                "login_http_exchange": {},
                "login_response_parse": {},
                "token_writeback": {},
                "post_login_runtime_seed": {},
            }
        return _api_ok(data, request_id=request_id)
    except Exception as exc:
        return _map_api_exception(exc, request_id)


# diagnostic endpoint - not in v1 whitelist, exclude from public schema
@router.get("/api/v1/debug/auth_rebuild_state", include_in_schema=False)
async def api_v1_debug_auth_rebuild_state():
    request_id = _next_request_id(None)
    try:
        am = getattr(_get_xiaomusic(), "auth_manager", None)
        if am is not None and hasattr(am, "auth_rebuild_debug_state"):
            data = am.auth_rebuild_debug_state()
        else:
            data = {
                "last_clear_short_session": {},
                "last_rebuild_short_session": {},
                "last_runtime_rebind": {},
                "last_verify": {},
            }
        return _api_ok(data, request_id=request_id)
    except Exception as exc:
        return _map_api_exception(exc, request_id)


# diagnostic endpoint - not in v1 whitelist, exclude from public schema
@router.get("/api/v1/debug/auth_runtime_reload_state", include_in_schema=False)
async def api_v1_debug_auth_runtime_reload_state():
    request_id = _next_request_id(None)
    try:
        am = getattr(_get_xiaomusic(), "auth_manager", None)
        if am is not None and hasattr(am, "auth_runtime_reload_debug_state"):
            data = am.auth_runtime_reload_debug_state()
        else:
            data = {
                "last_reload_runtime": {},
            }
        return _api_ok(data, request_id=request_id)
    except Exception as exc:
        return _map_api_exception(exc, request_id)


@router.post("/api/v1/control/previous")
async def api_v1_control_previous(data: ControlRequest):
    request_id = _next_request_id(data.request_id)
    try:
        out = await _get_facade().previous(
            device_id=data.device_id, request_id=request_id
        )
        return _api_ok(
            {k: v for k, v in out.items() if k != REQUEST_ID}, request_id=request_id
        )
    except Exception as exc:
        return _map_public_endpoint_exception(
            exc,
            request_id,
            default_error_code="E_PREVIOUS_OPERATION_FAILED",
            default_stage="dispatch",
            default_message="previous operation failed",
        )


@router.post("/api/v1/control/next")
async def api_v1_control_next(data: ControlRequest):
    request_id = _next_request_id(data.request_id)
    try:
        out = await _get_facade().next(device_id=data.device_id, request_id=request_id)
        return _api_ok(
            {k: v for k, v in out.items() if k != REQUEST_ID}, request_id=request_id
        )
    except Exception as exc:
        return _map_public_endpoint_exception(
            exc,
            request_id,
            default_error_code="E_NEXT_OPERATION_FAILED",
            default_stage="dispatch",
            default_message="next operation failed",
        )


@router.post("/api/v1/control/play-mode")
async def api_v1_control_play_mode(data: PlayModeRequest):
    request_id = _next_request_id(data.request_id)
    try:
        play_mode = str(data.play_mode or "").strip().lower()
        if play_mode not in _PLAY_MODE_HANDLERS:
            raise _bad_request(
                request_id,
                "invalid play_mode",
                field="play_mode",
                allowed=sorted(_PLAY_MODE_HANDLERS.keys()),
            )
        xm = _require_device(data.device_id, request_id)
        handler = getattr(xm, _PLAY_MODE_HANDLERS[play_mode])
        await handler(did=data.device_id, dotts=False, refresh_playlist=True)
        return _api_ok(
            {"status": "ok", DEVICE_ID: data.device_id, "play_mode": play_mode},
            request_id=request_id,
        )
    except Exception as exc:
        return _map_structured_endpoint_exception(
            exc,
            request_id,
            default_error_code="E_PLAY_MODE_OPERATION_FAILED",
            default_stage="xiaomi",
            default_message="play_mode operation failed",
        )


@router.post("/api/v1/control/shutdown-timer")
async def api_v1_control_shutdown_timer(data: ShutdownTimerRequest):
    request_id = _next_request_id(data.request_id)
    try:
        xm = _require_device(data.device_id, request_id)
        await xm.stop_after_minute(did=data.device_id, arg1=data.minutes)
        return _api_ok(
            {"status": "ok", DEVICE_ID: data.device_id, "minutes": int(data.minutes)},
            request_id=request_id,
        )
    except Exception as exc:
        return _map_structured_endpoint_exception(
            exc,
            request_id,
            default_error_code="E_SHUTDOWN_TIMER_OPERATION_FAILED",
            default_stage="xiaomi",
            default_message="shutdown timer operation failed",
        )


@router.post("/api/v1/library/favorites/add")
async def api_v1_library_favorites_add(data: FavoritesRequest):
    request_id = _next_request_id(data.request_id)
    try:
        xm = _require_device(data.device_id, request_id)
        await xm.add_to_favorites(did=data.device_id, arg1=data.track_name)
        return _api_ok(
            {"status": "ok", DEVICE_ID: data.device_id, "track_name": data.track_name},
            request_id=request_id,
        )
    except Exception as exc:
        return _map_structured_endpoint_exception(
            exc,
            request_id,
            default_error_code="E_FAVORITES_ADD_FAILED",
            default_stage="library",
            default_message="favorites add failed",
        )


@router.post("/api/v1/library/favorites/remove")
async def api_v1_library_favorites_remove(data: FavoritesRequest):
    request_id = _next_request_id(data.request_id)
    try:
        xm = _require_device(data.device_id, request_id)
        await xm.del_from_favorites(did=data.device_id, arg1=data.track_name)
        return _api_ok(
            {"status": "ok", DEVICE_ID: data.device_id, "track_name": data.track_name},
            request_id=request_id,
        )
    except Exception as exc:
        return _map_structured_endpoint_exception(
            exc,
            request_id,
            default_error_code="E_FAVORITES_REMOVE_FAILED",
            default_stage="library",
            default_message="favorites remove failed",
        )


@router.post("/api/v1/library/refresh")
async def api_v1_library_refresh(data: LibraryRefreshRequest):
    request_id = _next_request_id(data.request_id)
    try:
        xm = _get_xiaomusic()
        await xm.gen_music_list()
        return _api_ok({"status": "ok", "refreshed": True}, request_id=request_id)
    except Exception as exc:
        return _map_structured_endpoint_exception(
            exc,
            request_id,
            default_error_code="E_LIBRARY_REFRESH_FAILED",
            default_stage="library",
            default_message="library refresh failed",
        )


@router.get("/api/v1/library/playlists")
async def api_v1_library_playlists(request_id: str | None = None):
    rid = _next_request_id(request_id)
    try:
        from xiaomusic.playback.facade import build_track_id

        raw_playlists = _get_xiaomusic().music_library.get_music_list()
        from xiaomusic.utils.text_utils import custom_sort_key

        music_library = getattr(_get_xiaomusic(), "music_library", None)
        all_music = getattr(music_library, "all_music", {}) if music_library else {}

        playlists_with_ids = {}
        if isinstance(raw_playlists, dict):
            for name, songs in raw_playlists.items():
                if isinstance(songs, list):
                    sorted_songs = sorted(songs, key=custom_sort_key)
                    items = []
                    for idx, title in enumerate(sorted_songs):
                        identity_hint = ""
                        if isinstance(all_music, dict):
                            identity_hint = str(all_music.get(title) or "").strip()
                        items.append(
                            {
                                "id": build_track_id(name, idx, title, identity_hint=identity_hint),
                                "title": title,
                            }
                        )
                    playlists_with_ids[name] = items
                else:
                    playlists_with_ids[name] = songs
        return _api_ok(
            {"playlists": playlists_with_ids},
            request_id=rid,
        )
    except Exception as exc:
        return _map_structured_endpoint_exception(
            exc,
            rid,
            default_error_code="E_LIBRARY_PLAYLISTS_QUERY_FAILED",
            default_stage="library",
            default_message="library playlists query failed",
        )


@router.get("/api/v1/library/music-info")
async def api_v1_library_music_info(
    name: str = Query(""), request_id: str | None = None
):
    rid = _next_request_id(request_id)
    try:
        music_name = str(name or "").strip()
        if not music_name:
            raise _bad_request(rid, "name is required", field="name")
        url, _ = await _get_xiaomusic().music_library.get_music_url(music_name)
        tags = await _get_xiaomusic().music_library.get_music_tags(music_name)
        duration_seconds = 0.0
        if isinstance(tags, dict):
            try:
                duration_seconds = float(tags.get("duration") or 0)
            except Exception:
                duration_seconds = 0.0
        return _api_ok(
            {
                "name": music_name,
                "url": url,
                "duration_seconds": duration_seconds,
            },
            request_id=rid,
        )
    except Exception as exc:
        return _map_structured_endpoint_exception(
            exc,
            rid,
            default_error_code="E_LIBRARY_MUSIC_INFO_FAILED",
            default_stage="library",
            default_message="library music-info query failed",
        )


@router.get("/api/v1/search/online")
async def api_v1_search_online(
    keyword: str = Query(""),
    plugin: str = Query("all"),
    page: int = Query(1),
    limit: int = Query(20),
    request_id: str | None = None,
):
    rid = _next_request_id(request_id)
    try:
        query = str(keyword or "").strip()
        if not query:
            raise _bad_request(rid, "keyword is required", field="keyword")
        result = await _get_xiaomusic().get_music_list_online(
            keyword=query,
            plugin=str(plugin or "all"),
            page=int(page),
            limit=int(limit),
        )
        if isinstance(result, dict) and result.get("success") is False:
            return _api_response(
                10000,
                str(result.get("error") or "online search failed"),
                {
                    "error_code": "E_SEARCH_ONLINE_FAILED",
                    "stage": "system",
                },
                rid,
            )

        items: list[dict[str, Any]] = []
        for raw in list((result or {}).get("data") or []):
            if not isinstance(raw, dict):
                continue
            items.append(
                {
                    "name": str(raw.get("name") or ""),
                    "title": str(raw.get("title") or raw.get("name") or ""),
                    "artist": str(raw.get("artist") or ""),
                }
            )

        return _api_ok(
            {
                "items": items,
                "total": int((result or {}).get("total") or len(items)),
            },
            request_id=rid,
        )
    except Exception as exc:
        return _map_structured_endpoint_exception(
            exc,
            rid,
            default_error_code="E_SEARCH_ONLINE_FAILED",
            default_stage="system",
            default_message="online search failed",
        )


# diagnostic endpoint - not in v1 whitelist, exclude from public schema
@router.get("/api/v1/debug/auth_short_session_rebuild_state", include_in_schema=False)
async def api_v1_debug_auth_short_session_rebuild_state():
    request_id = _next_request_id(None)
    try:
        am = getattr(_get_xiaomusic(), "auth_manager", None)
        if am is not None and hasattr(am, "auth_short_session_rebuild_debug_state"):
            data = am.auth_short_session_rebuild_debug_state()
        else:
            data = {
                "last_short_session_rebuild": {},
                "last_persistent_auth_relogin": {},
                "last_runtime_rebind": {},
                "last_verify": {},
                "last_auth_recovery_flow": {},
                "last_locked_transition": {},
            }
        return _api_ok(data, request_id=request_id)
    except Exception as exc:
        return _map_api_exception(exc, request_id)


@router.get("/api/v1/player/state")
async def api_v1_player_state(
    device_id: str = Query(..., min_length=1), request_id: str | None = None
):
    rid = _next_request_id(request_id)
    try:
        snapshot = await _get_facade().build_player_state_snapshot(device_id=device_id)
        data = {
            DEVICE_ID: str(snapshot.get("device_id", device_id)),
            "revision": int(snapshot.get("revision", 0)),
            "play_session_id": str(snapshot.get("play_session_id") or ""),
            "transport_state": str(snapshot.get("transport_state") or "idle"),
            "track": snapshot.get("track"),
            "context": snapshot.get("context"),
            "position_ms": int(snapshot.get("position_ms", 0)),
            "duration_ms": int(snapshot.get("duration_ms", 0)),
            "volume": int(snapshot.get("volume", 0) or 0),
            "snapshot_at_ms": int(snapshot.get("snapshot_at_ms", 0)),
            "is_playing": bool(snapshot.get("transport_state") == "playing"),
            "cur_music": str((snapshot.get("track") or {}).get("title", "") or ""),
            "offset": max(0, int((snapshot.get("position_ms", 0) or 0) / 1000)),
            "duration": max(0, int((snapshot.get("duration_ms", 0) or 0) / 1000)),
            "current_track_id": str((snapshot.get("track") or {}).get("id", "") or ""),
            "current_index": ((snapshot.get("context") or {}).get("current_index")),
            "context_type": (
                "playlist" if (snapshot.get("context") or {}).get("id") else None
            ),
            "context_id": (snapshot.get("context") or {}).get("id"),
            "context_name": (snapshot.get("context") or {}).get("name"),
        }
        return _api_ok(data, request_id=rid)
    except Exception as exc:
        return _map_public_endpoint_exception(
            exc,
            rid,
            default_error_code="E_PLAYER_STATE_FAILED",
            default_stage="system",
            default_message="player state query failed",
        )


_player_stream_subscribers: dict[str, set[asyncio.Queue[bytes]]] = {}
_player_stream_sub_lock = asyncio.Lock()


async def _push_player_state_event(device_id: str) -> None:
    """Push current player state to all subscribers of this device."""
    from xiaomusic.events import PLAYER_STATE_CHANGED

    facade = _get_facade()
    try:
        snapshot = await facade.build_player_state_snapshot(device_id)
    except Exception:
        return
    payload = json.dumps(snapshot, ensure_ascii=False)
    event = (
        f"event: player_state\nid: {snapshot.get('revision', 0)}\ndata: {payload}\n\n"
    )
    event_bytes = event.encode("utf-8")
    async with _player_stream_sub_lock:
        subscribers = _player_stream_subscribers.get(device_id)
        if subscribers:
            for queue in list(subscribers):
                try:
                    queue.put_nowait(event_bytes)
                except Exception:
                    pass


@router.get("/api/v1/player/stream")
async def api_v1_player_stream(
    request: Request,
    device_id: str = Query(..., min_length=1),
):
    xm = _get_xiaomusic()
    if not xm.did_exist(device_id):
        rid = uuid4().hex[:16]
        return JSONResponse(
            status_code=404,
            content=_api_response(
                40004,
                "device not found",
                {"error_code": "E_DEVICE_NOT_FOUND", "stage": "request"},
                rid,
            ),
        )

    xiaomusic_obj = _get_xiaomusic()
    event_bus = getattr(xiaomusic_obj, "event_bus", None)

    q: asyncio.Queue[bytes] = asyncio.Queue()

    async with _player_stream_sub_lock:
        if device_id not in _player_stream_subscribers:
            _player_stream_subscribers[device_id] = set()
        _player_stream_subscribers[device_id].add(q)

    async def _remove_sub(did: str, queue: asyncio.Queue[bytes]):
        async with _player_stream_sub_lock:
            subs = _player_stream_subscribers.get(did)
            if subs and queue in subs:
                subs.discard(queue)
            if not subs:
                _player_stream_subscribers.pop(did, None)

    async def on_state_changed(**kwargs):
        event_device_id = kwargs.get("device_id")
        if event_device_id != device_id:
            return
        await _push_player_state_event(device_id)

    if event_bus is not None:
        from xiaomusic.events import PLAYER_STATE_CHANGED

        try:
            event_bus.subscribe(PLAYER_STATE_CHANGED, on_state_changed)
        except Exception:
            pass

    async def stream_closed():
        if event_bus is not None:
            from xiaomusic.events import PLAYER_STATE_CHANGED

            try:
                event_bus.unsubscribe(PLAYER_STATE_CHANGED, on_state_changed)
            except Exception:
                pass
        await _remove_sub(device_id, q)

    async def event_generator():
        try:
            facade = _get_facade()
            snapshot = await facade.build_player_state_snapshot(device_id)
            revision = snapshot.get("revision", 0)
            payload = json.dumps(snapshot, ensure_ascii=False)
            initial = (
                f"retry: 5000\n\n"
                f"event: player_state\n"
                f"id: {revision}\n"
                f"data: {payload}\n\n"
            )
            yield initial.encode("utf-8")

            last_heartbeat_ts = asyncio.get_event_loop().time()
            heartbeat_interval = 15.0

            while True:
                try:
                    timeout = max(
                        0.1,
                        heartbeat_interval
                        - (asyncio.get_event_loop().time() - last_heartbeat_ts),
                    )
                    raw_bytes = await asyncio.wait_for(q.get(), timeout=timeout)
                    last_heartbeat_ts = asyncio.get_event_loop().time()
                    yield raw_bytes
                except asyncio.TimeoutError:
                    now = asyncio.get_event_loop().time()
                    if now - last_heartbeat_ts >= heartbeat_interval:
                        yield b": heartbeat\n\n"
                        last_heartbeat_ts = now
                except asyncio.CancelledError:
                    break
                except Exception:
                    break
        finally:
            await stream_closed()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
