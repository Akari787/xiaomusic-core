from __future__ import annotations

import asyncio
from urllib.parse import urlparse
from dataclasses import asdict, is_dataclass
from uuid import uuid4
from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from xiaomusic.api.api_error import ApiError
from xiaomusic.api.base_url import detect_base_url
from xiaomusic.api.dependencies import verification, xiaomusic
from xiaomusic.api.models import ApiSessionsCleanupRequest
from xiaomusic.api.runtime_provider import get_runtime
from xiaomusic.api.models import (
    ApiResponse,
    ApiV1PauseRequest,
    ApiV1SetPlayModeRequest,
    ApiV1PlayMusicListRequest,
    ApiV1PlayMusicRequest,
    ApiV1ProbeRequest,
    ApiV1PlayUrlRequest,
    ApiV1ReachabilityRequest,
    ApiV1SetVolumeRequest,
    ApiV1StopRequest,
    ApiV1TtsRequest,
    ControlRequest,
    PlayRequest,
    ResolveRequest,
    TtsRequest,
    VolumeRequest,
)
from xiaomusic.api.response_utils import make_error, make_ok, playback_response
from xiaomusic.const import PLAY_TYPE_ALL, PLAY_TYPE_ONE, PLAY_TYPE_RND, PLAY_TYPE_SEQ, PLAY_TYPE_SIN
from xiaomusic.core.errors import DeliveryPrepareError, DeviceNotFoundError, SourceResolveError, TransportError
from xiaomusic.playback.facade import PlaybackFacade

router = APIRouter(dependencies=[Depends(verification)])
_facade: PlaybackFacade | None = None


def _get_facade() -> PlaybackFacade:
    global _facade
    if _facade is None:
        _facade = PlaybackFacade(xiaomusic, runtime_provider=get_runtime)
    return _facade


def _next_request_id(raw: str | None = None) -> str:
    return str(raw or uuid4().hex[:16])


def _api_response(code: int, message: str, data: dict[str, Any], request_id: str) -> dict[str, Any]:
    return ApiResponse(code=int(code), message=str(message), data=data, request_id=str(request_id)).model_dump()


def _api_ok(data: dict[str, Any], request_id: str, message: str = "ok") -> dict[str, Any]:
    return _api_response(code=0, message=message, data=data, request_id=request_id)


def _map_api_exception(exc: Exception, request_id: str) -> dict[str, Any]:
    if isinstance(exc, ApiError):
        rid = str(exc.request_id or request_id)
        return _api_response(exc.code, exc.message, exc.data, rid)
    if isinstance(exc, SourceResolveError):
        return _api_response(20002, "source resolve failed", {"error_type": exc.__class__.__name__}, request_id)
    if isinstance(exc, DeliveryPrepareError):
        return _api_response(30001, "delivery prepare failed", {"error_type": exc.__class__.__name__}, request_id)
    if isinstance(exc, TransportError):
        return _api_response(40002, "transport dispatch failed", {"error_type": exc.__class__.__name__}, request_id)
    if isinstance(exc, DeviceNotFoundError):
        return _api_response(40004, "device not found", {"error_type": exc.__class__.__name__}, request_id)
    return _api_response(10000, "internal error", {"error_type": exc.__class__.__name__}, request_id)


def _legacy_playback_from_unified_play(out: dict[str, Any], speaker_id: str) -> dict[str, Any]:
    code = int(out.get("code", 10000))
    request_id = str(out.get("request_id") or _next_request_id(None))
    data = out.get("data") or {}
    if not isinstance(data, dict):
        data = {}
    media = data.get("media") or {}
    if not isinstance(media, dict):
        media = {}
    ok = code == 0
    return playback_response(
        ok=ok,
        speaker_id=speaker_id,
        state="streaming" if ok else "failed",
        title=str(media.get("title") or "") or None,
        stream_url=str(media.get("stream_url") or ""),
        source_plugin=str(data.get("source_plugin") or "") or None,
        transport=str(data.get("transport") or "") or None,
        error_code=None if ok else "E_INTERNAL",
        message=str(out.get("message") or "ok"),
        request_id=request_id,
    )


def _state_to_api(state, ok: bool) -> str:
    if not ok:
        return "failed"
    s = str(state or "").lower()
    if s in {"1", "playing", "streaming"}:
        return "streaming"
    if s in {"creating", "resolving"}:
        return s
    return "streaming"


