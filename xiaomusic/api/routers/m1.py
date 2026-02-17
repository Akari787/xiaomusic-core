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


@router.post("/m1/play_url")
async def m1_play_url(data: DidUrl):
    did = data.did
    if not xiaomusic.did_exist(did):
        return {"ok": False, "error_code": "E_STREAM_NOT_FOUND", "error_message": "Did not exist"}
    return await _get_runtime().play_and_cast(did=did, url=data.url)


@router.post("/m1/play_link")
async def m1_play_link(data: DidUrl, proxy: bool = False):
    did = data.did
    if not xiaomusic.did_exist(did):
        return {
            "ok": False,
            "error_code": "E_STREAM_NOT_FOUND",
            "error_message": "Did not exist",
        }
    return await _get_runtime().play_link(did=did, url=data.url, prefer_proxy=proxy)


@router.get("/m1/healthz")
async def m1_healthz():
    return _get_runtime().healthz()


@router.get("/m1/sessions")
async def m1_sessions():
    return _get_runtime().sessions()


@router.post("/m1/stop")
async def m1_stop(data: SidObj):
    return _get_runtime().stop_session(sid=data.sid)


@router.get("/m1/stream/{sid}")
async def m1_stream(sid: str):
    try:
        generator = _get_runtime().stream_chunks(sid)
    except KeyError:
        raise HTTPException(status_code=404, detail="stream session not found") from None
    return StreamingResponse(generator, media_type="audio/mpeg")
