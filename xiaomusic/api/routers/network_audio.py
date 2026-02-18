"""Network audio URL-to-stream workflow routes."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from xiaomusic.api.dependencies import verification, xiaomusic
from xiaomusic.api.models import DidUrl, SidObj
from xiaomusic.network_audio.runtime import NetworkAudioRuntime

router = APIRouter(dependencies=[Depends(verification)])
_runtime: NetworkAudioRuntime | None = None


def _get_runtime() -> NetworkAudioRuntime:
    global _runtime
    if _runtime is None:
        _runtime = NetworkAudioRuntime(xiaomusic)
    return _runtime


@router.post("/network_audio/play_url")
async def network_audio_play_url(data: DidUrl):
    did = data.did
    if not xiaomusic.did_exist(did):
        return {
            "ok": False,
            "error_code": "E_STREAM_NOT_FOUND",
            "error_message": "Did not exist",
        }
    return await _get_runtime().play_and_cast(did=did, url=data.url)


@router.post("/network_audio/play_link")
async def network_audio_play_link(data: DidUrl, proxy: bool = False):
    did = data.did
    if not xiaomusic.did_exist(did):
        return {
            "ok": False,
            "error_code": "E_STREAM_NOT_FOUND",
            "error_message": "Did not exist",
        }
    return await _get_runtime().play_link(did=did, url=data.url, prefer_proxy=proxy)


@router.get("/network_audio/healthz")
async def network_audio_healthz():
    return _get_runtime().healthz()


@router.get("/network_audio/sessions")
async def network_audio_sessions():
    return _get_runtime().sessions()


@router.post("/network_audio/stop")
async def network_audio_stop(data: SidObj):
    return _get_runtime().stop_session(sid=data.sid)


@router.get("/network_audio/stream/{sid}")
async def network_audio_stream(sid: str):
    try:
        generator = _get_runtime().stream_chunks(sid)
    except KeyError:
        raise HTTPException(status_code=404, detail="stream session not found") from None
    return StreamingResponse(generator, media_type="audio/mpeg")
