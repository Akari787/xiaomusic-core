"""设备控制路由"""

import asyncio

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
from xiaomusic.api.models import DidCmd

from xiaomusic.security.exec_plugin import parse_exec_code

router = APIRouter(dependencies=[Depends(verification)])

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

