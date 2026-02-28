"""Network audio URL-to-stream workflow routes."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from xiaomusic.api.dependencies import verification
from xiaomusic.api.runtime_provider import get_runtime

router = APIRouter(dependencies=[Depends(verification)])


@router.get("/network_audio/healthz")
async def network_audio_healthz():
    return get_runtime().healthz()


@router.get("/network_audio/sessions")
async def network_audio_sessions():
    return get_runtime().sessions()


@router.get("/network_audio/stream/{sid}")
async def network_audio_stream(sid: str):
    try:
        generator = get_runtime().stream_chunks(sid)
    except KeyError:
        raise HTTPException(status_code=404, detail="stream session not found") from None
    return StreamingResponse(generator, media_type="audio/mpeg")
