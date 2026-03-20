from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Query

from xiaomusic import __version__
from xiaomusic.api.api_error import ApiError
from xiaomusic.constants.api_fields import DEVICE_ID, OPTIONS, QUERY, REQUEST_ID, SOURCE_HINT
from xiaomusic.api.models import (
    ApiResponse,
    ControlRequest,
    FavoritesRequest,
    LibraryRefreshRequest,
    PlayModeRequest,
    PlayRequest,
    PlaylistPlayIndexRequest,
    PlaylistPlayRequest,
    ResolveRequest,
    ShutdownTimerRequest,
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


def _next_request_id(raw: str | None = None) -> str:
    return str(raw or uuid4().hex[:16])


def _api_response(code: int, message: str, data: dict[str, Any], request_id: str) -> dict[str, Any]:
    return ApiResponse(code=int(code), message=str(message), data=data, request_id=str(request_id)).model_dump()


def _api_ok(data: dict[str, Any], request_id: str) -> dict[str, Any]:
    return _api_response(0, "ok", data, request_id)


def _bad_request(request_id: str, message: str, *, field: str = "", allowed: list[str] | None = None) -> ApiError:
    data: dict[str, Any] = {"error_code": "E_INVALID_REQUEST", "stage": "validate"}
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
            data={"error_code": "E_XIAOMI_PLAY_FAILED", "stage": "xiaomi"},
            request_id=request_id,
        )
    return xm


def _playlist_arg(playlist_name: str, music_name: str = "") -> str:
    return f"{playlist_name}|{music_name}" if music_name else playlist_name


def _map_api_exception(exc: Exception, request_id: str) -> dict[str, Any]:
    if isinstance(exc, ApiError):
        return _api_response(exc.code, exc.message, exc.data, str(exc.request_id or request_id))
    if isinstance(exc, InvalidRequestError):
        return _api_response(50001, str(exc), {}, request_id)
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
                "error_code": "E_XIAOMI_PLAY_FAILED",
                "stage": "xiaomi",
            },
            request_id,
        )
    return _api_response(
        10000,
        "internal error",
        {
            "error_type": exc.__class__.__name__,
            "error_code": "E_INTERNAL",
            "stage": None,
        },
        request_id,
    )


def _normalize_device(device: dict[str, Any]) -> dict[str, Any]:
    device_id = str(device.get("miotDID") or device.get("did") or device.get("deviceID") or "")
    return {
        DEVICE_ID: device_id,
        "name": str(device.get("name") or device.get("alias") or ""),
        "model": str(device.get("hardware") or ""),
        "online": bool(device.get("isOnline") or device.get("online") or False),
    }


