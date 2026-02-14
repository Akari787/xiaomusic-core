"""系统管理路由"""
import asyncio
import json
import os
import io
import base64
import shutil
import tempfile
from dataclasses import (
    asdict,
)
from qrcode.main import QRCode
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
)
from fastapi.openapi.docs import (
    get_redoc_html,
    get_swagger_ui_html,
)
from fastapi.openapi.utils import (
    get_openapi,
)
from fastapi.responses import (
    FileResponse,
)
from starlette.background import (
    BackgroundTask,
)

from xiaomusic import (
    __version__,
)
from xiaomusic.api.dependencies import (
    config,
    log,
    verification,
    xiaomusic,
)
from xiaomusic.utils.system_utils import (
    deepcopy_data_no_sensitive_info,
    get_latest_version,
    restart_xiaomusic,
    update_version,
)
from xiaomusic.qrcode_login import MiJiaAPI
router = APIRouter(dependencies=[Depends(verification)])


def _get_mijia_api():
    # auth_data_path should be a directory
    token_path = config.oauth2_token_path if config.oauth2_token_path else ""
    auth_dir = os.path.dirname(token_path) if token_path else None
    if not auth_dir:
        auth_dir = None
    return MiJiaAPI(
        auth_data_path=auth_dir,
        token_store=getattr(xiaomusic, "token_store", None),
    )


mi_jia_api = None
qrcode_login_task = None

@router.get("/")
async def read_index():
    """首页"""
    folder = os.path.dirname(
        os.path.dirname(os.path.dirname(__file__))
    )  # xiaomusic 目录
    return FileResponse(f"{folder}/static/index.html")

@router.get("/api/get_qrcode")
async def get_qrcode():
    """生成小米账号扫码登录用二维码，返回 base64 图片 URL。"""
    global qrcode_login_task
    global mi_jia_api
    try:
        if mi_jia_api is None:
            mi_jia_api = _get_mijia_api()
        qrcode_data = mi_jia_api.get_qrcode()
        # 已登录时 get_qrcode 返回 False，无需扫码
        if qrcode_data is False:
            return {
                "success": True,
                "already_logged_in": True,
                "qrcode_url": "",
                "message": "已登录，无需扫码",
            }

        # 优先使用小米返回的官方二维码图片 URL，与扫码内容一致且最可靠
        if qrcode_data.get("qr"):
            qrcode_url = qrcode_data["qr"]
        else:
            # 无 qr 时用 loginUrl 本地生成二维码图
            qr = QRCode(version=1, box_size=8, border=2)
            qr.add_data(qrcode_data["loginUrl"])
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = io.BytesIO()
            img.save(buf, "PNG")
            buf.seek(0)
            b64 = base64.b64encode(buf.read()).decode("ascii")
            qrcode_url = f"data:image/png;base64,{b64}"
        # 返回二维码的同时，在后台启动 get_logint_status，不阻塞本次响应
        if qrcode_login_task and not qrcode_login_task.done():
            qrcode_login_task.cancel()
        qrcode_login_task = asyncio.create_task(get_logint_status(qrcode_data["lp"]))
        return {
            "success": True,
            "qrcode_url": qrcode_url,
            "status_url": qrcode_data.get("lp", ""),
            "expire_seconds": config.qrcode_timeout,
        }
    except Exception as e:
        log.exception("get_qrcode failed: %s", e)
        return {"success": False, "message": str(e)}


async def get_logint_status(lp: str):
    """轮询获取扫码登录状态"""
    try:
        await asyncio.to_thread(mi_jia_api.get_logint_status, lp)
        # 扫码登录成功后立即重建认证状态，避免前端刷新后仍使用旧会话
        await xiaomusic.reinit()
    except asyncio.CancelledError:
        log.info("qrcode login polling cancelled")
        raise
    except ValueError as e:
        log.exception("get_logint_status failed: %s", e)
    except Exception as e:
        log.exception("refresh auth after qrcode login failed: %s", e)

@router.get("/getversion")
def getversion():
    """获取版本"""
    log.debug("getversion %s", __version__)
    return {"version": __version__}


