"""设备控制路由"""

from fastapi import APIRouter, Depends

from xiaomusic.api import response as api_response
from xiaomusic.api.dependencies import (
    log,
    verification,
    xiaomusic,
)
from xiaomusic.api.models import (
    Did,
    DidCmd,
    DidVolume,
)
from xiaomusic.playback.facade import PlaybackFacade

router = APIRouter(dependencies=[Depends(verification)])
_facade: PlaybackFacade | None = None


def _get_facade() -> PlaybackFacade:
    global _facade
    if _facade is None:
        _facade = PlaybackFacade(xiaomusic)
    return _facade

@router.get("/device_list")
async def device_list():
    """获取设备列表"""
    devices = await xiaomusic.getalldevices()
    return api_response.ok({"devices": devices}, contract="raw")

@router.get("/getvolume")
async def getvolume(did: str = ""):
    """获取音量"""
    if not xiaomusic.did_exist(did):
        return api_response.ok({"volume": 0}, contract="raw")

    volume = await xiaomusic.get_volume(did=did)
    return api_response.ok({"volume": volume}, contract="raw")


@router.get("/getplayerstatus")
async def getplayerstatus(did: str = ""):
    """获取完整播放状态（deprecated wrapper，内部已收敛到统一状态入口）

    返回小米音箱的完整播放状态，包括：
    - status: 播放状态 (0=停止, 1=播放)
    - volume: 音量
    - play_song_detail: 播放详情
        - position: 当前播放位置（毫秒）
        - duration: 总时长（毫秒）
    """
    if not xiaomusic.did_exist(did):
        return api_response.ok({"status": 0, "volume": 0}, contract="raw")

    log.warning(
        "deprecated_endpoint endpoint=/getplayerstatus replacement=/api/v1/player/state caller_should_migrate=true"
    )
    out = await _get_facade().status(did)
    return out["raw"]


@router.post("/setvolume")
async def setvolume(data: DidVolume):
    """设置音量"""
    did = data.did
    volume = data.volume
    if not xiaomusic.did_exist(did):
        return api_response.ok(contract="ret", ret="Did not exist")

    log.warning(
        "deprecated_endpoint endpoint=/setvolume replacement=/api/v1/control/volume caller_should_migrate=true"
    )
    log.info(f"set_volume {did} {volume}")
    out = await _get_facade().set_volume(did, int(volume))
    if out.get("status") != "ok":
        return api_response.ok(contract="ret", ret="Did not exist")
    return api_response.ok({"volume": volume}, contract="ret")


@router.post("/cmd")
async def do_cmd(data: DidCmd):
    """执行命令（deprecated，已不再作为正式 API）"""
    log.warning(
        "deprecated_endpoint endpoint=/cmd replacement=/api/v1/* caller_should_migrate=true did=%s cmd=%s",
        data.did,
        data.cmd,
    )
    return api_response.fail(
        "E_CMD_DEPRECATED",
        "/cmd has been deprecated; use structured /api/v1/* endpoints instead",
        http_status=410,
        contract="success_error",
        deprecated=True,
        replacement=[
            "/api/v1/control/previous",
            "/api/v1/control/next",
            "/api/v1/control/play-mode",
            "/api/v1/control/shutdown-timer",
            "/api/v1/library/favorites/add",
            "/api/v1/library/favorites/remove",
            "/api/v1/playlist/play",
            "/api/v1/playlist/play-index",
            "/api/v1/library/refresh",
        ],
    )


@router.get("/cmdstatus")
async def cmd_status():
    """命令状态"""
    finish = await xiaomusic.is_task_finish()
    if finish:
        return api_response.ok({"status": "finish"}, contract="ret")
    return api_response.ok({"status": "running"}, contract="ret")


@router.get("/playtts")
async def playtts(did: str, text: str):
    """播放 TTS"""
    if not xiaomusic.did_exist(did):
        return api_response.ok(contract="ret", ret="Did not exist")

    log.warning(
        "deprecated_endpoint endpoint=/playtts replacement=/api/v1/control/tts caller_should_migrate=true"
    )
    log.info(f"tts {did} {text}")
    out = await _get_facade().tts(did, text)
    if out.get("status") != "ok":
        return api_response.ok(contract="ret", ret="Did not exist")
    return api_response.ok(contract="ret")


@router.post("/device/stop")
async def stop(data: Did):
    """关机（deprecated wrapper，内部已收敛到统一停止入口）"""
    did = data.did
    log.info(f"stop did:{did}")
    if not xiaomusic.did_exist(did):
        return api_response.ok(contract="ret", ret="Did not exist")

    log.warning(
        "deprecated_endpoint endpoint=/device/stop replacement=/api/v1/control/stop caller_should_migrate=true"
    )
    try:
        await _get_facade().stop_legacy({"speaker_id": did})
    except Exception as e:
        log.warning(f"Execption {e}")
    return api_response.ok(contract="ret")
