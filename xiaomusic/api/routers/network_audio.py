"""Network audio URL-to-stream workflow routes."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from xiaomusic.api.dependencies import verification, xiaomusic
from xiaomusic.network_audio.runtime import NetworkAudioRuntime

router = APIRouter(dependencies=[Depends(verification)])
_runtime: NetworkAudioRuntime | None = None


def _get_runtime() -> NetworkAudioRuntime:
    global _runtime
    if _runtime is None:
        _runtime = NetworkAudioRuntime(xiaomusic)
    return _runtime


@router.get("/network_audio/healthz")
async def network_audio_healthz():
    return _get_runtime().healthz()


@router.get("/network_audio/sessions")
async def network_audio_sessions():
    return _get_runtime().sessions()


@router.get("/network_audio/stream/{sid}")
async def network_audio_stream(sid: str):
    try:
        generator = _get_runtime().stream_chunks(sid)
    except KeyError:
        raise HTTPException(status_code=404, detail="stream session not found") from None
    return StreamingResponse(generator, media_type="audio/mpeg")
