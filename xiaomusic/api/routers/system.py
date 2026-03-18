"""系统管理路由"""

import asyncio
import base64
import io
import json
import os
import shutil
import tempfile
import time
from dataclasses import asdict
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
    RedirectResponse,
)
from starlette.background import (
    BackgroundTask,
)

from xiaomusic.api import response as api_response
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


@router.get("/diagnostics")
async def diagnostics():
    """Runtime diagnostics (startup self-check, keyword conflicts, etc.)."""
    startup = getattr(xiaomusic, "startup_diagnostics", None)
    return api_response.ok(
        {
            "startup": asdict(startup) if startup is not None else None,
            "keyword_override_mode": getattr(config, "keyword_override_mode", "override"),
            "keyword_conflicts": list(getattr(config, "keyword_conflicts", []) or []),
            "last_download_result": getattr(xiaomusic, "last_download_result", None),
        },
        contract="raw",
    )


def _get_mijia_api():
    # auth_data_path should be a directory
    token_path = config.auth_token_path if config.auth_token_path else ""
    auth_dir = os.path.dirname(token_path) if token_path else None
    if not auth_dir:
        auth_dir = None
    return MiJiaAPI(
        auth_data_path=auth_dir,
        token_store=getattr(xiaomusic, "token_store", None),
    )


mi_jia_api = None
qrcode_login_task = None
qrcode_login_started_at = 0.0
qrcode_login_error = ""


async def _runtime_auth_ready() -> bool:
    """Best-effort runtime auth readiness without forcing network relogin."""
    try:
        am = getattr(xiaomusic, "auth_manager", None)
        if am is None:
            return False
        return not await am.need_login()
    except Exception:
        return False

@router.get("/")
async def read_index():
    """首页"""
    return RedirectResponse(url="/webui/", status_code=302)


@router.get("/debug/api-v1")
async def debug_api_v1_index():
    """API v1 调试页入口。"""
    return RedirectResponse(url="/webui/#/debug/api-v1", status_code=302)

@router.get("/api/get_qrcode")
async def get_qrcode():
    """生成小米账号扫码登录用二维码，返回 base64 图片 URL。"""
    global qrcode_login_task
    global mi_jia_api
    global qrcode_login_started_at
    global qrcode_login_error
    try:
        # Always rebuild API object to avoid stale in-memory cookies/session.
        mi_jia_api = _get_mijia_api()
        qrcode_login_error = ""
        qrcode_data = mi_jia_api.get_qrcode()
        if isinstance(qrcode_data, dict) and qrcode_data.get("ok") is False:
            return api_response.fail(
                "E_INTERNAL",
                qrcode_data.get("error", {}).get("message", "外部服务不可用"),
                contract="success_error",
                error=qrcode_data.get("error", {}),
            )
        # 已登录时 get_qrcode 返回 False，无需扫码
        if qrcode_data is False:
            return api_response.ok(
                {
                    "already_logged_in": True,
                    "qrcode_url": "",
                    "message": "已登录，无需扫码",
                },
                contract="success_error",
            )

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
        qrcode_login_task = asyncio.create_task(get_logint_status(mi_jia_api, qrcode_data["lp"]))
        qrcode_login_started_at = time.time()
        return api_response.ok(
            {
                "qrcode_url": qrcode_url,
                "status_url": qrcode_data.get("lp", ""),
                "expire_seconds": config.qrcode_timeout,
            },
            contract="success_error",
        )
    except Exception as e:
        qrcode_login_error = str(e)
        log.exception("get_qrcode failed: %s", e)
        return api_response.fail(
            "E_INTERNAL", str(e), contract="success_error", exc=e
        )


