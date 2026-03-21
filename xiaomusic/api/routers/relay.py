"""Relay session stream workflow routes (formal paths)."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from xiaomusic.api import response as api_response
from xiaomusic.api.runtime_provider import get_runtime

router = APIRouter()


@router.get("/relay/healthz")
async def relay_healthz():
    return api_response.ok(get_runtime().healthz(), contract="raw")


@router.get("/relay/sessions")
async def relay_sessions():
    return api_response.ok(get_runtime().sessions(), contract="raw")


@router.get("/relay/stream/{sid}")
async def relay_stream(sid: str):
    try:
        generator = get_runtime().stream_chunks(sid)
    except KeyError:
        raise HTTPException(status_code=404, detail="stream session not found") from None
    return StreamingResponse(generator, media_type="audio/mpeg")
