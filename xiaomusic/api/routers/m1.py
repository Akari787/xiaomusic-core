"""M1 URL-to-stream minimal workflow routes."""

from fastapi import APIRouter, Depends

from xiaomusic.api.dependencies import verification, xiaomusic
from xiaomusic.api.models import DidUrl, SidObj
from xiaomusic.m1.runtime import M1Runtime

router = APIRouter(dependencies=[Depends(verification)])
_runtime: M1Runtime | None = None


def _get_runtime() -> M1Runtime:
    global _runtime
    if _runtime is None:
        _runtime = M1Runtime(xiaomusic)
    return _runtime


@router.post("/m1/play_url")
async def m1_play_url(data: DidUrl):
    did = data.did
    if not xiaomusic.did_exist(did):
        return {"ok": False, "error_code": "E_STREAM_NOT_FOUND", "error_message": "Did not exist"}
    return await _get_runtime().play_and_cast(did=did, url=data.url)


@router.get("/m1/healthz")
async def m1_healthz():
    return _get_runtime().healthz()


@router.get("/m1/sessions")
async def m1_sessions():
    return _get_runtime().sessions()


@router.post("/m1/stop")
async def m1_stop(data: SidObj):
    return _get_runtime().stop_session(sid=data.sid)