@router.get("/getsetting")
async def getsetting(need_device_list: bool = False):
    """获取设置"""
    config_data = xiaomusic.getconfig()
    data = asdict(config_data)
    data["httpauth_password"] = "******"

    def _token_valid(j: dict) -> bool:
        # oauth2 token must contain serviceToken to be usable
        st = j.get("serviceToken") or j.get("yetAnotherServiceToken")
        return bool(j.get("userId") and j.get("passToken") and j.get("ssecurity") and st)

    # oauth2 token may come from env or file; prefer token_store when available
    token_available = False
    try:
        ts = getattr(xiaomusic, "token_store", None)
        if ts is not None:
            j = ts.load().data
        elif config_data.oauth2_token_path and os.path.isfile(config_data.oauth2_token_path):
            with open(config_data.oauth2_token_path, encoding="utf-8") as f:
                j = json.load(f)
        else:
            j = {}
        token_available = _token_valid(j)
    except Exception:
        token_available = False
    data["oauth2_token_available"] = token_available
    if need_device_list:
        device_list = await xiaomusic.getalldevices()
        log.info(f"getsetting device_list: {device_list}")
        data["device_list"] = device_list
    return data


@router.get("/api/oauth2/status")
async def oauth2_status():
    global qrcode_login_task
    token_path = config.oauth2_token_path
    token_exists = bool(token_path and os.path.isfile(token_path))
    token_valid = False
    try:
        ts = getattr(xiaomusic, "token_store", None)
        if ts is not None:
            j = ts.load().data
        elif token_exists:
            with open(token_path, encoding="utf-8") as f:
                j = json.load(f)
        else:
            j = {}
        st = j.get("serviceToken") or j.get("yetAnotherServiceToken")
        token_valid = bool(j.get("userId") and j.get("passToken") and j.get("ssecurity") and st)
    except Exception:
        token_valid = False
    return {
        "success": True,
        "token_file": token_path,
        "token_exists": token_exists,
        "token_valid": token_valid,
        # Used by frontend to decide if refresh loop should continue
        "cloud_available": token_valid,
        "login_in_progress": bool(qrcode_login_task and not qrcode_login_task.done()),
    }


@router.post("/api/oauth2/logout")
async def oauth2_logout():
    """退出 OAuth2 登录并删除本地 token 文件。

    仅删除 token 文件，不修改其它配置；删除后会触发 reinit 让服务重新读取认证状态。
    """
    global qrcode_login_task
    token_path = config.oauth2_token_path

    # 如果正在轮询扫码登录，先取消
    if qrcode_login_task and not qrcode_login_task.done():
        qrcode_login_task.cancel()

    removed = False
    removed_paths = []

    # Best-effort remove token file (handle relative path ambiguity)
    candidates = []
    if token_path:
        candidates.append(token_path)
        candidates.append(os.path.abspath(token_path))
        candidates.append(os.path.join(os.getcwd(), token_path))
        candidates.append(os.path.join("/app", token_path.lstrip("/")))
    for p in [c for c in candidates if c]:
        if os.path.isfile(p):
            try:
                os.remove(p)
                removed = True
                removed_paths.append(p)
            except OSError as e:
                log.exception("remove oauth2 token file failed: %s", e)
                raise HTTPException(status_code=500, detail=f"remove token failed: {e}")

    # Clear in-memory token cache (e.g., persist_token=false)
    try:
        ts = getattr(xiaomusic, "token_store", None)
        if ts is not None:
            ts.clear()
    except Exception:
        pass

    # Clear in-memory auth/session state so it stops being treated as logged-in.
    try:
        if getattr(xiaomusic, "auth_manager", None) is not None:
            am = xiaomusic.auth_manager
            am.mina_service = None
            am.miio_service = None
            am.login_acount = None
            am.login_signature = None
            am.cookie_jar = None
            # clear aiohttp cookie jar
            try:
                am.mi_session.cookie_jar.clear()
            except Exception:
                pass
            # remove cached miservice token store if exists
            try:
                mi_token_home = os.path.join(am.config.conf_path, ".mi.token")
                if os.path.isfile(mi_token_home):
                    os.remove(mi_token_home)
            except Exception:
                pass

        # Reset MiJiaAPI cache so /api/get_qrcode won't think we're already logged-in.
        global mi_jia_api
        mi_jia_api = _get_mijia_api()

        # Reinit to apply new auth state
        await xiaomusic.reinit()
    except Exception as e:
        log.exception("cleanup after oauth2 logout failed: %s", e)
        raise HTTPException(status_code=500, detail=f"logout cleanup failed: {e}")

    return {
        "success": True,
        "removed": removed,
        "token_file": token_path,
        "removed_paths": removed_paths,
    }


@router.post("/api/jellyfin/sync")
async def jellyfin_sync():
    ret = await xiaomusic.online_music_service.sync_jellyfin_music_lists()
    if ret.get("success"):
        return ret
    raise HTTPException(status_code=400, detail=ret.get("error", "sync failed"))


