from __future__ import annotations

import asyncio
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Query, Request

from xiaomusic.api.base_url import detect_base_url
from xiaomusic.api.dependencies import verification, xiaomusic
from xiaomusic.api.routers.network_audio import _get_runtime as _shared_runtime
from xiaomusic.api.models import (
    ApiV1PlayMusicListRequest,
    ApiV1PlayMusicRequest,
    ApiV1PlayUrlRequest,
    ApiV1ReachabilityRequest,
    ApiV1StopRequest,
)
from xiaomusic.network_audio.contracts import ERROR_CODES
from xiaomusic.playback.facade import PlaybackFacade

router = APIRouter(dependencies=[Depends(verification)])
_facade: PlaybackFacade | None = None


def _get_facade() -> PlaybackFacade:
    global _facade
    if _facade is None:
        _facade = PlaybackFacade(xiaomusic, runtime_provider=_shared_runtime)
    return _facade


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


@router.get("/api/v1/detect_base_url")
async def api_v1_detect_base_url(request: Request):
    base = detect_base_url(request, xiaomusic.getconfig())
    if base:
        return {"success": True, "base_url": base, "message": "检测到推荐地址"}
    return {
        "success": True,
        "base_url": None,
        "message": "自动检测失败，请手动填写",
    }


@router.post("/api/v1/play_url")
async def api_v1_play_url(data: ApiV1PlayUrlRequest):
    speaker_id = data.speaker_id

    options = (data.options.model_dump() if data.options else {})
    if options.get("volume") is not None:
        try:
            await xiaomusic.set_volume(speaker_id, int(options["volume"]))
        except Exception:
            pass

    mode = "network_audio_link" if str(data.url).startswith(("http://", "https://")) else "direct"
    try:
        out = await _get_facade().play_url(
            url=data.url,
            speaker_id=speaker_id,
            options={"mode": mode, "prefer_proxy": False},
        )
    except Exception:
        return {
            "sid": "",
            "state": "failed",
            "title": None,
            "is_live": None,
            "error_code": "E_STREAM_NOT_FOUND",
        }

    raw = out.get("raw") or {}
    return {
        "sid": out.get("sid") or "",
        "state": _state_to_api(out.get("state"), bool(out.get("ok"))),
        "title": out.get("title"),
        "is_live": _is_live(raw),
        "error_code": out.get("error_code"),
    }


@router.post("/api/v1/play_music")
async def api_v1_play_music(data: ApiV1PlayMusicRequest):
    try:
        await xiaomusic.do_play(
            did=data.speaker_id,
            name=data.music_name,
            search_key=data.search_key or "",
        )
        return {
            "success": True,
            "state": "playing",
            "error_code": None,
        }
    except Exception:
        return {
            "success": False,
            "state": "failed",
            "error_code": "E_XIAOMI_PLAY_FAILED",
        }


@router.post("/api/v1/play_music_list")
async def api_v1_play_music_list(data: ApiV1PlayMusicListRequest):
    try:
        await xiaomusic.do_play_music_list(
            did=data.speaker_id,
            list_name=data.list_name,
            music_name=data.music_name or "",
        )
        return {
            "success": True,
            "state": "playing",
            "error_code": None,
        }
    except Exception:
        return {
            "success": False,
            "state": "failed",
            "error_code": "E_XIAOMI_PLAY_FAILED",
        }


@router.post("/api/v1/stop")
async def api_v1_stop(data: ApiV1StopRequest):
    out = await _get_facade().stop(
        {
            "sid": data.sid or "",
            "speaker_id": data.speaker_id or "",
        }
    )
    return {
        "sid": out.get("sid") or "",
        "speaker_id": out.get("speaker_id") or "",
        "state": out.get("state") or "stopped",
        "error_code": out.get("error_code"),
    }


@router.get("/api/v1/status")
async def api_v1_status(
    speaker_id: str | None = Query(default=None),
    sid: str | None = Query(default=None),
):
    out = await _get_facade().status({"speaker_id": speaker_id or "", "sid": sid or ""})
    raw = out.get("raw") or {}
    uptime = None
    reconnect_count = None
    if isinstance(raw.get("session"), dict):
        sess: dict = raw["session"]
        uptime = int(sess.get("uptime") or 0) if sess.get("uptime") is not None else None
        reconnect_count = sess.get("reconnect_count")

    return {
        "sid": out.get("sid") or "",
        "state": out.get("state") or "unknown",
        "title": out.get("title"),
        "is_live": _is_live(raw),
        "uptime": uptime,
        "reconnect_count": reconnect_count,
        "error_code": out.get("error_code"),
    }


@router.post("/api/v1/test_reachability")
async def api_v1_test_reachability(request: Request, data: ApiV1ReachabilityRequest):
    base_url = data.base_url or detect_base_url(request, xiaomusic.getconfig())
    if not base_url:
        return {
            "success": False,
            "reachable": False,
            "error_code": "E_INTERNAL",
            "message": "自动检测失败，请手动填写",
        }

    parsed = urlparse(base_url)
    if not parsed.scheme or not parsed.hostname:
        return {
            "success": False,
            "reachable": False,
            "error_code": "E_INTERNAL",
            "message": "base_url 无效",
        }

    test_url = f"{base_url.rstrip('/')}/static/silence.mp3"
    try:
        out = await _get_facade().play_url(
            url=test_url,
            speaker_id=data.speaker_id,
            options={"mode": "direct"},
        )
    except Exception:
        return {
            "success": False,
            "reachable": False,
            "error_code": "E_STREAM_NOT_FOUND",
            "message": ERROR_CODES["E_STREAM_NOT_FOUND"],
        }
    await asyncio.sleep(2)
    st = await _get_facade().status({"speaker_id": data.speaker_id})
    reachable = bool(out.get("ok")) and str(st.get("state")) in {"1", "playing", "streaming"}
    return {
        "success": True,
        "reachable": reachable,
        "test_url": test_url,
        "sid": out.get("sid") or "",
        "error_code": None if reachable else (out.get("error_code") or "E_XIAOMI_PLAY_FAILED"),
        "message": "地址可达" if reachable else "地址不可达，请检查网络与端口",
    }
