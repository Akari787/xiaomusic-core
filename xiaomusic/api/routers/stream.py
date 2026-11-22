"""Speaker-facing stream addresses issued by StreamGateway (ADR-0007).

Media data plane: anonymous like /music, because the speaker cannot send
credentials. Only signed addresses are served.
"""

from __future__ import annotations

import aiohttp
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from xiaomusic.api.dependencies import log, xiaomusic
from xiaomusic.api.routers import file as file_routes
from xiaomusic.stream_gateway import HttpTarget, LocalTarget

router = APIRouter()

_PASS_HEADERS = ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges")


def _gateway():
    try:
        gw = getattr(xiaomusic, "stream_gateway", None)
    except RuntimeError:  # app not initialized
        gw = None
    if gw is None:
        raise HTTPException(status_code=503, detail="stream gateway unavailable")
    return gw


async def _proxy(url: str, range_header: str | None) -> Response:
    session = aiohttp.ClientSession(
        # No total timeout: a long track can take longer than any fixed limit.
        timeout=aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=300),
    )
    headers = {"Range": range_header} if range_header else {}
    try:
        resp = await session.get(url, headers=headers, allow_redirects=True)
    except Exception as exc:
        await session.close()
        log.warning("stream_gateway upstream_error=%s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="upstream unavailable") from exc
    if resp.status not in (200, 206, 416):
        status = resp.status
        resp.release()
        await session.close()
        log.warning("stream_gateway upstream_status=%s", status)
        raise HTTPException(status_code=502, detail=f"upstream status {status}")

    out_headers = {k: resp.headers[k] for k in _PASS_HEADERS if k in resp.headers}

    async def body():
        try:
            async for chunk in resp.content.iter_chunked(64 * 1024):
                yield chunk
        finally:
            resp.release()
            await session.close()

    return StreamingResponse(
        body(),
        status_code=resp.status,
        headers=out_headers,
        media_type=resp.headers.get("Content-Type", "audio/mpeg"),
    )


@router.get("/stream/v1/{did}/{queue}/{index}/{source}/{item}")
async def stream(
    request: Request,
    did: str,
    queue: str,
    index: int,
    source: str,
    item: str,
    sig: str = "",
):
    gw = _gateway()
    ref = gw.verify(did, queue, index, source, item, sig)
    if ref is None:
        raise HTTPException(status_code=404, detail="not found")
    range_header = request.headers.get("range")
    gw.tracker.observe(ref, range_header)
    try:
        target = gw.resolve(ref)
    except LookupError as exc:
        raise HTTPException(status_code=503, detail="source unavailable") from exc
    if isinstance(target, LocalTarget):
        # Same handling as /music (path checks, m4a conversion, Range).
        return await file_routes.music_file(request, target.relative_path)
    if isinstance(target, HttpTarget):
        return await _proxy(target.url, range_header)
    raise HTTPException(status_code=404, detail="not found")


@router.options("/stream/v1/{did}/{queue}/{index}/{source}/{item}")
async def stream_options():
    return Response(headers={"Accept-Ranges": "bytes"})
