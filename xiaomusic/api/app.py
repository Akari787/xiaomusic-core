"""FastAPI 应用实例和中间件配置"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

from xiaomusic import __version__
from xiaomusic.api.dependencies import (
    AuthStaticFiles,
    reset_http_server,
)

if TYPE_CHECKING:
    from xiaomusic.xiaomusic import XiaoMusic

# 导入内部状态管理器
from xiaomusic.api.dependencies import _state


@asynccontextmanager
async def app_lifespan(app):
    """应用生命周期管理"""
    task = None
    if _state.is_initialized():
        task = asyncio.create_task(_state._xiaomusic.run_forever())
    try:
        yield
    except asyncio.CancelledError:
        # 正常关闭时的取消，不需要记录
        pass
    finally:
        # 关闭时取消后台任务
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                if _state.is_initialized():
                    _state._log.info("Background task cleanup: CancelledError")
            except Exception as e:
                if _state.is_initialized():
                    _state._log.error(f"Background task cleanup error: {e}")


# 创建 FastAPI 应用实例
app = FastAPI(
    lifespan=app_lifespan,
    version=__version__,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


# Map security-related errors (e.g. exec# governance) to a safe HTTP response.
try:
    from xiaomusic.security.errors import (
        ExecDisabledError,
        ExecNotAllowedError,
        ExecValidationError,
        OutboundBlockedError,
        SelfUpdateDisabledError,
        SecurityError,
    )

    @app.exception_handler(SecurityError)
    async def _handle_security_error(request, exc):
        # Keep response and logs generic; details may contain user input.
        logging.getLogger("xiaomusic.security").warning(
            "SECURITY: blocked request: %s", exc.__class__.__name__
        )
        if isinstance(exc, ExecDisabledError):
            detail = "exec plugin disabled"
        elif isinstance(exc, ExecNotAllowedError):
            detail = "exec command not allowed"
        elif isinstance(exc, ExecValidationError):
            detail = "invalid exec command"
        elif isinstance(exc, OutboundBlockedError):
            detail = "outbound blocked"
        elif isinstance(exc, SelfUpdateDisabledError):
            detail = "self update disabled"
        else:
            detail = "blocked by security policy"
        return JSONResponse(status_code=403, content={"detail": detail})

except Exception:
    # If security modules are unavailable for some reason, don't break app import.
    pass

def _configure_cors(app: FastAPI, allow_origins: list[str]):
    # Remove existing CORSMiddleware (if any) and re-add with current config.
    app.user_middleware = [
        m for m in app.user_middleware if m.cls is not CORSMiddleware
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=False,
        allow_methods=["*"] ,
        allow_headers=["*"],
    )
    app.middleware_stack = app.build_middleware_stack()

# 添加 GZip 中间件
app.add_middleware(GZipMiddleware, minimum_size=500)


def HttpInit(_xiaomusic: "XiaoMusic"):
    """初始化 HTTP 服务器

    Args:
        _xiaomusic: XiaoMusic 实例
    """
    # 初始化应用状态
    _state.initialize(_xiaomusic)

    # Configure CORS based on config (default localhost-only).
    origins = list(getattr(_xiaomusic.config, "cors_allow_origins", []) or [])
    if not origins:
        origins = ["http://localhost", "http://127.0.0.1"]
    _configure_cors(app, origins)

    # 挂载静态文件
    folder = os.path.dirname(os.path.dirname(__file__))  # xiaomusic 目录
    app.mount("/static", AuthStaticFiles(directory=f"{folder}/static"), name="static")

    # Optional: host separated webui build output under /webui.
    repo_root = os.path.dirname(folder)
    webui_dist = os.getenv(
        "XIAOMUSIC_WEBUI_DIST_PATH",
        os.path.join(repo_root, "webui", "dist"),
    )
    if os.path.isdir(webui_dist):
        if not any(getattr(r, "path", "") == "/webui" for r in app.routes):
            app.mount(
                "/webui",
                StaticFiles(directory=webui_dist, html=True),
                name="webui",
            )

    # 注册所有路由
    from xiaomusic.api.routers import register_routers

    register_routers(app)

    # 重置 HTTP 服务器配置
    reset_http_server(app)
