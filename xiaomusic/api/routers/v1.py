from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, File, Query, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from xiaomusic import __version__
from xiaomusic.api.api_error import ApiError
from xiaomusic.api.models import (
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
from xiaomusic.api.routers.v1_shared import (
    _api_ok,
    _api_response,
    _bad_request,
    _get_facade,
    _map_api_exception,
    _map_public_endpoint_exception,
    _map_structured_endpoint_exception,
    _next_request_id,
    _runtime_auth_ready_v1,
    auth_status_v1_svc,
    configured_device_snapshot,
    diag_query_svc,
    sources_delete_svc,
    sources_disable_svc,
    sources_enable_svc,
    sources_list_svc,
    sources_reload_svc,
    sources_upload_svc,
)
from xiaomusic.api.routers.v1_shared import (
    _get_xiaomusic as _v1_shared_get_xiaomusic,
)
from xiaomusic.constants.api_fields import (
    DEVICE_ID,
    OPTIONS,
    QUERY,
    REQUEST_ID,
    SOURCE_HINT,
)
from xiaomusic.core.models import PlayOptions
from xiaomusic.core.source.online_plugin_reference_store import (
    OnlinePluginReferenceStore,
)

# Keep _get_xiaomusic and _require_device as local module-level functions
# so tests can monkeypatch them directly on the v1 module.
# _get_facade is imported from v1_shared (single source of truth — no duplicate facade).


def _get_xiaomusic():
    return _v1_shared_get_xiaomusic()


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


router = APIRouter()
LOG = logging.getLogger("xiaomusic.api.v1")
SERVER_INSTANCE_ID = uuid4().hex
_PLAY_MODE_HANDLERS = {
    "one": "set_play_type_one",
    "all": "set_play_type_all",
    "random": "set_play_type_rnd",
    "single": "set_play_type_sin",
    "sequence": "set_play_type_seq",
}


def _public_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Attach the process epoch without changing facade/runtime state."""
    return {**snapshot, "server_instance_id": SERVER_INSTANCE_ID}


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


async def _settings_snapshot(include_devices: bool) -> dict[str, Any]:
    xm = _get_xiaomusic()
    config_data = asdict(xm.getconfig())
    config_data["httpauth_password"] = "******"
    _service = getattr(xm, "online_music_service", None)
    _jf = getattr(_service, "jellyfin_client", None)
    _jf_status = _jf.login_status() if _jf is not None else {}
    config_data["jellyfin_logged_in"] = bool(_jf_status.get("logged_in"))
    config_data["jellyfin_username"] = str(_jf_status.get("username") or "")

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
        devices = configured_device_snapshot(xm)

    return {
        "settings": config_data,
        "device_ids": device_ids,
        "devices": devices,
    }


@router.post("/api/v1/play")
async def api_v1_play(data: PlayRequest):
    request_id = _next_request_id(data.request_id)
    raw_options = getattr(data, OPTIONS, None)
    options = PlayOptions.from_payload(raw_options.model_dump() if raw_options is not None else None)
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
            snapshot = _public_snapshot(
                await facade.build_player_state_snapshot(
                    device_id=out.get(DEVICE_ID, getattr(data, DEVICE_ID))
                )
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
    raw_options = getattr(data, OPTIONS, None)
    options = PlayOptions.from_payload(raw_options.model_dump() if raw_options is not None else None)
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
        rows = configured_device_snapshot(_get_xiaomusic())
        return _api_ok({"devices": rows}, request_id=request_id)
    except Exception as exc:
        return _map_structured_endpoint_exception(
            exc,
            request_id,
            default_error_code="E_DEVICES_QUERY_FAILED",
            default_stage="system",
            default_message="devices query failed",
        )


@router.get("/api/v1/sources", include_in_schema=False, deprecated=True)
async def api_v1_sources(request_id: str | None = None):
    LOG.warning("deprecated route called: GET /api/v1/sources (use /api/admin/v1/sources)")
    rid = _next_request_id(request_id)
    try:
        return _api_ok(sources_list_svc(), request_id=rid)
    except Exception as exc:
        return _map_structured_endpoint_exception(
            exc, rid, default_error_code="E_SOURCES_QUERY_FAILED",
            default_stage="system", default_message="sources query failed",
        )


@router.post("/api/v1/sources/reload", include_in_schema=False, deprecated=True)
async def api_v1_sources_reload(request_id: str | None = None):
    LOG.warning("deprecated route called: POST /api/v1/sources/reload (use /api/admin/v1/sources/reload)")
    rid = _next_request_id(request_id)
    try:
        return _api_ok(sources_reload_svc(), request_id=rid)
    except Exception as exc:
        return _map_structured_endpoint_exception(
            exc, rid, default_error_code="E_SOURCES_RELOAD_FAILED",
            default_stage="system", default_message="sources reload failed",
        )


@router.post("/api/v1/sources/upload", include_in_schema=False, deprecated=True)
async def api_v1_sources_upload(
    file: UploadFile = File(...), request_id: str | None = None
):
    LOG.warning("deprecated route called: POST /api/v1/sources/upload (use /api/admin/v1/sources/upload)")
    rid = _next_request_id(request_id)
    try:
        content = await file.read()
        return _api_ok(sources_upload_svc(file.filename or "", content), request_id=rid)
    except Exception as exc:
        return _map_structured_endpoint_exception(
            exc, rid, default_error_code="E_SOURCES_UPLOAD_FAILED",
            default_stage="system", default_message="sources upload failed",
        )


@router.delete("/api/v1/sources/{name}", include_in_schema=False, deprecated=True)
async def api_v1_sources_delete(name: str, request_id: str | None = None):
    LOG.warning("deprecated route called: DELETE /api/v1/sources/{name} (use /api/admin/v1/sources/{name})")
    rid = _next_request_id(request_id)
    try:
        return _api_ok(sources_delete_svc(name), request_id=rid)
    except Exception as exc:
        return _map_structured_endpoint_exception(
            exc, rid, default_error_code="E_SOURCES_DELETE_FAILED",
            default_stage="system", default_message="sources delete failed",
        )


@router.put("/api/v1/sources/{name}/enable", include_in_schema=False, deprecated=True)
async def api_v1_sources_enable(name: str, request_id: str | None = None):
    LOG.warning("deprecated route called: PUT /api/v1/sources/{name}/enable (use /api/admin/v1/sources/{name}/enable)")
    rid = _next_request_id(request_id)
    try:
        return _api_ok(sources_enable_svc(name), request_id=rid)
    except Exception as exc:
        return _map_structured_endpoint_exception(
            exc, rid, default_error_code="E_SOURCES_ENABLE_FAILED",
            default_stage="system", default_message="sources enable failed",
        )


@router.put("/api/v1/sources/{name}/disable", include_in_schema=False, deprecated=True)
async def api_v1_sources_disable(name: str, request_id: str | None = None):
    LOG.warning("deprecated route called: PUT /api/v1/sources/{name}/disable (use /api/admin/v1/sources/{name}/disable)")
    rid = _next_request_id(request_id)
    try:
        return _api_ok(sources_disable_svc(name), request_id=rid)
    except Exception as exc:
        return _map_structured_endpoint_exception(
            exc, rid, default_error_code="E_SOURCES_DISABLE_FAILED",
            default_stage="system", default_message="sources disable failed",
        )


@router.get("/api/v1/system/status")
async def api_v1_system_status():
    request_id = _next_request_id(None)
    try:
        xm = _get_xiaomusic()
        rows = configured_device_snapshot(xm)
        return _api_ok(
            {
                "status": "ok",
                "version": __version__,
                "devices_count": len(rows),
                "runtime_devices_count": len(
                    getattr(getattr(xm, "device_manager", None), "devices", {}) or {}
                ),
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
        for _key in (
            "jellyfin_password",
            "jellyfin_api_key",
            "jellyfin_user_id",
            "jellyfin_logged_in",
            "jellyfin_username",
        ):
            settings.pop(_key, None)

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


@router.get("/api/v1/auth/status", include_in_schema=False, deprecated=True)
async def api_v1_auth_status(request_id: str | None = None):
    LOG.warning("deprecated route called: GET /api/v1/auth/status (use /api/admin/v1/auth/status)")
    rid = _next_request_id(request_id)
    try:
        return _api_ok(await auth_status_v1_svc(), request_id=rid)
    except Exception as exc:
        return _map_api_exception(exc, rid)


# diagnostic endpoint - not in v1 whitelist, exclude from public schema
@router.get("/api/v1/debug/auth_state", include_in_schema=False, deprecated=True)
async def api_v1_debug_auth_state():
    LOG.warning("deprecated route called: GET /api/v1/debug/auth_state (use /api/internal/diagnostics/auth_state)")
    request_id = _next_request_id(None)
    try:
        return _api_ok(diag_query_svc("auth_state"), request_id=request_id)
    except Exception as exc:
        return _map_api_exception(exc, request_id)


# diagnostic endpoint - not in v1 whitelist, exclude from public schema
@router.get("/api/v1/debug/auth_recovery_state", include_in_schema=False, deprecated=True)
async def api_v1_debug_auth_recovery_state():
    LOG.warning("deprecated route called: GET /api/v1/debug/auth_recovery_state (use /api/internal/diagnostics/auth_recovery_state)")
    request_id = _next_request_id(None)
    try:
        return _api_ok(diag_query_svc("auth_recovery_state"), request_id=request_id)
    except Exception as exc:
        return _map_api_exception(exc, request_id)


# diagnostic endpoint - not in v1 whitelist, exclude from public schema
@router.get("/api/v1/debug/miaccount_login_trace", include_in_schema=False, deprecated=True)
async def api_v1_debug_miaccount_login_trace():
    LOG.warning("deprecated route called: GET /api/v1/debug/miaccount_login_trace (use /api/internal/diagnostics/miaccount_login_trace)")
    request_id = _next_request_id(None)
    try:
        return _api_ok(diag_query_svc("miaccount_login_trace"), request_id=request_id)
    except Exception as exc:
        return _map_api_exception(exc, request_id)


# diagnostic endpoint - not in v1 whitelist, exclude from public schema
@router.get("/api/v1/debug/auth_rebuild_state", include_in_schema=False, deprecated=True)
async def api_v1_debug_auth_rebuild_state():
    LOG.warning("deprecated route called: GET /api/v1/debug/auth_rebuild_state (use /api/internal/diagnostics/auth_rebuild_state)")
    request_id = _next_request_id(None)
    try:
        return _api_ok(diag_query_svc("auth_rebuild_state"), request_id=request_id)
    except Exception as exc:
        return _map_api_exception(exc, request_id)


# diagnostic endpoint - not in v1 whitelist, exclude from public schema
@router.get("/api/v1/debug/auth_runtime_reload_state", include_in_schema=False, deprecated=True)
async def api_v1_debug_auth_runtime_reload_state():
    LOG.warning("deprecated route called: GET /api/v1/debug/auth_runtime_reload_state (use /api/internal/diagnostics/auth_runtime_reload_state)")
    request_id = _next_request_id(None)
    try:
        return _api_ok(diag_query_svc("auth_runtime_reload_state"), request_id=request_id)
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
        target = str(data.entity_id or data.track_name or "").strip()
        await xm.add_to_favorites(did=data.device_id, arg1=target)
        return _api_ok(
            {
                "status": "ok",
                DEVICE_ID: data.device_id,
                "track_name": data.track_name,
                "entity_id": data.entity_id,
            },
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
        target = str(data.entity_id or data.track_name or "").strip()
        await xm.del_from_favorites(did=data.device_id, arg1=target)
        return _api_ok(
            {
                "status": "ok",
                DEVICE_ID: data.device_id,
                "track_name": data.track_name,
                "entity_id": data.entity_id,
            },
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

        music_library = getattr(_get_xiaomusic(), "music_library", None)
        raw_playlists = _get_xiaomusic().music_library.get_music_list()
        all_music = getattr(music_library, "all_music", {}) if music_library else {}
        playlist_items = (
            music_library.get_playlist_items() if music_library and hasattr(music_library, "get_playlist_items") else {}
        )

        playlists_with_ids = {}
        if isinstance(raw_playlists, dict):
            for name, songs in raw_playlists.items():
                if isinstance(songs, list):
                    structured_items = playlist_items.get(name) if isinstance(playlist_items, dict) else None
                    items = []
                    if isinstance(structured_items, list) and structured_items:
                        members_by_legacy = {
                            str(item.get("legacy_name") or item.get("display_name") or "").strip(): item
                            for item in structured_items
                            if isinstance(item, dict)
                        }
                        for idx, title in enumerate(songs):
                            member = members_by_legacy.get(str(title or "").strip(), {})
                            identity_hint = str(
                                member.get("entity_id")
                                or member.get("source_item_id")
                                or member.get("item_id")
                                or ""
                            ).strip()
                            item_id = str(member.get("item_id") or "").strip() or build_track_id(
                                name,
                                idx,
                                str(title or ""),
                                identity_hint=identity_hint,
                            )
                            items.append(
                                {
                                    "id": item_id,
                                    "entity_id": str(member.get("entity_id") or "").strip(),
                                    "title": str(member.get("display_name") or title or ""),
                                }
                            )
                    else:
                        for idx, title in enumerate(songs):
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
    name: str = Query(""),
    entity_id: str = Query(""),
    request_id: str | None = None,
):
    rid = _next_request_id(request_id)
    try:
        music_name = str(name or "").strip()
        requested_entity_id = str(entity_id or "").strip()
        if not music_name and not requested_entity_id:
            raise _bad_request(rid, "name or entity_id is required", field="name")
        xm = _get_xiaomusic()
        music_library = xm.music_library
        if requested_entity_id:
            resolver = getattr(music_library, "get_legacy_name_for_entity", None)
            if callable(resolver):
                music_name = str(resolver(requested_entity_id) or "").strip()
        if not music_name:
            raise _bad_request(rid, "name or entity_id is required", field="name")
        if requested_entity_id and hasattr(music_library, "get_music_url_by_entity"):
            url, _ = await music_library.get_music_url_by_entity(requested_entity_id)
            tags = await music_library.get_music_tags_by_entity(requested_entity_id)
        else:
            url, _ = await music_library.get_music_url(music_name)
            tags = await music_library.get_music_tags(music_name)
        duration_seconds = 0.0
        if isinstance(tags, dict):
            try:
                duration_seconds = float(tags.get("duration") or 0)
            except Exception:
                duration_seconds = 0.0
        resolved_entity_id = requested_entity_id
        if not resolved_entity_id:
            resolver = getattr(music_library, "resolve_entity_id_by_name", None)
            if callable(resolver):
                resolved_entity_id = str(resolver(music_name) or "").strip()
        return _api_ok(
            {
                "name": music_name,
                "entity_id": resolved_entity_id,
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


def _get_online_plugin_reference_store(xiaomusic) -> OnlinePluginReferenceStore:
    store = getattr(xiaomusic, "_online_plugin_reference_store", None)
    if not isinstance(store, OnlinePluginReferenceStore):
        store = OnlinePluginReferenceStore()
        xiaomusic._online_plugin_reference_store = store
    return store


def _build_online_search_item(
    raw: dict[str, Any],
    store: OnlinePluginReferenceStore,
    enabled_js_plugins: set[str],
) -> dict[str, Any]:
    item = {
        "name": str(raw.get("name") or ""),
        "title": str(raw.get("title") or raw.get("name") or ""),
        "artist": str(raw.get("artist") or ""),
    }
    platform = str(raw.get("platform") or "").strip()
    raw_media_id = str(
        raw.get("media_id") or raw.get("id") or raw.get("songmid") or raw.get("songid") or ""
    ).strip()
    source = str(raw.get("source") or "").strip().lower()
    platform_lower = platform.lower()
    is_known_non_js_source = source in {"openapi", "jellyfin"} or platform_lower.startswith(
        ("openapi", "jellyfin")
    )
    is_enabled_js_plugin = platform in enabled_js_plugins
    if platform and raw_media_id and not is_known_non_js_source and is_enabled_js_plugin:
        token = store.put(raw)
        public_media_id = "opm_" + hashlib.sha256(
            f"{platform}\0{raw_media_id}".encode()
        ).hexdigest()[:24]
        item["play_reference"] = {
            "query": token,
            "source_hint": "online_plugin",
            "media_id": public_media_id,
            "title": item["title"],
        }
    return item


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
        xiaomusic = _get_xiaomusic()
        reference_store = _get_online_plugin_reference_store(xiaomusic)
        js_plugin_manager = getattr(xiaomusic, "js_plugin_manager", None)
        enabled_js_plugins = set()
        if js_plugin_manager is not None:
            get_enabled_plugins = getattr(js_plugin_manager, "get_enabled_plugins", None)
            if callable(get_enabled_plugins):
                enabled_js_plugins = {
                    str(name).strip()
                    for name in get_enabled_plugins()
                    if str(name).strip()
                }
        result = await xiaomusic.get_music_list_online(
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
            items.append(_build_online_search_item(raw, reference_store, enabled_js_plugins))

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
@router.get("/api/v1/debug/auth_short_session_rebuild_state", include_in_schema=False, deprecated=True)
async def api_v1_debug_auth_short_session_rebuild_state():
    LOG.warning("deprecated route called: GET /api/v1/debug/auth_short_session_rebuild_state (use /api/internal/diagnostics/auth_short_session_rebuild_state)")
    request_id = _next_request_id(None)
    try:
        return _api_ok(diag_query_svc("auth_short_session_rebuild_state"), request_id=request_id)
    except Exception as exc:
        return _map_api_exception(exc, request_id)


@router.get("/api/v1/player/state")
async def api_v1_player_state(
    device_id: str = Query(..., min_length=1), request_id: str | None = None
):
    rid = _next_request_id(request_id)
    try:
        snapshot = _public_snapshot(
            await _get_facade().build_player_state_snapshot(device_id=device_id)
        )
        data = {
            DEVICE_ID: str(snapshot.get("device_id", device_id)),
            "server_instance_id": SERVER_INSTANCE_ID,
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


@dataclass(eq=False)
class _PlayerStreamSubscriber:
    queue: asyncio.Queue[dict[str, Any]] = field(
        default_factory=lambda: asyncio.Queue(maxsize=1)
    )
    initializing: bool = True
    initial_ready: asyncio.Event = field(default_factory=asyncio.Event)
    initial_snapshot: dict[str, Any] | None = None
    initial_error: Exception | None = None


@dataclass
class _PlayerStreamChannel:
    device_id: str
    event_bus: Any
    subscribers: set[_PlayerStreamSubscriber] = field(default_factory=set)
    initial_ready: asyncio.Event = field(default_factory=asyncio.Event)
    initial_snapshot: dict[str, Any] | None = None
    initial_error: Exception | None = None
    latest_snapshot: dict[str, Any] | None = None
    build_task: asyncio.Task | None = None
    dirty: bool = False
    refresh_waiters: set[_PlayerStreamSubscriber] = field(default_factory=set)
    closed: bool = False
    cleanup_started: bool = False
    listener: Any = None

    def start(self) -> None:
        if self.closed or self.build_task is not None:
            return
        self.build_task = asyncio.create_task(self._build_loop())
        self.build_task.add_done_callback(self._consume_build_task)

    @staticmethod
    def _consume_build_task(task: asyncio.Task) -> None:
        try:
            task.exception()
        except (asyncio.CancelledError, Exception):
            pass

    def mark_dirty(self) -> None:
        if self.closed:
            return
        self.dirty = True
        self.start()

    def _set_initial_error(self, error: Exception) -> None:
        self.initial_error = error
        self.initial_ready.set()
        for subscriber in tuple(self.subscribers):
            if subscriber.initializing:
                subscriber.initial_error = error
                subscriber.initial_ready.set()

    def _set_initial_snapshot(self, snapshot: dict[str, Any]) -> None:
        self.initial_snapshot = snapshot
        self.latest_snapshot = snapshot
        self.initial_ready.set()
        for subscriber in tuple(self.subscribers):
            if subscriber.initializing:
                subscriber.initial_snapshot = snapshot
                subscriber.initial_ready.set()

    async def _build_loop(self) -> None:
        try:
            while self.subscribers and not self.closed:
                self.dirty = False
                refresh_waiters = set(self.refresh_waiters)
                try:
                    snapshot = _public_snapshot(
                        await _get_facade().build_player_state_snapshot(self.device_id)
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if self.dirty:
                        # An event arrived while the failed build was running;
                        # consume that one retry, but never self-trigger retries.
                        continue
                    if self.initial_snapshot is None:
                        self._set_initial_error(exc)
                    else:
                        for subscriber in refresh_waiters:
                            if subscriber in self.subscribers and subscriber.initializing:
                                subscriber.initial_error = exc
                                subscriber.initial_ready.set()
                        self.refresh_waiters.difference_update(refresh_waiters)
                    break

                if self.initial_snapshot is None:
                    self._set_initial_snapshot(snapshot)
                    self.refresh_waiters.difference_update(refresh_waiters)
                    if self.dirty:
                        continue
                    break

                snapshot_is_newer = _snapshot_is_newer(snapshot, self.latest_snapshot)
                if snapshot_is_newer:
                    self.latest_snapshot = snapshot
                for subscriber in refresh_waiters:
                    if subscriber in self.subscribers and subscriber.initializing:
                        subscriber.initial_snapshot = snapshot
                        subscriber.initial_ready.set()
                self.refresh_waiters.difference_update(refresh_waiters)

                # Refreshes with a new revision are real state events too.
                # Same-revision refreshes remain filtered by this comparison.
                if snapshot_is_newer:
                    self._fanout(snapshot)
                if self.dirty:
                    continue
                break
            await self._before_task_exit()
        finally:
            if self.build_task is asyncio.current_task():
                self.build_task = None
            if self.dirty and self.subscribers and not self.closed:
                self.start()
            if self.initial_snapshot is not None:
                self.initial_ready.set()

    def _fanout(self, snapshot: dict[str, Any]) -> None:
        for subscriber in tuple(self.subscribers):
            _queue_latest(subscriber.queue, snapshot)

    async def _before_task_exit(self) -> None:
        """Test seam for the final dirty/task-clear handoff."""

    async def initial_for(self, subscriber: _PlayerStreamSubscriber) -> dict[str, Any]:
        await subscriber.initial_ready.wait()
        if subscriber.initial_error is not None:
            raise subscriber.initial_error
        selected = subscriber.initial_snapshot or self.initial_snapshot
        if self.latest_snapshot is not None and (
            selected is None or _snapshot_is_newer(self.latest_snapshot, selected)
        ):
            selected = self.latest_snapshot
        while True:
            try:
                queued = subscriber.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if _snapshot_is_newer(queued, selected):
                selected = queued
        subscriber.initializing = False
        return selected or _public_snapshot({"device_id": self.device_id, "revision": 0})

    def add(self, subscriber: _PlayerStreamSubscriber) -> None:
        self.subscribers.add(subscriber)
        if self.initial_snapshot is None:
            self.start()
            return
        self.refresh_waiters.add(subscriber)
        self.dirty = True
        self.start()

    def remove(self, subscriber: _PlayerStreamSubscriber) -> None:
        self.subscribers.discard(subscriber)
        self.refresh_waiters.discard(subscriber)

    async def close(self) -> None:
        if self.cleanup_started:
            return
        self.cleanup_started = True
        self.closed = True
        if self.listener is not None:
            try:
                self.event_bus.unsubscribe("player_state_changed", self.listener)
            except Exception:
                pass
            self.listener = None
        if self.build_task is not None:
            self.build_task.cancel()
            try:
                await self.build_task
            except asyncio.CancelledError:
                pass
            self.build_task = None


_player_stream_channels: dict[str, _PlayerStreamChannel] = {}
_player_stream_sub_lock = asyncio.Lock()


def _snapshot_revision(snapshot: dict[str, Any] | None) -> tuple[str, int]:
    if not snapshot:
        return "", -1
    return (str(snapshot.get("server_instance_id", "")), int(snapshot.get("revision", 0)))


def _snapshot_is_newer(
    snapshot: dict[str, Any], previous: dict[str, Any] | None
) -> bool:
    current_epoch, current_revision = _snapshot_revision(snapshot)
    previous_epoch, previous_revision = _snapshot_revision(previous)
    return current_epoch != previous_epoch or current_revision > previous_revision


def _queue_latest(queue: asyncio.Queue[dict[str, Any]], snapshot: dict[str, Any]) -> None:
    try:
        queue.put_nowait(snapshot)
    except asyncio.QueueFull:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            queue.put_nowait(snapshot)
        except asyncio.QueueFull:
            pass


SSE_HEARTBEAT = b": heartbeat\n\n"


def _encode_player_state_event(snapshot: dict[str, Any]) -> bytes:
    payload = json.dumps(snapshot, ensure_ascii=False)
    event_id = f"{snapshot.get('server_instance_id', SERVER_INSTANCE_ID)}:{snapshot.get('revision', 0)}"
    return f"event: player_state\nid: {event_id}\ndata: {payload}\n\n".encode()


async def _push_player_state_event(device_id: str) -> None:
    """Compatibility hook; normal delivery is owned by the per-device listener."""
    channel = _player_stream_channels.get(device_id)
    if channel is not None:
        channel.mark_dirty()


def _create_player_stream_channel(
    device_id: str, event_bus: Any
) -> _PlayerStreamChannel:
    channel = _PlayerStreamChannel(device_id=device_id, event_bus=event_bus)
    _player_stream_channels[device_id] = channel
    if event_bus is not None:
        from xiaomusic.events import PLAYER_STATE_CHANGED

        def listener(**kwargs: Any) -> None:
            if kwargs.get("device_id") == device_id:
                channel.mark_dirty()

        channel.listener = listener
        try:
            event_bus.subscribe(PLAYER_STATE_CHANGED, listener)
        except Exception:
            channel.listener = None
    return channel


async def _get_player_stream_channel(device_id: str, event_bus: Any) -> _PlayerStreamChannel:
    async with _player_stream_sub_lock:
        channel = _player_stream_channels.get(device_id)
        return channel or _create_player_stream_channel(device_id, event_bus)


async def _register_player_stream_subscriber(
    device_id: str, event_bus: Any
) -> tuple[_PlayerStreamChannel, _PlayerStreamSubscriber]:
    """Atomically select/create a channel and attach one HTTP subscriber."""
    async with _player_stream_sub_lock:
        channel = _player_stream_channels.get(device_id)
        if channel is None or channel.closed:
            channel = _create_player_stream_channel(device_id, event_bus)
        subscriber = _PlayerStreamSubscriber()
        channel.add(subscriber)
        return channel, subscriber


async def _release_player_stream_subscriber(
    device_id: str, channel: _PlayerStreamChannel, subscriber: _PlayerStreamSubscriber
) -> None:
    should_close = False
    async with _player_stream_sub_lock:
        channel.remove(subscriber)
        if not channel.subscribers:
            channel.closed = True
            if _player_stream_channels.get(device_id) is channel:
                _player_stream_channels.pop(device_id, None)
            should_close = True
    if should_close:
        await channel.close()


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

    event_bus = getattr(xm, "event_bus", None)

    async def event_generator():
        channel: _PlayerStreamChannel | None = None
        subscriber: _PlayerStreamSubscriber | None = None
        try:
            channel, subscriber = await _register_player_stream_subscriber(
                device_id, event_bus
            )
            initial = await channel.initial_for(subscriber)
            yield (
                b"retry: 5000\n\n"
                + _encode_player_state_event(initial)
            )
            last_heartbeat_ts = asyncio.get_event_loop().time()
            heartbeat_interval = 15.0
            last_snapshot = initial
            while True:
                try:
                    timeout = max(
                        0.1,
                        heartbeat_interval
                        - (asyncio.get_event_loop().time() - last_heartbeat_ts),
                    )
                    snapshot = await asyncio.wait_for(subscriber.queue.get(), timeout)
                    if not _snapshot_is_newer(snapshot, last_snapshot):
                        continue
                    last_snapshot = snapshot
                    last_heartbeat_ts = asyncio.get_event_loop().time()
                    yield _encode_player_state_event(snapshot)
                except asyncio.TimeoutError:
                    now = asyncio.get_event_loop().time()
                    if now - last_heartbeat_ts >= heartbeat_interval:
                        yield SSE_HEARTBEAT
                        last_heartbeat_ts = now
                except asyncio.CancelledError:
                    break
                except Exception:
                    break
        finally:
            if channel is not None and subscriber is not None:
                await _release_player_stream_subscriber(
                    device_id, channel, subscriber
                )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
