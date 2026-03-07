"""设备控制路由"""

import asyncio
import urllib.parse

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
)

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

from xiaomusic.security.exec_plugin import parse_exec_code

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

    out = await _get_facade().status({"speaker_id": did})
    return out["raw"]


@router.post("/setvolume")
async def setvolume(data: DidVolume):
    """设置音量"""
    did = data.did
    volume = data.volume
    if not xiaomusic.did_exist(did):
        return api_response.ok(contract="ret", ret="Did not exist")

    log.info(f"set_volume {did} {volume}")
    out = await _get_facade().set_volume(did, int(volume))
    if not out.get("ok"):
        return api_response.ok(contract="ret", ret="Did not exist")
    return api_response.ok({"volume": volume}, contract="ret")


@router.post("/cmd")
async def do_cmd(data: DidCmd):
    """执行命令"""
    did = data.did
    cmd = data.cmd
    log.info(f"docmd. did:{did} cmd:{cmd}")
    if not xiaomusic.did_exist(did):
        return api_response.ok(contract="ret", ret="Did not exist")

    if len(cmd) > 0:
        try:
            # Block exec# at HTTP layer when disabled/not allowed.
            device = xiaomusic.device_manager.devices.get(did)
            if device is not None and xiaomusic.command_handler is not None:
                opvalue, oparg = xiaomusic.command_handler.match_cmd(
                    device, cmd, ctrl_panel=True
                )
                if opvalue == "exec":
                    if not xiaomusic.config.enable_exec_plugin:
                        raise HTTPException(status_code=403, detail="exec plugin disabled")
                    call = parse_exec_code(str(oparg))
                    allowed = set(xiaomusic.config.allowed_exec_commands or [])
                    if call.command not in allowed:
                        raise HTTPException(status_code=403, detail="exec command not allowed")

            await xiaomusic.cancel_all_tasks()
            task = asyncio.create_task(xiaomusic.do_check_cmd(did=did, query=cmd))
            xiaomusic.append_running_task(task)
        except HTTPException:
            # Make sure FastAPI returns the intended HTTP error.
            raise
        except Exception as e:
            log.warning(f"Exception {e}")
        return api_response.ok(contract="ret")
    return api_response.ok(contract="ret", ret="Unknow cmd")


@router.get("/cmdstatus")
async def cmd_status():
    """命令状态"""
    finish = await xiaomusic.is_task_finish()
    if finish:
        return api_response.ok({"status": "finish"}, contract="ret")
    return api_response.ok({"status": "running"}, contract="ret")


@router.get("/playurl")
async def playurl(did: str, url: str):
    """播放 URL（deprecated wrapper，内部已收敛到统一播放入口）"""
    if not xiaomusic.did_exist(did):
        return api_response.ok(contract="ret", ret="Did not exist")
    decoded_url = urllib.parse.unquote(url)
    log.info(f"playurl did: {did} url: {decoded_url}")
    out = await _get_facade().play_url(
        url=decoded_url,
        speaker_id=did,
        options={"mode": "core"},
    )
    return out["raw"].get("cast_ret", out["raw"])


@router.get("/playtts")
async def playtts(did: str, text: str):
    """播放 TTS"""
    if not xiaomusic.did_exist(did):
        return api_response.ok(contract="ret", ret="Did not exist")

    log.info(f"tts {did} {text}")
    out = await _get_facade().tts(did, text)
    if not out.get("ok"):
        return api_response.ok(contract="ret", ret="Did not exist")
    return api_response.ok(contract="ret")


@router.post("/device/stop")
async def stop(data: Did):
    """关机（deprecated wrapper，内部已收敛到统一停止入口）"""
    did = data.did
    log.info(f"stop did:{did}")
    if not xiaomusic.did_exist(did):
        return api_response.ok(contract="ret", ret="Did not exist")

    try:
        await _get_facade().stop_legacy({"speaker_id": did})
    except Exception as e:
        log.warning(f"Execption {e}")
    return api_response.ok(contract="ret")
