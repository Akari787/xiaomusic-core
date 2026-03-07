"""路由注册"""

from __future__ import annotations

from fastapi import Depends


def _verification_dependency():
    from xiaomusic.api.dependencies import verification

    return Depends(verification)

def register_routers(app):
    """注册所有路由到应用

    Args:
        app: FastAPI 应用实例
    """
    from xiaomusic.api import websocket
    from xiaomusic.api.routers import device, file, music, network_audio, playlist, plugin, system, v1

    auth_dep = _verification_dependency()

    # 系统与页面入口。
    app.include_router(system.router, tags=["系统管理"])

    # 正式维护的 API v1 命名空间。
    app.include_router(v1.router, tags=["API v1"], dependencies=[auth_dep])

    # 非 v1 的业务与辅助路由（不属于正式 v1 收口范围）。
    app.include_router(device.router, tags=["设备控制"], dependencies=[auth_dep])
    app.include_router(music.router, tags=["音乐管理"], dependencies=[auth_dep])
    app.include_router(playlist.router, tags=["播放列表"], dependencies=[auth_dep])
    app.include_router(network_audio.router, tags=["网络音频"], dependencies=[auth_dep])
    app.include_router(file.router, tags=["文件操作"], dependencies=[auth_dep])
    app.include_router(plugin.router, tags=["插件管理"], dependencies=[auth_dep])
    app.include_router(websocket.router, tags=["WebSocket"])