@router.post("/api/v1/play")
async def api_v1_play(data: PlayRequest):
    request_id = _next_request_id(data.request_id)
    options = PlayOptions.from_payload(getattr(data, OPTIONS, None))
    try:
        out = await _get_facade().play(
            device_id=getattr(data, DEVICE_ID),
            query=getattr(data, QUERY),
            source_hint=getattr(data, SOURCE_HINT),
            options=options,
            request_id=request_id,
        )
        return _api_ok(
            {
                "status": out.get("status", "playing"),
                DEVICE_ID: out.get(DEVICE_ID, getattr(data, DEVICE_ID)),
                "source_plugin": out.get("source_plugin", ""),
                "transport": out.get("transport", ""),
                "sid": request_id,
                "media": out.get("media", {}),
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
        return _map_api_exception(exc, request_id)


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
        return _map_api_exception(exc, request_id)


@router.post("/api/v1/control/stop")
async def api_v1_control_stop(data: ControlRequest):
    request_id = _next_request_id(data.request_id)
    try:
        out = await _get_facade().stop(data.device_id, request_id=request_id)
        return _api_ok({k: v for k, v in out.items() if k != REQUEST_ID}, request_id=request_id)
    except Exception as exc:
        return _map_api_exception(exc, request_id)


@router.post("/api/v1/control/pause")
async def api_v1_control_pause(data: ControlRequest):
    request_id = _next_request_id(data.request_id)
    try:
        out = await _get_facade().pause(data.device_id, request_id=request_id)
        return _api_ok({k: v for k, v in out.items() if k != REQUEST_ID}, request_id=request_id)
    except Exception as exc:
        return _map_api_exception(exc, request_id)


@router.post("/api/v1/control/resume")
async def api_v1_control_resume(data: ControlRequest):
    request_id = _next_request_id(data.request_id)
    try:
        out = await _get_facade().resume(data.device_id, request_id=request_id)
        return _api_ok({k: v for k, v in out.items() if k != REQUEST_ID}, request_id=request_id)
    except Exception as exc:
        return _map_api_exception(exc, request_id)


@router.post("/api/v1/control/tts")
async def api_v1_control_tts(data: TtsRequest):
    request_id = _next_request_id(data.request_id)
    try:
        out = await _get_facade().tts(data.device_id, data.text, request_id=request_id)
        return _api_ok({k: v for k, v in out.items() if k != REQUEST_ID}, request_id=request_id)
    except Exception as exc:
        return _map_api_exception(exc, request_id)


@router.post("/api/v1/control/volume")
async def api_v1_control_volume(data: VolumeRequest):
    request_id = _next_request_id(data.request_id)
    try:
        out = await _get_facade().set_volume(data.device_id, int(data.volume), request_id=request_id)
        return _api_ok({k: v for k, v in out.items() if k != REQUEST_ID}, request_id=request_id)
    except Exception as exc:
        return _map_api_exception(exc, request_id)


@router.post("/api/v1/control/probe")
async def api_v1_control_probe(data: ControlRequest):
    request_id = _next_request_id(data.request_id)
    try:
        out = await _get_facade().probe(data.device_id, request_id=request_id)
        return _api_ok({k: v for k, v in out.items() if k != REQUEST_ID}, request_id=request_id)
    except Exception as exc:
        return _map_api_exception(exc, request_id)


@router.get("/api/v1/devices")
async def api_v1_devices():
    request_id = _next_request_id(None)
    try:
        devices = await _get_xiaomusic().getalldevices()
        rows = [_normalize_device(item) for item in (devices or []) if isinstance(item, dict)]
        return _api_ok({"devices": rows}, request_id=request_id)
    except Exception as exc:
        return _map_api_exception(exc, request_id)


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
        return _map_api_exception(exc, request_id)


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
        out = await _get_facade().previous(device_id=data.device_id, request_id=request_id)
        return _api_ok({k: v for k, v in out.items() if k != REQUEST_ID}, request_id=request_id)
    except Exception as exc:
        return _map_api_exception(exc, request_id)


@router.post("/api/v1/control/next")
async def api_v1_control_next(data: ControlRequest):
    request_id = _next_request_id(data.request_id)
    try:
        out = await _get_facade().next(device_id=data.device_id, request_id=request_id)
        return _api_ok({k: v for k, v in out.items() if k != REQUEST_ID}, request_id=request_id)
    except Exception as exc:
        return _map_api_exception(exc, request_id)


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
        await handler(did=data.device_id, dotts=False, refresh_playlist=False)
        return _api_ok({"status": "ok", DEVICE_ID: data.device_id, "play_mode": play_mode}, request_id=request_id)
    except Exception as exc:
        return _map_api_exception(exc, request_id)


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
        return _map_api_exception(exc, request_id)


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
        return _map_api_exception(exc, request_id)


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
        return _map_api_exception(exc, request_id)


@router.post("/api/v1/playlist/play")
async def api_v1_playlist_play(data: PlaylistPlayRequest):
    request_id = _next_request_id(data.request_id)
    try:
        if not str(data.playlist_name or "").strip():
            raise _bad_request(request_id, "playlist_name is required", field="playlist_name")
        xm = _require_device(data.device_id, request_id)
        await xm.play_music_list(did=data.device_id, arg1=_playlist_arg(data.playlist_name, data.music_name))
        return _api_ok(
            {
                "status": "ok",
                DEVICE_ID: data.device_id,
                "playlist_name": data.playlist_name,
                "music_name": data.music_name,
            },
            request_id=request_id,
        )
    except Exception as exc:
        return _map_api_exception(exc, request_id)


@router.post("/api/v1/playlist/play-index")
async def api_v1_playlist_play_index(data: PlaylistPlayIndexRequest):
    request_id = _next_request_id(data.request_id)
    try:
        if not str(data.playlist_name or "").strip():
            raise _bad_request(request_id, "playlist_name is required", field="playlist_name")
        xm = _require_device(data.device_id, request_id)
        await xm.play_music_list_by_index(
            did=data.device_id,
            playlist_name=data.playlist_name,
            index=int(data.index),
        )
        return _api_ok(
            {
                "status": "ok",
                DEVICE_ID: data.device_id,
                "playlist_name": data.playlist_name,
                "index": int(data.index),
            },
            request_id=request_id,
        )
    except Exception as exc:
        return _map_api_exception(exc, request_id)


@router.post("/api/v1/library/refresh")
async def api_v1_library_refresh(data: LibraryRefreshRequest):
    request_id = _next_request_id(data.request_id)
    try:
        xm = _get_xiaomusic()
        await xm.gen_music_list()
        return _api_ok({"status": "ok", "refreshed": True}, request_id=request_id)
    except Exception as exc:
        return _map_api_exception(exc, request_id)


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
async def api_v1_player_state(device_id: str = Query(..., min_length=1), request_id: str | None = None):
    rid = _next_request_id(request_id)
    try:
        out = await _get_facade().player_state(device_id=device_id, request_id=rid)
        data = {
            DEVICE_ID: str(out.get(DEVICE_ID, device_id)),
            "is_playing": bool(out.get("is_playing", False)),
            "cur_music": str(out.get("cur_music", "") or ""),
            "offset": max(0, int(out.get("offset", 0) or 0)),
            "duration": max(0, int(out.get("duration", 0) or 0)),
        }
        return _api_ok(data, request_id=rid)
    except Exception as exc:
        return _map_api_exception(exc, rid)