async def get_logint_status(api: MiJiaAPI, lp: str):
    """轮询获取扫码登录状态"""
    global qrcode_login_error
    try:
        await asyncio.to_thread(api.get_logint_status, lp)
        qrcode_login_error = ""
        # 扫码登录成功后立即重建认证状态，避免前端刷新后仍使用旧会话
        await xiaomusic.reinit()
        # If auth was previously locked by backoff/circuit-breaker,
        # clear the lock after successful QR login + reinit.
        try:
            am = getattr(xiaomusic, "auth_manager", None)
            if am is not None and hasattr(am, "clear_auth_lock"):
                am.clear_auth_lock(reason="qrcode_login_success", mode="healthy")
        except Exception:
            log.exception("clear auth lock after qrcode login failed")
    except asyncio.CancelledError:
        log.info("qrcode login polling cancelled")
        raise
    except ValueError as e:
        qrcode_login_error = str(e)
        log.exception("get_logint_status failed: %s", e)
    except Exception as e:
        qrcode_login_error = str(e)
        log.exception("refresh auth after qrcode login failed: %s", e)

@router.get("/getversion")
def getversion():
    """获取版本"""
    log.debug("getversion %s", __version__)
    return api_response.ok({"version": __version__}, contract="raw")


@router.get("/getsetting")
async def getsetting(need_device_list: bool = False):
    """获取设置"""
    config_data = xiaomusic.getconfig()
    data = asdict(config_data)
    data["httpauth_password"] = "******"
    # Never expose secrets to the browser.
    if data.get("jellyfin_api_key"):
        data["jellyfin_api_key"] = "******"

    def _token_valid(j: dict) -> bool:
        # auth token must contain serviceToken to be usable
        st = j.get("serviceToken") or j.get("yetAnotherServiceToken")
        return bool(j.get("userId") and j.get("passToken") and j.get("ssecurity") and st)

    # auth token may come from env or file; prefer token_store when available
    token_available = False
    try:
        ts = getattr(xiaomusic, "token_store", None)
        if ts is not None:
            j = ts.get()
        else:
            j = {}
        token_available = _token_valid(j)
    except Exception:
        token_available = False
    runtime_ready = await _runtime_auth_ready()
    data["auth_token_available"] = token_available
    data["auth_runtime_ready"] = runtime_ready
    if need_device_list:
        device_list = await xiaomusic.getalldevices()
        log.info(f"getsetting device_list: {device_list}")
        data["device_list"] = device_list
    return api_response.ok(data, contract="raw")