@router.post("/savesetting")
async def savesetting(request: Request):
    """保存设置"""
    try:
        data_json = await request.body()
        data = json.loads(data_json.decode("utf-8"))
        debug_data = deepcopy_data_no_sensitive_info(data)
        log.info(f"saveconfig: {debug_data}")
        config_obj = xiaomusic.getconfig()
        if (
            data.get("httpauth_password") == "******"
            or data.get("httpauth_password", "") == ""
        ):
            data["httpauth_password"] = config_obj.httpauth_password
        await xiaomusic.saveconfig(data)

        # 重置 HTTP 服务器配置
        from xiaomusic.api.app import app
        from xiaomusic.api.dependencies import reset_http_server

        reset_http_server(app)

        return "save success"
    except json.JSONDecodeError as err:
        raise HTTPException(status_code=400, detail="Invalid JSON") from err


@router.post("/api/system/modifiysetting")
async def modifiysetting(request: Request):
    """修改部分设置"""
    try:
        data_json = await request.body()
        data = json.loads(data_json.decode("utf-8"))
        debug_data = deepcopy_data_no_sensitive_info(data)
        log.info(f"modifiysetting: {debug_data}")

        config_obj = xiaomusic.getconfig()

        # 处理密码字段，如果是 ****** 或空字符串则保持原值
        if "httpauth_password" in data and (
            data["httpauth_password"] == "******" or data["httpauth_password"] == ""
        ):
            data["httpauth_password"] = config_obj.httpauth_password

        # 检查是否有HTTP服务器相关配置被修改
        has_http_config_changed = any(
            config_obj.is_http_server_config(key) for key in data.keys()
        )

        # 更新配置
        config_obj.update_config(data)

        # 如果有HTTP配置变更，重置HTTP服务器
        if has_http_config_changed:
            from xiaomusic.api.app import app
            from xiaomusic.api.dependencies import reset_http_server

            reset_http_server(app)
            log.info("HTTP server configuration has been reset")

        # 保存配置到文件
        xiaomusic.save_cur_config()

        return {"success": True, "message": "Configuration updated successfully"}
    except json.JSONDecodeError as err:
        raise HTTPException(status_code=400, detail="Invalid JSON") from err
    except Exception as err:
        log.error(f"Error updating configuration: {err}")
        raise HTTPException(status_code=500, detail=str(err)) from err


@router.get("/downloadlog")
def downloadlog():
    """下载日志"""
    file_path = config.log_file
    if os.path.exists(file_path):
        # 创建一个临时文件来保存日志的快照
        temp_file = tempfile.NamedTemporaryFile(delete=False)
        try:
            with open(file_path, "rb") as f:
                shutil.copyfileobj(f, temp_file)
            temp_file.close()

            # 使用BackgroundTask在响应发送完毕后删除临时文件
            def cleanup_temp_file(tmp_file_path):
                os.remove(tmp_file_path)

            background_task = BackgroundTask(cleanup_temp_file, temp_file.name)
            return FileResponse(
                temp_file.name,
                media_type="text/plain",
                filename="xiaomusic.txt",
                background=background_task,
            )
        except Exception as e:
            os.remove(temp_file.name)
            raise HTTPException(
                status_code=500, detail="Error capturing log file"
            ) from e
    else:
        return {"message": "File not found."}


@router.get("/latestversion")
async def latest_version():
    """获取最新版本"""
    version = await get_latest_version("xiaomusic")
    if version:
        return {"ret": "OK", "version": version}
    else:
        return {"ret": "Fetch version failed"}


@router.post("/updateversion")
async def updateversion(version: str = "", lite: bool = True):
    """更新版本"""
    import asyncio

    ret = await update_version(version, lite)
    if ret != "OK":
        return {"ret": ret}

    asyncio.create_task(restart_xiaomusic())
    return {"ret": "OK"}


@router.get("/docs", include_in_schema=False)
async def get_swagger_documentation():
    """Swagger 文档"""
    return get_swagger_ui_html(openapi_url="/openapi.json", title="docs")


@router.get("/redoc", include_in_schema=False)
async def get_redoc_documentation():
    """ReDoc 文档"""
    return get_redoc_html(openapi_url="/openapi.json", title="docs")


@router.get("/openapi.json", include_in_schema=False)
async def openapi():
    """OpenAPI 规范"""
    from xiaomusic.api.app import app

    return get_openapi(title=app.title, version=app.version, routes=app.routes)
