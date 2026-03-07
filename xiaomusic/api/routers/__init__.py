"""路由注册"""

def register_routers(app):
    """注册所有路由到应用

    Args:
        app: FastAPI 应用实例
    """
    from xiaomusic.api import websocket
    from xiaomusic.api.routers import device, file, music, network_audio, playlist, plugin, system, v1

    # 注册各个路由模块
    app.include_router(system.router, tags=["系统管理"])
    app.include_router(device.router, tags=["设备控制"])
    app.include_router(music.router, tags=["音乐管理"])
    app.include_router(playlist.router, tags=["播放列表"])
    app.include_router(plugin.router, tags=["插件管理"])
    app.include_router(v1.router, tags=["API v1"])
    app.include_router(network_audio.router, tags=["网络音频"])
    app.include_router(file.router, tags=["文件操作"])
    app.include_router(websocket.router, tags=["WebSocket"])