@router.get("/api/auth/status")
async def auth_status():
    global qrcode_login_task
    global qrcode_login_started_at
    global qrcode_login_error
    token_path = config.auth_token_path
    token_exists = bool(token_path and os.path.isfile(token_path))
    token_valid = False
    persistent_auth_available = False
    short_session_available = False
    try:
        ts = getattr(xiaomusic, "token_store", None)
        if ts is not None:
            j = ts.get()
            token_exists = bool(getattr(ts, "path", None) and ts.path.exists())
        else:
            j = {}
        st = j.get("serviceToken") or j.get("yetAnotherServiceToken")
        persistent_auth_available = bool(
            j.get("userId")
            and j.get("passToken")
            and j.get("psecurity")
            and j.get("ssecurity")
            and j.get("cUserId")
            and j.get("deviceId")
        )
        short_session_available = bool(st)
        token_valid = bool(j.get("userId") and j.get("passToken") and j.get("ssecurity") and st)
    except Exception:
        token_valid = False
        persistent_auth_available = False
        short_session_available = False
    runtime_ready = await _runtime_auth_ready()
    login_in_progress = bool(qrcode_login_task and not qrcode_login_task.done())
    auth_state = {}
    try:
        am = getattr(xiaomusic, "auth_manager", None)
        if am is not None and hasattr(am, "auth_status_snapshot"):
            auth_state = am.auth_status_snapshot()
    except Exception:
        auth_state = {}
    auth_debug = {}
    try:
        am = getattr(xiaomusic, "auth_manager", None)
        if am is not None and hasattr(am, "auth_debug_state"):
            auth_debug = am.auth_debug_state()
    except Exception:
        auth_debug = {}
    rebuild_debug = {}
    rebuild_failed = False
    rebuild_error_code = ""
    rebuild_failed_reason = ""
    try:
        am = getattr(xiaomusic, "auth_manager", None)
        if am is not None and hasattr(am, "auth_short_session_rebuild_debug_state"):
            rebuild_debug = am.auth_short_session_rebuild_debug_state()
            last_rebuild = rebuild_debug.get("last_short_session_rebuild", {})
            last_flow = rebuild_debug.get("last_auth_recovery_flow", {})
            last_rebuild_result = last_rebuild.get("result", "")
            last_flow_result = last_flow.get("result", "")
            rebuild_failed = last_rebuild_result == "failed" or last_flow_result == "failed"
            rebuild_error_code = str(last_rebuild.get("error_code", "") or last_flow.get("error_code", ""))
            rebuild_failed_reason = str(
                last_rebuild.get("failed_reason", "")
                or last_rebuild.get("error_message", "")
                or last_flow.get("failed_reason", "")
                or last_flow.get("error_message", "")
            )
    except Exception:
        rebuild_debug = {}
    if login_in_progress:
        expire_after = int(getattr(config, "qrcode_timeout", 120)) + 15
        if qrcode_login_started_at > 0 and (time.time() - qrcode_login_started_at) > expire_after:
            try:
                qrcode_login_task.cancel()
            except Exception:
                pass
            login_in_progress = False

    status_reason = "healthy"
    status_reason_detail = ""
    if bool(auth_state.get("locked", False)):
        status_reason = "manual_login_required"
        status_reason_detail = str(auth_state.get("lock_reason", "") or "auth locked")
    elif not persistent_auth_available:
        status_reason = "persistent_auth_missing"
        status_reason_detail = "all long-lived auth fields missing from token"
    elif persistent_auth_available and not short_session_available:
        if rebuild_failed:
            status_reason = "short_session_rebuild_failed"
            status_reason_detail = f"rebuild failed: {rebuild_error_code}"
        else:
            status_reason = "short_session_missing"
            status_reason_detail = "short-lived session tokens missing"
    elif persistent_auth_available and short_session_available and not runtime_ready:
        status_reason = "runtime_not_ready"
        status_reason_detail = "runtime auth ready but not verified"

    return api_response.ok(
        {
            "token_file": token_path,
            "auth_token_file": token_path,
            "token_exists": token_exists,
            "token_valid": token_valid,
            "cloud_available": token_valid,
            "runtime_auth_ready": runtime_ready,
            "persistent_auth_available": persistent_auth_available,
            "short_session_available": short_session_available,
            "status_reason": status_reason,
            "status_reason_detail": status_reason_detail,
            "rebuild_failed": rebuild_failed,
            "rebuild_error_code": rebuild_error_code,
            "rebuild_failed_reason": rebuild_failed_reason[:200] if rebuild_failed_reason else "",
            "login_in_progress": login_in_progress,
            "last_error": qrcode_login_error or auth_debug.get("last_auth_error", ""),
            "auth_mode": auth_state.get("mode", "healthy"),
            "auth_locked": bool(auth_state.get("locked", False)),
            "auth_lock_until": auth_state.get("locked_until_ts"),
            "auth_lock_reason": auth_state.get("lock_reason", ""),
        },
        contract="success_error",
    )


@router.post("/api/auth/logout")
async def auth_logout():
    """退出认证登录并删除本地 token 文件。

    仅删除 token 文件，不修改其它配置；删除后会触发 reinit 让服务重新读取认证状态。
    """
    global qrcode_login_task
    global qrcode_login_started_at
    global qrcode_login_error
    token_path = config.auth_token_path

    # 如果正在轮询扫码登录，先取消
    if qrcode_login_task and not qrcode_login_task.done():
        qrcode_login_task.cancel()
    qrcode_login_started_at = 0.0
    qrcode_login_error = ""

    removed = False
    removed_paths = []

    # Clear token through TokenStore only.
    try:
        ts = getattr(xiaomusic, "token_store", None)
        if ts is not None:
            removed, removed_paths = ts.clear_and_remove()
    except Exception:
        log.exception("remove auth token file failed")
        raise HTTPException(status_code=500, detail="remove token failed")

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
        log.exception("cleanup after auth logout failed: %s", e)
        raise HTTPException(status_code=500, detail=f"logout cleanup failed: {e}")

    return api_response.ok(
        {
            "removed": removed,
            "token_file": token_path,
            "auth_token_file": token_path,
            "removed_paths": removed_paths,
        },
        contract="success_error",
    )


