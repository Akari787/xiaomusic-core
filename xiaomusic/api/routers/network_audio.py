"""Network audio URL-to-stream workflow routes."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from xiaomusic.api.dependencies import verification, xiaomusic
from xiaomusic.api.models import DidUrl, SidObj
from xiaomusic.api.response_utils import playback_response
from xiaomusic.network_audio.runtime import NetworkAudioRuntime
from xiaomusic.playback.facade import PlaybackFacade

router = APIRouter(dependencies=[Depends(verification)])
_runtime: NetworkAudioRuntime | None = None
_facade: PlaybackFacade | None = None


def _get_runtime() -> NetworkAudioRuntime:
    global _runtime
    if _runtime is None:
        _runtime = NetworkAudioRuntime(xiaomusic)
    return _runtime


def _get_facade() -> PlaybackFacade:
    global _facade
    if _facade is None:
        _facade = PlaybackFacade(xiaomusic, runtime_provider=_get_runtime)
    return _facade


@router.post("/network_audio/play_url")
async def network_audio_play_url(data: DidUrl):
    """Deprecated wrapper for unified play_url facade."""
    did = data.did
    if not xiaomusic.did_exist(did):
        return playback_response(
            ok=False,
            speaker_id=did,
            state="failed",
            error_code="E_STREAM_NOT_FOUND",
            deprecated=True,
        )
    out = await _get_facade().play_url(
        url=data.url,
        speaker_id=did,
        options={"mode": "network_audio_cast"},
    )
    return playback_response(
        ok=bool(out.get("ok")),
        sid=out.get("sid") or "",
        speaker_id=did,
        state=str(out.get("state") or "unknown"),
        title=out.get("title"),
        stream_url=out.get("stream_url") or "",
        error_code=out.get("error_code"),
        deprecated=True,
    )


@router.post("/network_audio/play_link")
async def network_audio_play_link(data: DidUrl, proxy: bool = False):
    """Deprecated wrapper for unified play_url facade."""
    did = data.did
    if not xiaomusic.did_exist(did):
        return playback_response(
            ok=False,
            speaker_id=did,
            state="failed",
            error_code="E_STREAM_NOT_FOUND",
            deprecated=True,
        )
    out = await _get_facade().play_url(
        url=data.url,
        speaker_id=did,
        options={"mode": "network_audio_link", "prefer_proxy": proxy},
    )
    return playback_response(
        ok=bool(out.get("ok")),
        sid=out.get("sid") or "",
        speaker_id=did,
        state=str(out.get("state") or "unknown"),
        title=out.get("title"),
        stream_url=out.get("stream_url") or "",
        error_code=out.get("error_code"),
        deprecated=True,
    )


@router.get("/network_audio/healthz")
async def network_audio_healthz():
    return _get_runtime().healthz()


@router.get("/network_audio/sessions")
async def network_audio_sessions():
    return _get_runtime().sessions()


@router.post("/network_audio/stop")
async def network_audio_stop(data: SidObj):
    out = await _get_facade().stop({"sid": data.sid})
    return playback_response(
        ok=bool(out.get("ok")),
        sid=out.get("sid") or "",
        speaker_id=out.get("speaker_id") or "",
        state=str(out.get("state") or "stopped"),
        stream_url=out.get("stream_url") or "",
        error_code=out.get("error_code"),
        deprecated=True,
    )


@router.get("/network_audio/stream/{sid}")
async def network_audio_stream(sid: str):
    try:
        generator = _get_runtime().stream_chunks(sid)
    except KeyError:
        raise HTTPException(status_code=404, detail="stream session not found") from None
    return StreamingResponse(generator, media_type="audio/mpeg")