def _is_live(out: dict) -> bool | None:
    url_info = out.get("url_info") or {}
    hint = str(url_info.get("kind_hint") or "").lower()
    if hint in {"live", "radio"}:
        return True
    if hint in {"vod", "audio"}:
        return False
    return None


def _coerce_session(raw: dict) -> dict:
    sess: Any = raw.get("session")
    if isinstance(sess, dict):
        return sess
    if sess is not None and is_dataclass(sess) and not isinstance(sess, type):
        return asdict(sess)  # type: ignore[arg-type]
    return {}


def _infer_stage(state: str, error_code: str | None) -> str:
    s = str(state or "").lower()
    if s in {"1", "playing"}:
        return "xiaomi"
    if s == "resolving":
        return "resolve"
    if s in {"streaming", "reconnecting"}:
        return "stream"
    ec = str(error_code or "")
    if ec.startswith("E_RESOLVE"):
        return "resolve"
    if ec.startswith("E_STREAM_START"):
        return "ffmpeg"
    if ec.startswith("E_XIAOMI"):
        return "xiaomi"
    if ec.startswith("E_STREAM"):
        return "stream"
    return "unknown"


def _playback_from_facade(out: dict, *, fallback_state: str = "unknown", deprecated: bool | None = None) -> dict:
    raw = out.get("raw") or {}
    sess = _coerce_session(raw)
    uptime = None
    reconnect_count = None
    if sess:
        if sess.get("uptime") is not None:
            uptime = int(sess.get("uptime") or 0)
        reconnect_count = sess.get("reconnect_count")

    state = str(out.get("state") or fallback_state)
    error_code = out.get("error_code") or sess.get("last_error_code")
    last_transition_at = sess.get("last_transition_at")
    last_error_code = sess.get("last_error_code")
    cache_hit = out.get("cache_hit")
    resolve_ms = out.get("resolve_ms")
    if resolve_ms is None:
        resolve_ms = sess.get("resolve_ms")
    stage = out.get("fail_stage") or _infer_stage(state, error_code)
    source_plugin = out.get("source_plugin") or raw.get("source")
    transport = out.get("transport") or raw.get("transport")

    return playback_response(
        ok=bool(out.get("ok")),
        sid=out.get("sid") or "",
        speaker_id=out.get("speaker_id") or "",
        state=state,
        title=out.get("title"),
        stream_url=out.get("stream_url") or "",
        is_live=_is_live(raw),
        uptime=uptime,
        reconnect_count=reconnect_count,
        stage=stage,
        last_transition_at=last_transition_at,
        last_error_code=last_error_code,
        cache_hit=cache_hit,
        resolve_ms=resolve_ms,
        source_plugin=source_plugin,
        transport=transport,
        error_code=error_code,
        deprecated=deprecated,
    )


@router.get("/api/v1/detect_base_url")
async def api_v1_detect_base_url(request: Request):
    base = detect_base_url(request, xiaomusic.getconfig())
    if base:
        return make_ok(payload={"base_url": base}, message="检测到推荐地址")
    return make_error(
        "E_INTERNAL",
        message="自动检测失败，请手动填写",
        payload={"base_url": None},
    )


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
        payload = {
            "status": out.get("status", "playing"),
            "device_id": out.get("device_id", data.device_id),
            "source_plugin": out.get("source_plugin", ""),
            "transport": out.get("transport", ""),
            "media": out.get("media", {}),
            "extra": out.get("extra", {}),
        }
        return _api_ok(payload, request_id=request_id)
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
        payload = {
            "resolved": bool(out.get("resolved", False)),
            "source_plugin": out.get("source_plugin", ""),
            "media": out.get("media", {}),
            "extra": out.get("extra", {}),
        }
        return _api_ok(payload, request_id=request_id)
    except Exception as exc:
        return _map_api_exception(exc, request_id)


@router.post("/api/v1/play_url")
async def api_v1_play_url(data: ApiV1PlayUrlRequest):
    options = data.options.model_dump() if data.options else {}
    request_id = _next_request_id(None)
    if options.get("volume") is not None:
        try:
            await _get_facade().control_set_volume(data.speaker_id, int(options["volume"]), request_id=request_id)
        except Exception:
            pass
    unified = await api_v1_play(
        PlayRequest(
            device_id=data.speaker_id,
            query=data.url,
            source_hint="auto",
            options={"no_cache": bool(options.get("no_cache", False))},
            request_id=request_id,
        )
    )
    return _legacy_playback_from_unified_play(unified, data.speaker_id)