@router.post("/api/auth/refresh")
async def auth_refresh():
    """手动触发认证运行时重载（从磁盘重新装载 auth.json 并重建会话）。"""
    am = getattr(xiaomusic, "auth_manager", None)
    if am is None:
        raise HTTPException(status_code=503, detail="auth manager unavailable")
    try:
        if hasattr(am, "manual_reload_runtime"):
            ret = await am.manual_reload_runtime(reason="manual_refresh_runtime")
        else:
            ret = await am.manual_refresh(reason="manual_refresh_runtime")
        return api_response.ok(ret, contract="raw")
    except Exception as e:
        log.exception("auth refresh runtime failed: %s", e)
        return api_response.ok(
            {
                "refreshed": False,
                "runtime_auth_ready": False,
                "token_saved": False,
                "token_loaded": False,
                "token_store_reloaded": False,
                "runtime_rebound": False,
                "device_map_refreshed": False,
                "verify_result": "failed",
                "last_error": str(e),
                "error_code": type(e).__name__,
                "timestamps": {
                    "saveTime": None,
                    "last_ok_ts": None,
                    "last_refresh_ts": None,
                },
            },
            contract="raw",
        )


@router.post("/api/auth/refresh_runtime")
async def auth_refresh_runtime():
    """显式认证运行时重载入口，行为与 /api/auth/refresh 一致。"""
    return await auth_refresh()


@router.post("/api/jellyfin/sync")
async def jellyfin_sync():
    ret = await xiaomusic.online_music_service.sync_jellyfin_music_lists()
    if ret.get("success"):
        return api_response.ok(ret, contract="raw")
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

        # Jellyfin API key should not be displayed; keep existing unless user overrides.
        # Some browsers clear password fields on refresh, so empty string should also keep old value.
        if data.get("jellyfin_api_key") in {"******", ""}:
            data["jellyfin_api_key"] = config_obj.jellyfin_api_key
        await xiaomusic.saveconfig(data)

        # 重置 HTTP 服务器配置
        from xiaomusic.api.app import app
        from xiaomusic.api.dependencies import reset_http_server

        reset_http_server(app)

        return api_response.ok("save success", contract="raw")
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

        # Jellyfin API key should not be displayed; keep existing unless user overrides.
        # Some browsers clear password fields on refresh, so empty string should also keep old value.
        if "jellyfin_api_key" in data and data["jellyfin_api_key"] in {"******", ""}:
            data["jellyfin_api_key"] = config_obj.jellyfin_api_key

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

        return api_response.ok(
            {"message": "Configuration updated successfully"},
            contract="success_error",
        )
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
        return api_response.ok({"message": "File not found."}, contract="raw")


@router.get("/latestversion")
async def latest_version():
    """获取最新版本"""
    version = await get_latest_version("xiaomusic")
    if version:
        return api_response.ok({"version": version}, contract="ret")
    else:
        return api_response.ok(contract="ret", ret="Fetch version failed")


@router.post("/updateversion")
async def updateversion(version: str = "", lite: bool = True):
    """更新版本"""
    import asyncio

    try:
        ret = await update_version(config, version, lite)
    except Exception as e:
        return api_response.ok(contract="ret", ret=str(e))

    if ret != "OK":
        return api_response.ok(contract="ret", ret=ret)

    asyncio.create_task(restart_xiaomusic())
    return api_response.ok(contract="ret")


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
