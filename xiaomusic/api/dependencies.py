"""依赖注入和认证相关功能"""

import secrets
from typing import (
    TYPE_CHECKING,
    Annotated,
)

import bcrypt
from fastapi import (
    Depends,
    HTTPException,
    Request,
    status,
)
from fastapi.security import (
    HTTPBasic,
    HTTPBasicCredentials,
)
from fastapi.staticfiles import StaticFiles

from xiaomusic.core.settings import get_auth_settings

if TYPE_CHECKING:
    import logging

    from xiaomusic.config import Config
    from xiaomusic.xiaomusic import XiaoMusic

security = HTTPBasic()


class _AppStateProxy:
    """应用状态代理类

    提供类似全局变量的访问方式，但实际上是动态获取的。
    这样既保持了代码的简洁性，又避免了真正的全局变量。
    """

    def __init__(self):
        self._xiaomusic: XiaoMusic | None = None
        self._config: Config | None = None
        self._log: logging.Logger | None = None

    def initialize(self, xiaomusic_instance: "XiaoMusic"):
        """初始化应用状态

        Args:
            xiaomusic_instance: XiaoMusic 实例
        """
        self._xiaomusic = xiaomusic_instance
        self._config = xiaomusic_instance.config
        self._log = xiaomusic_instance.log

    def is_initialized(self) -> bool:
        """检查是否已初始化"""
        return self._xiaomusic is not None


# 创建内部状态管理器
_state = _AppStateProxy()


class _LazyProxy:
    """延迟代理类，用于模拟全局变量"""

    def __init__(self, attr_name: str):
        self._attr_name = attr_name

    def __getattr__(self, name):
        """代理所有属性访问"""
        obj = getattr(_state, self._attr_name)
        if obj is None:
            raise RuntimeError(
                f"{self._attr_name} not initialized. Call initialize() first."
            )
        return getattr(obj, name)

    def __call__(self, *args, **kwargs):
        """代理函数调用"""
        obj = getattr(_state, self._attr_name)
        if obj is None:
            raise RuntimeError(
                f"{self._attr_name} not initialized. Call initialize() first."
            )
        return obj(*args, **kwargs)

    def __bool__(self):
        """支持布尔判断"""
        obj = getattr(_state, self._attr_name)
        return obj is not None and bool(obj)

    def __repr__(self):
        obj = getattr(_state, self._attr_name)
        return repr(obj) if obj is not None else f"<Uninitialized {self._attr_name}>"


# 创建代理对象，可以像普通变量一样使用
# 添加类型注解以支持 IDE 代码跳转和补全
xiaomusic: "XiaoMusic" = _LazyProxy("_xiaomusic")  # type: ignore
config: "Config" = _LazyProxy("_config")  # type: ignore
log: "logging.Logger" = _LazyProxy("_log")  # type: ignore


def _verify_basic_credentials(credentials: HTTPBasicCredentials) -> bool:
    """校验 Basic 凭据；调用方决定是否启用该校验。"""
    current_username_bytes = credentials.username.encode("utf8")
    correct_username_bytes = config.httpauth_username.encode("utf8")
    is_correct_username = secrets.compare_digest(
        current_username_bytes, correct_username_bytes
    )
    settings = get_auth_settings()
    try:
        is_correct_password = bcrypt.checkpw(
            credentials.password.encode("utf8"),
            settings.HTTP_AUTH_HASH.encode("utf8"),
        )
    except ValueError:
        is_correct_password = False
    if not (is_correct_username and is_correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return True


def verification(
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
):
    """HTTP Basic 认证（可由全局 legacy no-auth 模式覆盖）。"""
    return _verify_basic_credentials(credentials)


def strict_verification(
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
):
    """敏感路由的强制 Basic 认证，不受全局 no-auth override 影响。"""
    return _verify_basic_credentials(credentials)


def no_verification():
    """无认证模式"""
    return True


class AuthStaticFiles(StaticFiles):
    """需要认证的静态文件服务"""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

    async def __call__(self, scope, receive, send) -> None:
        request = Request(scope, receive)
        root_path = str(scope.get("root_path") or "").rstrip("/")
        request_path = str(scope.get("path") or "")
        public_audio_paths = {"/static/silence.mp3", "/static/search.mp3"}
        path_candidates = {
            request_path,
            request.url.path,
            f"{root_path}{request_path}",
        }
        is_public_audio = bool(path_candidates & public_audio_paths)
        if not config.disable_httpauth and not is_public_audio:
            verification(await security(request))
        await super().__call__(scope, receive, send)


def reset_http_server(app):
    """重置 HTTP 服务器配置"""
    log.info(f"disable_httpauth:{config.disable_httpauth}")
    if config.disable_httpauth:
        app.dependency_overrides[verification] = no_verification
    else:
        app.dependency_overrides = {}
