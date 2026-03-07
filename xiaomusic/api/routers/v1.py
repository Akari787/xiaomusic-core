from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends

from xiaomusic import __version__
from xiaomusic.api.api_error import ApiError
from xiaomusic.api.dependencies import verification, xiaomusic
from xiaomusic.api.models import (
    ApiResponse,
    ControlRequest,
    PlayRequest,
    ResolveRequest,
    TtsRequest,
    VolumeRequest,
)
from xiaomusic.core.errors import (
    DeliveryPrepareError,
    DeviceNotFoundError,
    InvalidRequestError,
    SourceResolveError,
    TransportError,
)
from xiaomusic.playback.facade import PlaybackFacade

router = APIRouter(dependencies=[Depends(verification)])
_facade: PlaybackFacade | None = None


def _get_facade() -> PlaybackFacade:
    global _facade
    if _facade is None:
        _facade = PlaybackFacade(xiaomusic)
    return _facade


def _next_request_id(raw: str | None = None) -> str:
    return str(raw or uuid4().hex[:16])


def _api_response(code: int, message: str, data: dict[str, Any], request_id: str) -> dict[str, Any]:
    return ApiResponse(code=int(code), message=str(message), data=data, request_id=str(request_id)).model_dump()


def _api_ok(data: dict[str, Any], request_id: str) -> dict[str, Any]:
    return _api_response(0, "ok", data, request_id)


def _map_api_exception(exc: Exception, request_id: str) -> dict[str, Any]:
    if isinstance(exc, ApiError):
        return _api_response(exc.code, exc.message, exc.data, str(exc.request_id or request_id))
    if isinstance(exc, InvalidRequestError):
        return _api_response(50001, str(exc), {}, request_id)
    if isinstance(exc, SourceResolveError):
        return _api_response(20002, "source resolve failed", {"error_type": exc.__class__.__name__}, request_id)
    if isinstance(exc, DeliveryPrepareError):
        return _api_response(30001, "delivery prepare failed", {"error_type": exc.__class__.__name__}, request_id)
    if isinstance(exc, TransportError):
        return _api_response(40002, "transport dispatch failed", {"error_type": exc.__class__.__name__}, request_id)
    if isinstance(exc, DeviceNotFoundError):
        return _api_response(40004, "device not found", {"error_type": exc.__class__.__name__}, request_id)
    return _api_response(10000, "internal error", {"error_type": exc.__class__.__name__}, request_id)


def _normalize_device(device: dict[str, Any]) -> dict[str, Any]:
    device_id = str(device.get("miotDID") or device.get("did") or device.get("deviceID") or "")
    return {
        "device_id": device_id,
        "name": str(device.get("name") or device.get("alias") or ""),
        "model": str(device.get("hardware") or ""),
        "online": bool(device.get("isOnline") or device.get("online") or False),
    }


@router.post("/api/v1/play")
async def api_v1_play(data: PlayRequest):
    request_id = _next_request_id(data.request_id)
    try:
        out = await _get_facade().play(
            device_id=data.device_id,
            query=data.query,
            source_hint=data.source_hint,
            options=data.options,
            request_id=request_id,
        )
        return _api_ok(
            {
                "status": out.get("status", "playing"),
                "device_id": out.get("device_id", data.device_id),
                "source_plugin": out.get("source_plugin", ""),
                "transport": out.get("transport", ""),
                "media": out.get("media", {}),
                "extra": out.get("extra", {}),
            },
            request_id=request_id,
        )
    except Exception as exc:
        return _map_api_exception(exc, request_id)


@router.post("/api/v1/resolve")
async def api_v1_resolve(data: ResolveRequest):
    request_id = _next_request_id(data.request_id)
    try:
        out = await _get_facade().resolve(
            query=data.query,
            source_hint=data.source_hint,
            options=data.options,
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
        return _api_ok({k: v for k, v in out.items() if k != "request_id"}, request_id=request_id)
    except Exception as exc:
        return _map_api_exception(exc, request_id)


@router.post("/api/v1/control/pause")
async def api_v1_control_pause(data: ControlRequest):
    request_id = _next_request_id(data.request_id)
    try:
        out = await _get_facade().pause(data.device_id, request_id=request_id)
        return _api_ok({k: v for k, v in out.items() if k != "request_id"}, request_id=request_id)
    except Exception as exc:
        return _map_api_exception(exc, request_id)


@router.post("/api/v1/control/resume")
async def api_v1_control_resume(data: ControlRequest):
    request_id = _next_request_id(data.request_id)
    try:
        out = await _get_facade().resume(data.device_id, request_id=request_id)
        return _api_ok({k: v for k, v in out.items() if k != "request_id"}, request_id=request_id)
    except Exception as exc:
        return _map_api_exception(exc, request_id)


@router.post("/api/v1/control/tts")
async def api_v1_control_tts(data: TtsRequest):
    request_id = _next_request_id(data.request_id)
    try:
        out = await _get_facade().tts(data.device_id, data.text, request_id=request_id)
        return _api_ok({k: v for k, v in out.items() if k != "request_id"}, request_id=request_id)
    except Exception as exc:
        return _map_api_exception(exc, request_id)


@router.post("/api/v1/control/volume")
async def api_v1_control_volume(data: VolumeRequest):
    request_id = _next_request_id(data.request_id)
    try:
        out = await _get_facade().set_volume(data.device_id, int(data.volume), request_id=request_id)
        return _api_ok({k: v for k, v in out.items() if k != "request_id"}, request_id=request_id)
    except Exception as exc:
        return _map_api_exception(exc, request_id)


@router.post("/api/v1/control/probe")
async def api_v1_control_probe(data: ControlRequest):
    request_id = _next_request_id(data.request_id)
    try:
        out = await _get_facade().probe(data.device_id, request_id=request_id)
        return _api_ok({k: v for k, v in out.items() if k != "request_id"}, request_id=request_id)
    except Exception as exc:
        return _map_api_exception(exc, request_id)


@router.get("/api/v1/devices")
async def api_v1_devices():
    request_id = _next_request_id(None)
    try:
        devices = await xiaomusic.getalldevices()
        rows = [_normalize_device(item) for item in (devices or []) if isinstance(item, dict)]
        return _api_ok({"devices": rows}, request_id=request_id)
    except Exception as exc:
        return _map_api_exception(exc, request_id)


@router.get("/api/v1/system/status")
async def api_v1_system_status():
    request_id = _next_request_id(None)
    try:
        devices = await xiaomusic.getalldevices()
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
