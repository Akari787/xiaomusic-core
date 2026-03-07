"""Network audio URL-to-stream workflow routes."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from xiaomusic.api import response as api_response
from xiaomusic.api.runtime_provider import get_runtime

router = APIRouter()


@router.get("/network_audio/healthz")
async def network_audio_healthz():
    return api_response.ok(get_runtime().healthz(), contract="raw")


@router.get("/network_audio/sessions")
async def network_audio_sessions():
    return api_response.ok(get_runtime().sessions(), contract="raw")


@router.get("/network_audio/stream/{sid}")
async def network_audio_stream(sid: str):
    try:
        generator = get_runtime().stream_chunks(sid)
    except KeyError:
        raise HTTPException(status_code=404, detail="stream session not found") from None
    return StreamingResponse(generator, media_type="audio/mpeg")