@router.post("/api/v1/play_music")
async def api_v1_play_music(data: ApiV1PlayMusicRequest):
    unified = await api_v1_play(
        PlayRequest(
            device_id=data.speaker_id,
            query=data.music_name or data.search_key,
            source_hint="local_library",
            options={"search_key": data.search_key or ""},
        )
    )
    return _legacy_playback_from_unified_play(unified, data.speaker_id)


@router.post("/api/v1/play_music_list")
async def api_v1_play_music_list(data: ApiV1PlayMusicListRequest):
    unified = await api_v1_play(
        PlayRequest(
            device_id=data.speaker_id,
            query=data.music_name or data.list_name,
            source_hint="local_library",
            options={"list_name": data.list_name},
        )
    )
    if int(unified.get("code", 10000)) == 0:
        return _legacy_playback_from_unified_play(unified, data.speaker_id)

    # compatibility_layer: keep historical mixed-playlist behavior for web/online songs.
    # removal_condition: remove when playlist play is fully migrated to unified source plugins.
    try:
        await xiaomusic.do_play_music_list(
            did=data.speaker_id,
            list_name=data.list_name,
            music_name=data.music_name or "",
        )
        return playback_response(
            ok=True,
            speaker_id=data.speaker_id,
            state="playing",
            source_plugin="legacy_playlist",
            transport="mina",
            deprecated=True,
            message="ok",
        )
    except Exception:
        return _legacy_playback_from_unified_play(unified, data.speaker_id)


@router.post("/api/v1/set_play_mode")
async def api_v1_set_play_mode(data: ApiV1SetPlayModeRequest):
    mapping = {
        0: PLAY_TYPE_ONE,
        1: PLAY_TYPE_ALL,
        2: PLAY_TYPE_RND,
        3: PLAY_TYPE_SIN,
        4: PLAY_TYPE_SEQ,
    }
    mode = mapping.get(int(data.mode_index), PLAY_TYPE_RND)
    try:
        await xiaomusic.set_play_type(
            data.speaker_id,
            mode,
            False,
            refresh_playlist=False,
        )
        return make_ok(
            payload={"speaker_id": data.speaker_id, "mode_index": int(data.mode_index)},
            message="play mode updated",
        )
    except Exception:
        return make_error(
            "E_XIAOMI_PLAY_FAILED",
            message="set play mode failed",
            payload={"speaker_id": data.speaker_id, "mode_index": int(data.mode_index)},
        )


@router.post("/api/v1/control/stop")
async def api_v1_control_stop(data: ControlRequest):
    request_id = _next_request_id(data.request_id)
    try:
        out = await _get_facade().control_stop(data.device_id, request_id=request_id)
        return _api_ok(out, request_id=request_id)
    except Exception as exc:
        return _map_api_exception(exc, request_id)


@router.post("/api/v1/control/pause")
async def api_v1_control_pause(data: ControlRequest):
    request_id = _next_request_id(data.request_id)
    try:
        out = await _get_facade().control_pause(data.device_id, request_id=request_id)
        return _api_ok(out, request_id=request_id)
    except Exception as exc:
        return _map_api_exception(exc, request_id)


@router.post("/api/v1/control/resume")
async def api_v1_control_resume(data: ControlRequest):
    request_id = _next_request_id(data.request_id)
    try:
        out = await _get_facade().control_resume(data.device_id, request_id=request_id)
        return _api_ok(out, request_id=request_id)
    except Exception as exc:
        return _map_api_exception(exc, request_id)


@router.post("/api/v1/control/tts")
async def api_v1_control_tts(data: TtsRequest):
    request_id = _next_request_id(data.request_id)
    try:
        out = await _get_facade().control_tts(data.device_id, data.text, request_id=request_id)
        return _api_ok(out, request_id=request_id)
    except Exception as exc:
        return _map_api_exception(exc, request_id)


@router.post("/api/v1/control/volume")
async def api_v1_control_volume(data: VolumeRequest):
    request_id = _next_request_id(data.request_id)
    try:
        out = await _get_facade().control_set_volume(
            data.device_id,
            int(data.volume),
            request_id=request_id,
        )
        return _api_ok(out, request_id=request_id)
    except Exception as exc:
        return _map_api_exception(exc, request_id)


