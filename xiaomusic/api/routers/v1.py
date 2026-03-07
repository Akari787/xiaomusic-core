from __future__ import annotations

import asyncio
from urllib.parse import urlparse
from dataclasses import asdict, is_dataclass
from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from xiaomusic.api.base_url import detect_base_url
from xiaomusic.api.dependencies import verification, xiaomusic
from xiaomusic.api.models import ApiSessionsCleanupRequest
from xiaomusic.api.runtime_provider import get_runtime
from xiaomusic.api.models import (
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
)
from xiaomusic.api.response_utils import make_error, make_ok, playback_response
from xiaomusic.const import PLAY_TYPE_ALL, PLAY_TYPE_ONE, PLAY_TYPE_RND, PLAY_TYPE_SEQ, PLAY_TYPE_SIN
from xiaomusic.playback.facade import PlaybackFacade

router = APIRouter(dependencies=[Depends(verification)])
_facade: PlaybackFacade | None = None


def _get_facade() -> PlaybackFacade:
    global _facade
    if _facade is None:
        _facade = PlaybackFacade(xiaomusic, runtime_provider=get_runtime)
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


@router.post("/api/v1/play_url")
async def api_v1_play_url(data: ApiV1PlayUrlRequest):
    speaker_id = data.speaker_id

    options = (data.options.model_dump() if data.options else {})
    if options.get("volume") is not None:
        try:
            await _get_facade().set_volume(speaker_id, int(options["volume"]))
        except Exception:
            pass

    mode = "core"
    no_cache = bool(options.get("no_cache", False))
    try:
        out = await _get_facade().play_url(
            url=data.url,
            speaker_id=speaker_id,
            options={"mode": mode, "prefer_proxy": False, "no_cache": no_cache},
        )
    except Exception:
        return playback_response(
            ok=False,
            speaker_id=speaker_id,
            state="failed",
            error_code="E_STREAM_NOT_FOUND",
        )

    out["state"] = _state_to_api(out.get("state"), bool(out.get("ok")))
    return _playback_from_facade(out, fallback_state="failed")


@router.post("/api/v1/play_music")
async def api_v1_play_music(data: ApiV1PlayMusicRequest):
    try:
        await xiaomusic.do_play(
            did=data.speaker_id,
            name=data.music_name,
            search_key=data.search_key or "",
        )
        return playback_response(ok=True, speaker_id=data.speaker_id, state="playing")
    except Exception:
        return playback_response(
            ok=False,
            speaker_id=data.speaker_id,
            state="failed",
            error_code="E_XIAOMI_PLAY_FAILED",
        )


@router.post("/api/v1/play_music_list")
async def api_v1_play_music_list(data: ApiV1PlayMusicListRequest):
    try:
        await xiaomusic.do_play_music_list(
            did=data.speaker_id,
            list_name=data.list_name,
            music_name=data.music_name or "",
        )
        return playback_response(ok=True, speaker_id=data.speaker_id, state="playing")
    except Exception:
        return playback_response(
            ok=False,
            speaker_id=data.speaker_id,
            state="failed",
            error_code="E_XIAOMI_PLAY_FAILED",
        )


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


@router.post("/api/v1/stop")
async def api_v1_stop(data: ApiV1StopRequest):
    out = await _get_facade().stop(
        {
            "sid": data.sid or "",
            "speaker_id": data.speaker_id or "",
        }
    )
    return _playback_from_facade(out, fallback_state="stopped")


@router.post("/api/v1/pause")
async def api_v1_pause(data: ApiV1PauseRequest):
    out = await _get_facade().pause(data.speaker_id)
    if not out.get("ok"):
        return make_error(
            out.get("error_code") or "E_XIAOMI_PLAY_FAILED",
            payload={"speaker_id": data.speaker_id},
        )
    return make_ok(payload={"speaker_id": data.speaker_id}, message="paused")


@router.post("/api/v1/tts")
async def api_v1_tts(data: ApiV1TtsRequest):
    out = await _get_facade().tts(data.speaker_id, data.text)
    if not out.get("ok"):
        return make_error(
            out.get("error_code") or "E_XIAOMI_PLAY_FAILED",
            payload={"speaker_id": data.speaker_id},
        )
    return make_ok(payload={"speaker_id": data.speaker_id}, message="tts sent")


@router.post("/api/v1/set_volume")
async def api_v1_set_volume(data: ApiV1SetVolumeRequest):
    out = await _get_facade().set_volume(data.speaker_id, data.volume)
    if not out.get("ok"):
        return make_error(
            out.get("error_code") or "E_XIAOMI_PLAY_FAILED",
            payload={"speaker_id": data.speaker_id, "volume": data.volume},
        )
    return make_ok(
        payload={"speaker_id": data.speaker_id, "volume": int(data.volume)},
        message="volume updated",
    )


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
        out = await _get_facade().play_url(
            url=test_url,
            speaker_id=data.speaker_id,
            options={"mode": "core"},
        )
    except Exception:
        return make_error(
            "E_STREAM_NOT_FOUND",
            payload={"reachable": False, "test_url": test_url, "sid": ""},
        )
    await asyncio.sleep(2)
    st = await _get_facade().status({"speaker_id": data.speaker_id})
    reachable = bool(out.get("ok")) and str(st.get("state")) in {"1", "playing", "streaming"}
    payload = {
        "reachable": reachable,
        "test_url": test_url,
        "sid": out.get("sid") or "",
    }
    if reachable:
        return make_ok(payload=payload, message="地址可达")
    return make_error(
        out.get("error_code") or "E_XIAOMI_PLAY_FAILED",
        message="地址不可达，请检查网络与端口",
        payload=payload,
    )