@router.post("/api/v1/stop")
async def api_v1_stop(data: ApiV1StopRequest):
    if not data.speaker_id:
        rid = _next_request_id(None)
        return _api_response(50001, "device_id is required", {}, rid)
    unified = await api_v1_control_stop(ControlRequest(device_id=data.speaker_id))
    code = int(unified.get("code", 10000))
    ok = code == 0
    req = str(unified.get("request_id") or _next_request_id(None))
    data_payload = unified.get("data") or {}
    if not isinstance(data_payload, dict):
        data_payload = {}
    return playback_response(
        ok=ok,
        speaker_id=data.speaker_id,
        state="stopped" if ok else "failed",
        source_plugin=None,
        transport=str(data_payload.get("transport") or "") or None,
        error_code=None if ok else "E_INTERNAL",
        message=str(unified.get("message") or "ok"),
        request_id=req,
    )


@router.post("/api/v1/pause")
async def api_v1_pause(data: ApiV1PauseRequest):
    return await api_v1_control_pause(ControlRequest(device_id=data.speaker_id))


@router.post("/api/v1/tts")
async def api_v1_tts(data: ApiV1TtsRequest):
    return await api_v1_control_tts(TtsRequest(device_id=data.speaker_id, text=data.text))


@router.post("/api/v1/set_volume")
async def api_v1_set_volume(data: ApiV1SetVolumeRequest):
    return await api_v1_control_volume(VolumeRequest(device_id=data.speaker_id, volume=int(data.volume)))


@router.post("/api/v1/probe")
async def api_v1_probe(data: ApiV1ProbeRequest):
    out = await _get_facade().probe(data.speaker_id)
    if not out.get("ok"):
        return make_error(
            out.get("error_code") or "E_XIAOMI_PLAY_FAILED",
            payload={"speaker_id": data.speaker_id},
        )
    raw = out.get("raw") or {}
    return make_ok(
        payload={
            "speaker_id": data.speaker_id,
            "transport": raw.get("transport"),
            "reachability": raw.get("reachability") or {},
        },
        message="probe done",
    )


@router.get("/api/v1/status")
async def api_v1_status(
    speaker_id: str | None = Query(default=None),
    sid: str | None = Query(default=None),
):
    out = await _get_facade().status({"speaker_id": speaker_id or "", "sid": sid or ""})
    return _playback_from_facade(out)


@router.post("/api/v1/sessions/cleanup")
async def api_v1_sessions_cleanup(data: ApiSessionsCleanupRequest):
    runtime = get_runtime()
    ret = runtime.cleanup_sessions(
        max_sessions=int(data.max_sessions or 100),
        ttl_seconds=data.ttl_seconds,
    )
    return make_ok(
        payload={
            "removed": ret.get("removed", 0),
            "remaining": ret.get("remaining", 0),
        },
        message="cleanup done",
    )


@router.post("/api/v1/test_reachability")
async def api_v1_test_reachability(request: Request, data: ApiV1ReachabilityRequest):
    base_url = data.base_url or detect_base_url(request, xiaomusic.getconfig())
    if not base_url:
        return make_error(
            "E_INTERNAL",
            message="自动检测失败，请手动填写",
            payload={"reachable": False},
        )

    parsed = urlparse(base_url)
    if not parsed.scheme or not parsed.hostname:
        return make_error(
            "E_INTERNAL",
            message="base_url 无效",
            payload={"reachable": False},
        )

    test_url = f"{base_url.rstrip('/')}/static/silence.mp3"
    try:
        out = await _get_facade().play(
            device_id=data.speaker_id,
            query=test_url,
            source_hint="auto",
            options={},
        )
    except Exception:
        return make_error(
            "E_STREAM_NOT_FOUND",
            payload={"reachable": False, "test_url": test_url, "sid": ""},
        )
    await asyncio.sleep(2)
    st = await _get_facade().status({"speaker_id": data.speaker_id})
    reachable = str(st.get("state")) in {"1", "playing", "streaming"}
    payload = {
        "reachable": reachable,
        "test_url": test_url,
        "sid": "",
    }
    if reachable:
        return make_ok(payload=payload, message="地址可达")
    return make_error(
        out.get("error_code") or "E_XIAOMI_PLAY_FAILED",
        message="地址不可达，请检查网络与端口",
        payload=payload,
    )
