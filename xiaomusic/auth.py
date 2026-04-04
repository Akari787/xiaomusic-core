"""
简化版认证管理模块

核心设计原则：
1. 恢复失败就重登，不尝试复用旧 short session
2. 简化状态机：HEALTHY → DEGRADED → LOCKED
3. 播放前主动探测，超过30秒没活动先探测
4. 失败自动重试一次，重试前触发后台恢复
"""

import asyncio
import json
import os
import time
from typing import Any, Callable, TypeVar

from aiohttp import ClientSession
from miservice import MiAccount, MiIOService, MiNAService

from xiaomusic.const import COOKIE_TEMPLATE
from xiaomusic.utils.system_utils import (
    parse_cookie_string,
    parse_cookie_string_to_dict,
)

# 认证错误关键词
AUTH_ERROR_KEYWORDS = (
    "login failed",
    "unauthorized",
    "invalid token",
    "service token expired",
    "servicetoken invalid",
    "servicetoken expired",
    "token expired",
    "401",
    "403",
)

# 网络错误关键词
NETWORK_ERROR_KEYWORDS = (
    "timeout",
    "timed out",
    "connection reset",
    "connection aborted",
    "connection refused",
    "name or service not known",
    "temporary failure in name resolution",
    "dns",
    "network is unreachable",
    "remote disconnected",
    "502",
    "503",
    "504",
)

T = TypeVar("T")


def is_auth_error(exc=None, resp=None, body=None) -> bool:
    """判断是否是认证错误"""
    status = None
    if resp is not None:
        status = getattr(resp, "status", None)
    if status is None and exc is not None:
        status = getattr(exc, "status", None)
    if status is None and exc is not None:
        status = getattr(exc, "code", None)
    if status in (401, 403):
        return True

    text_parts = []
    if body is not None:
        if isinstance(body, dict):
            for key in ("code", "message", "msg", "error", "detail"):
                val = body.get(key)
                if val is not None:
                    text_parts.append(str(val))
        else:
            text_parts.append(str(body))
    if exc is not None:
        text_parts.append(str(exc))

    lowered = " ".join(text_parts).lower()
    return any(word in lowered for word in AUTH_ERROR_KEYWORDS)


def is_network_error(exc=None, resp=None, body=None) -> bool:
    """判断是否是网络错误"""
    status = None
    if resp is not None:
        status = getattr(resp, "status", None)
    if status is None and exc is not None:
        status = getattr(exc, "status", None)
    if status is None and exc is not None:
        status = getattr(exc, "code", None)
    if status is not None and int(status) >= 500:
        return True

    text_parts = []
    if body is not None:
        text_parts.append(str(body))
    if exc is not None:
        text_parts.append(str(exc))
    lowered = " ".join(text_parts).lower()
    return any(word in lowered for word in NETWORK_ERROR_KEYWORDS)


class SimpleAuthManager:
    """
    简化版认证管理器

    核心特点：
    - 简化状态机：HEALTHY, DEGRADED, LOCKED
    - 简化恢复链路：失败就重登
    - 冷却机制防止频繁重试
    - 主动探测确保播放可用
    """

    # 状态定义
    STATE_HEALTHY = "healthy"
    STATE_DEGRADED = "degraded"
    STATE_LOCKED = "locked"

    def __init__(self, config, log, device_manager, token_store=None):
        self.config = config
        self.log = log
        self.device_manager = device_manager
        self.token_store = token_store

        # 运行时对象
        self.mina_service = None
        self.miio_service = None
        self.login_account = None
        self.login_signature = None
        self.cookie_jar = None

        # 文件路径
        self.mi_token_home = os.path.join(self.config.conf_path, ".mi.token")
        self.auth_token_path = getattr(self.config, "auth_token_path", "")

        # 简化后的状态
        self._state = self.STATE_HEALTHY
        self._locked_until: float = 0
        self._last_error: str = ""
        self._last_ok_ts: float = 0
        self._last_login_ts: float = 0

        # 冷却机制
        self._cooldown_until: float = 0
        self._cooldown_sec: int = 60  # 默认冷却60秒

        # 重试计数
        self._retry_count: int = 0
        self._max_retries: int = 3

        # 设备ID
        self._cur_did = None
        self.device_id = self._get_random_device_id()
        self.mi_session = ClientSession()

        # 后台恢复任务
        self._recovery_task: asyncio.Task | None = None
        self._recovery_inflight: bool = False

        # 兼容性调试状态
        self._last_recovery_result: str = "skipped"
        self._last_recovery_stage: str = ""
        self._last_recovery_error_code: str = ""
        self._last_recovery_error_message: str = ""
        self._last_login_trace: dict[str, Any] = {}
        self._last_runtime_reload_state: dict[str, Any] = {}
        self._last_auto_runtime_reload_state: dict[str, Any] = {}
        self._last_short_session_rebuild_state: dict[str, Any] = {}

    def _get_random_device_id(self) -> str:
        """生成随机设备ID"""
        import random
        import string

        chars = string.ascii_uppercase + string.digits
        return "".join(random.choices(chars, k=16))

    def _has_persistent_auth_fields(self, auth_data: dict[str, Any]) -> bool:
        """判断是否具备可用于重登的长生命周期认证字段。"""
        return bool(
            auth_data.get("userId")
            and auth_data.get("passToken")
            and auth_data.get("psecurity")
            and auth_data.get("ssecurity")
            and auth_data.get("cUserId")
            and auth_data.get("deviceId")
        )

    # ==================== 公共接口 ====================

    @property
    def is_auth_error(self):
        """保持向后兼容的属性"""
        return is_auth_error

    async def init_all_data(self):
        """初始化所有数据，检查登录状态"""
        self.mi_token_home = os.path.join(self.config.conf_path, ".mi.token")

        # 检查是否可以登录
        if not await self.can_login():
            self.log.warning("没有认证 Token，无法登录")
            return

        # 检查是否需要登录
        if await self.need_login():
            self.log.info("需要登录，开始认证...")
            success = await self.ensure_logged_in(
                force=True, reason="init_all_data", prefer_refresh=True
            )
            if not success:
                self.log.warning("登录失败，将尝试在首次使用时恢复")
        else:
            self.log.info("认证状态有效，跳过登录")

        # 更新设备信息
        await self.device_manager.update_device_info(self)

        # 设置cookie
        cookie_jar = self.get_cookie()
        if cookie_jar:
            self.mi_session.cookie_jar.update_cookies(cookie_jar)
            self.cookie_jar = self.mi_session.cookie_jar

    async def can_login(self) -> bool:
        """检查是否可以登录"""
        if self._get_auth_data():
            return True
        return False

    async def need_login(self) -> bool:
        """检查是否需要登录"""
        if self.mina_service is None:
            return True
        if self.login_signature != self._get_login_signature():
            return True
        # 尝试快速验证
        try:
            await self.mina_service.device_list()
            return False
        except Exception as e:
            self.log.warning(f"验证失败，可能需要重新登录: {e}")
            return True

    def is_auth_locked(self) -> bool:
        """保持向后兼容的锁定判定"""
        return self._state == self.STATE_LOCKED and time.time() < self._locked_until

    async def ensure_logged_in(
        self,
        force: bool = False,
        reason: str = "ensure_logged_in",
        prefer_refresh: bool = True,
        recovery_owner: bool = False,
        **kwargs,
    ) -> bool:
        """兼容旧入口：统一委托给 ensure_auth。"""
        _ = prefer_refresh, recovery_owner, kwargs
        return await self.ensure_auth(force=force, reason=reason)

    def record_playback_capability_verify(self, *args, **kwargs):
        """兼容播放能力探测接口"""
        self._last_login_trace = {
            **self._last_login_trace,
            "playback_capability_verify": {
                "args_len": len(args),
                "kwargs_keys": sorted(list(kwargs.keys())),
                "ts": int(time.time() * 1000),
            },
        }

    async def ensure_auth(
        self, force: bool = False, reason: str = "ensure_auth"
    ) -> bool:
        """
        确保认证可用，如果不可用则尝试恢复

        这是核心入口，所有需要认证的操作都应该先调用此方法
        """
        # 如果在冷却期，检查是否过期
        if not force and time.time() < self._cooldown_until:
            return False

        if force:
            return await self._try_login(reason=reason or "ensure_auth")

        # 如果状态健康，快速返回
        if self._state == self.STATE_HEALTHY:
            if self.mina_service is None:
                self._state = self.STATE_DEGRADED
                self._last_error = "mina service unavailable"
                return await self._try_login(reason=reason or "ensure_auth")
            try:
                await self.mina_service.device_list()
                self._last_ok_ts = time.time()
                return True
            except Exception as e:
                self._last_error = str(e)[:200]
                if is_network_error(exc=e):
                    self._state = self.STATE_DEGRADED
                    self._start_cooldown()
                    return False
                self._state = self.STATE_DEGRADED
                self._last_recovery_stage = "probe"
                self._last_recovery_error_code = "auth_error"
                self._last_recovery_error_message = self._last_error
                return await self._try_login(reason=reason or "ensure_auth")

        # 状态不健康，尝试恢复
        if self._state in (self.STATE_DEGRADED, self.STATE_LOCKED):
            # 如果已锁定，检查是否过期
            if (
                self._state == self.STATE_LOCKED
                and time.time() < self._locked_until
                and not force
            ):
                return False

            success = await self._try_login(reason=reason or "ensure_auth")
            if success:
                self._state = self.STATE_HEALTHY
                return True
            else:
                # 恢复失败，先进入降级并根据重试次数决定是否锁定
                if self._retry_count >= self._max_retries:
                    self._state = self.STATE_LOCKED
                    self._locked_until = time.time() + 300  # 锁定5分钟
                else:
                    self._state = self.STATE_DEGRADED
                return False

        return False

    def auth_status_snapshot(self) -> dict[str, Any]:
        """获取认证状态快照"""
        return {
            "state": self._state,
            "locked": self._state == self.STATE_LOCKED,
            "locked_until_ts": int(self._locked_until * 1000)
            if self._locked_until > 0
            else 0,
            "lock_reason": self._last_error,
            "last_ok_ts": int(self._last_ok_ts * 1000) if self._last_ok_ts > 0 else 0,
            "cooldown_until_ts": int(self._cooldown_until * 1000)
            if self._cooldown_until > 0
            else 0,
        }

    def auth_debug_state(self) -> dict[str, Any]:
        """获取调试状态"""
        return {
            "last_auth_error": self._last_error,
            "state": self._state,
            "retry_count": self._retry_count,
        }

    # ==================== 核心恢复逻辑 ====================

    async def _try_login(self, reason: str = "") -> bool:
        """
        简化的登录逻辑

        核心思路：
        1. 读取持久化 token
        2. 创建 MiAccount 并登录
        3. 重建服务对象
        4. 验证
        """
        try:
            # Step 1: 读取认证数据
            auth_data = self._get_auth_data()
            if not auth_data:
                self._last_error = "no auth data"
                self._last_recovery_result = "failed"
                self._last_recovery_stage = "read_auth"
                self._last_recovery_error_code = "missing_auth_data"
                self._last_recovery_error_message = self._last_error
                return False

            user_id = auth_data.get("userId", "")
            if not user_id:
                self._last_error = "no userId"
                self._last_recovery_result = "failed"
                self._last_recovery_stage = "read_auth"
                self._last_recovery_error_code = "missing_auth_fields"
                self._last_recovery_error_message = self._last_error
                return False

            # Step 2: 创建 MiAccount
            try:
                mi_account = MiAccount()
            except TypeError:
                mi_account = MiAccount(
                    self.mi_session,
                    user_id,
                    "",  # 空密码，使用持久化 token
                    str(self.mi_token_home),
                )

            # Step 3: 设置持久化 token
            self.set_token(mi_account)
            self._last_login_trace = {
                "stage": "login_input_snapshot",
                "reason": reason,
                "has_passToken": bool(auth_data.get("passToken")),
                "has_psecurity": bool(auth_data.get("psecurity")),
                "has_ssecurity": bool(auth_data.get("ssecurity")),
                "has_userId": bool(auth_data.get("userId")),
                "has_cUserId": bool(auth_data.get("cUserId")),
                "has_deviceId": bool(auth_data.get("deviceId")),
                "has_serviceToken": bool(auth_data.get("serviceToken")),
                "has_yetAnotherServiceToken": bool(
                    auth_data.get("yetAnotherServiceToken")
                ),
                "ts": int(time.time() * 1000),
            }

            # Step 4: 登录获取新的 short session
            # 这是关键：用 login() 而非复用旧 token
            try:
                await mi_account.login("micoapi")
            except Exception as login_err:
                # 登录失败，尝试用旧 token 直接创建服务
                self.log.warning(f"login failed, trying direct: {login_err}")
                self._last_login_trace = {
                    **self._last_login_trace,
                    "stage": "login_http_exchange",
                    "result": "failed",
                    "error": str(login_err)[:200],
                }

            # Step 5: 重建服务对象
            try:
                self.mina_service = MiNAService(mi_account)
            except TypeError:
                self.mina_service = MiNAService()
            try:
                self.miio_service = MiIOService(mi_account)
            except TypeError:
                self.miio_service = MiIOService()
            self.login_account = mi_account

            # Step 6: 验证
            await self.mina_service.device_list()

            # 验证成功，更新签名
            self.login_signature = self._get_login_signature()
            self._last_ok_ts = time.time()
            self._last_login_ts = time.time()
            self._last_error = ""
            self._retry_count = 0
            self._state = self.STATE_HEALTHY
            self._last_recovery_result = "ok"
            self._last_recovery_stage = "verify"
            self._last_recovery_error_code = ""
            self._last_recovery_error_message = ""
            self._last_login_trace = {
                **self._last_login_trace,
                "stage": "post_login_runtime_seed",
                "result": "ok",
                "runtime_seed_has_serviceToken": bool(
                    mi_account.token.get("serviceToken")
                ),
                "runtime_seed_has_yetAnotherServiceToken": bool(
                    mi_account.token.get("yetAnotherServiceToken")
                ),
                "runtime_seed_has_ssecurity": bool(
                    mi_account.token.get("micoapi", (None, None))[0]
                ),
            }

            # 持久化新 token
            self._persist_auth_data(auth_data, mi_account, "login")

            self.log.info("认证成功")
            return True

        except Exception as e:
            self._last_error = str(e)[:200]
            self.log.error(f"认证失败: {e}")
            self._retry_count += 1
            if is_network_error(exc=e):
                self._last_recovery_result = "failed"
                self._last_recovery_stage = "network"
                self._last_recovery_error_code = "network_error"
                self._last_recovery_error_message = self._last_error
                self._state = self.STATE_DEGRADED
                self._start_cooldown()
            elif is_auth_error(exc=e):
                self._last_recovery_result = "failed"
                self._last_recovery_stage = "login"
                self._last_recovery_error_code = "auth_error"
                self._last_recovery_error_message = self._last_error
                self._state = self.STATE_DEGRADED
                if self._retry_count >= self._max_retries:
                    self._state = self.STATE_LOCKED
                    self._locked_until = time.time() + 300
                else:
                    self._start_cooldown()
            else:
                self._last_recovery_result = "failed"
                self._last_recovery_stage = "login"
                self._last_recovery_error_code = type(e).__name__
                self._last_recovery_error_message = self._last_error
                self._state = self.STATE_DEGRADED
                self._start_cooldown()
            return False

    def _start_cooldown(self):
        """开始冷却期"""
        self._cooldown_until = time.time() + self._cooldown_sec

    def _persist_auth_data(self, auth_data: dict, mi_account, reason: str = "") -> None:
        """持久化认证数据"""
        if self.token_store is None:
            return

        merged = dict(auth_data or {})
        try:
            acct = getattr(mi_account, "token", {}) or {}
            for key in ("passToken", "userId", "deviceId", "cUserId"):
                if acct.get(key):
                    merged[key] = acct.get(key)

            mico = acct.get("micoapi")
            if isinstance(mico, (tuple, list)) and len(mico) >= 2:
                if mico[0]:
                    merged["ssecurity"] = mico[0]
                if mico[1]:
                    merged["serviceToken"] = mico[1]
                    merged.setdefault("yetAnotherServiceToken", mico[1])

            if acct.get("serviceToken"):
                merged["serviceToken"] = acct.get("serviceToken")
            if acct.get("yetAnotherServiceToken"):
                merged["yetAnotherServiceToken"] = acct.get("yetAnotherServiceToken")

            merged["saveTime"] = int(time.time() * 1000)
        except Exception as e:
            self.log.warning(f"persist token merge failed: {e}")

        self.token_store.update(merged, reason=reason or "login")
        self.token_store.flush()

    def _get_auth_data(self) -> dict:
        """读取认证数据"""
        if self.token_store is not None:
            user_data = self.token_store.get()
        else:
            if not os.path.isfile(self.auth_token_path):
                return {}
            with open(self.auth_token_path, encoding="utf-8") as f:
                user_data = json.loads(f.read())

        required_fields = {"passToken", "userId"}
        if not required_fields.issubset(user_data):
            return {}
        return user_data

    def _get_login_signature(self) -> str:
        """获取登录签名，用于检测是否需要重新登录"""
        auth_data = self._get_auth_data()
        user_id = auth_data.get("userId", "")
        pass_token = (
            auth_data.get("passToken", "")[:8] if auth_data.get("passToken") else ""
        )
        return f"{user_id}:{pass_token}"

    # ==================== Token 操作 ====================

    def set_token(self, account):
        """设置 token 到 account"""
        user_data = self._get_auth_data()
        if user_data:
            self.device_id = user_data.get("deviceId", self.device_id)
            token_payload = {
                "passToken": user_data["passToken"],
                "userId": user_data["userId"],
                "deviceId": self.device_id,
            }
            for key in ("psecurity", "ssecurity", "cUserId"):
                if user_data.get(key):
                    token_payload[key] = user_data.get(key)
            if user_data.get("serviceToken"):
                token_payload["serviceToken"] = user_data.get("serviceToken")
            if user_data.get("yetAnotherServiceToken"):
                token_payload["yetAnotherServiceToken"] = user_data.get(
                    "yetAnotherServiceToken"
                )
            account.token = token_payload

    def get_cookie(self):
        """获取 Cookie"""
        auth_data = self._get_auth_data()
        service_token = auth_data.get("yetAnotherServiceToken") or auth_data.get(
            "serviceToken"
        )
        if service_token and auth_data.get("userId"):
            device_id = auth_data.get("deviceId") or self.config.get_one_device_id()
            c_user_id = auth_data.get("cUserId") or auth_data.get("userId")
            cookie_string = COOKIE_TEMPLATE.format(
                device_id=device_id,
                service_token=service_token,
                user_id=auth_data.get("userId"),
            )
            cookie_string += (
                f"; cUserId={c_user_id}; yetAnotherServiceToken={service_token}"
            )
            return parse_cookie_string(cookie_string)

        if not os.path.exists(self.mi_token_home):
            self.log.warning(f"{self.mi_token_home} file not exist")
            return None

        with open(self.mi_token_home, encoding="utf-8") as f:
            user_data = json.loads(f.read())
        user_id = user_data.get("userId")
        service_token = user_data.get("micoapi")[1]
        device_id = self.config.get_one_device_id()
        cookie_string = COOKIE_TEMPLATE.format(
            device_id=device_id, service_token=service_token, user_id=user_id
        )
        return parse_cookie_string(cookie_string)

    def get_cookie_dict(self, device_id=""):
        """获取 Cookie 字典"""
        auth_data = self._get_auth_data()
        service_token = auth_data.get("yetAnotherServiceToken") or auth_data.get(
            "serviceToken"
        )
        user_id = auth_data.get("userId", "")
        c_user_id = auth_data.get("cUserId") or user_id
        did = device_id or auth_data.get("deviceId") or self.config.get_one_device_id()

        if service_token and user_id:
            cookie_string = COOKIE_TEMPLATE.format(
                device_id=did,
                service_token=service_token,
                user_id=user_id,
            )
            cookie_string += (
                f"; cUserId={c_user_id}; yetAnotherServiceToken={service_token}"
            )
            return parse_cookie_string_to_dict(cookie_string)
        return {}

    # ==================== 设备操作 ====================

    async def try_update_device_id(self):
        """更新设备ID"""
        try:
            mi_dids = self.config.mi_did.split(",")
            hardware_data = await self.mina_service.device_list()
            devices = {}
            for h in hardware_data:
                device_id = h.get("deviceID", "")
                hardware = h.get("hardware", "")
                did = h.get("miotDID", "")
                name = h.get("alias", "") or h.get("name", "未知名字")
                if device_id and hardware and did and (did in mi_dids):
                    from xiaomusic.config import Device

                    device = self.config.devices.get(did, Device())
                    device.did = did
                    self._cur_did = did
                    device.device_id = device_id
                    device.hardware = hardware
                    device.name = name
                    devices[did] = device
            self.config.devices = devices
            self.log.info(f"选中的设备: {devices}")
            return devices
        except Exception as e:
            self.log.warning(f"更新设备ID失败: {e}")
            return {}

    # ==================== 带恢复的调用 ====================

    async def auth_call(
        self, fn: Callable[..., T], *args, retry: int = 1, ctx: str = "", **kwargs
    ) -> T:
        """
        带自动恢复的调用

        策略：
        1. 先尝试调用
        2. 如果失败，触发后台恢复
        3. 等待恢复完成后重试一次
        """
        last_err = None

        for attempt in range(retry + 1):
            try:
                # 确保认证可用
                if not await self.ensure_auth():
                    if attempt < retry:
                        # 触发后台恢复并等待
                        self._schedule_background_recovery()
                        await asyncio.sleep(2)
                        continue
                    raise RuntimeError("认证不可用")

                # 尝试调用
                result = await fn(*args, **kwargs)
                self._last_ok_ts = time.time()
                return result

            except Exception as e:
                last_err = e
                self._last_error = str(e)[:200]

                # 判断错误类型
                if is_network_error(exc=e):
                    # 网络错误，不触发恢复重试
                    if attempt < retry:
                        await asyncio.sleep(1)
                        continue
                elif is_auth_error(exc=e):
                    # 认证错误，触发恢复
                    self._state = self.STATE_DEGRADED
                    if attempt < retry:
                        self._schedule_background_recovery()
                        await asyncio.sleep(2)
                        continue

                if attempt < retry:
                    continue

        raise last_err

    def _schedule_background_recovery(self):
        """安排后台恢复任务"""
        if self._recovery_task is not None and not self._recovery_task.done():
            # 已经有恢复任务在运行
            return

        async def _do_recovery():
            try:
                success = await self._try_login()
                if success:
                    self._state = self.STATE_HEALTHY
                    self.log.info("后台恢复成功")
                else:
                    self._state = self.STATE_LOCKED
                    self._locked_until = time.time() + 300
                    self.log.warning("后台恢复失败，已进入锁定状态")
            except Exception as e:
                self.log.error(f"后台恢复异常: {e}")
                self._state = self.STATE_LOCKED
                self._locked_until = time.time() + 300
            finally:
                self._recovery_task = None

        self._recovery_task = asyncio.create_task(_do_recovery())

    async def mina_call(
        self, method_name: str, *args, retry: int = 1, ctx: str = "", **kwargs
    ):
        """调用 mina 服务的便捷方法"""

        async def _call():
            if self.mina_service is None:
                raise RuntimeError("mina service unavailable")
            method = getattr(self.mina_service, method_name)
            return await method(*args, **kwargs)

        return await self.auth_call(_call, retry=retry, ctx=f"mina:{method_name}:{ctx}")

    async def miio_call(self, fn, *, retry: int = 1, ctx: str = ""):
        """调用 miio 服务的便捷方法"""
        return await self.auth_call(fn, retry=retry, ctx=f"miio:{ctx}")

    # ==================== 主动探测 ====================

    async def keepalive_loop(self, interval_sec: int = 300):
        """
        Keepalive 循环

        每隔 interval_sec 秒检查一次认证状态
        如果发现问题，触发恢复
        """
        while True:
            try:
                await asyncio.sleep(interval_sec)

                # 检查认证状态
                if not await self.ensure_auth():
                    self.log.warning("Keepalive: 认证不健康，触发恢复")
                    # 不等待恢复完成，下一轮再检查
                else:
                    # 认证健康，尝试调用 device_list 保持连接
                    try:
                        await self.mina_service.device_list()
                        self._last_ok_ts = time.time()
                    except Exception as e:
                        self.log.warning(f"Keepalive probe failed: {e}")
                        self._state = self.STATE_DEGRADED

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.log.error(f"Keepalive loop error: {e}")

    # ==================== API 兼容接口 ====================

    async def manual_reload_runtime(
        self, reason: str = "manual_refresh_runtime", **kwargs
    ) -> dict[str, Any]:
        """
        手动重新加载运行时（兼容原有 API）

        用于 WebUI 的刷新按钮
        """
        success = await self.ensure_logged_in(
            force=True, reason=reason, prefer_refresh=True
        )
        runtime_reload_state = {
            "reason": reason,
            "result": "ok" if success else "failed",
            "error_code": ""
            if success
            else (self._last_recovery_error_code or "login_failed"),
            "error_message": self._last_error,
            "state_before": self._state,
            "state_after": self._state,
            "verify_auth_failure_detected": False,
            "recovery_chain_handoff": False,
            "recovery_chain_result": "skipped",
            "recovery_chain_terminal_stage": self._last_recovery_stage,
            "recovery_chain_terminal_error_code": self._last_recovery_error_code,
            "recovery_chain_terminal_error_message": self._last_recovery_error_message,
            "auto_runtime_reload_triggered": bool(
                kwargs.get("auto_runtime_reload_triggered", False)
            ),
            "auto_runtime_reload_source": kwargs.get("auto_runtime_reload_source", ""),
            "auto_runtime_reload_reason": kwargs.get("auto_runtime_reload_reason", ""),
            "auto_runtime_reload_skipped_reason": kwargs.get(
                "auto_runtime_reload_skipped_reason", ""
            ),
        }
        self._last_runtime_reload_state = {"last_reload_runtime": runtime_reload_state}
        if runtime_reload_state["auto_runtime_reload_triggered"]:
            self._last_auto_runtime_reload_state = {
                "last_auto_runtime_reload": runtime_reload_state
            }

        return {
            "refreshed": success,
            "runtime_auth_ready": success,
            "token_saved": success,
            "token_loaded": bool(self._get_auth_data()),
            "token_store_reloaded": self.token_store is not None,
            "runtime_rebound": success,
            "device_map_refreshed": success,
            "verify_result": "ok" if success else "failed",
            "last_error": self._last_error,
            "error_code": "" if success else "login_failed",
            "runtime_seed_incomplete": not success,
            "runtime_rebind_attempted": True,
            "verify_attempted": success,
            "mode_before": self._state,
            "mode_after": self._state,
            "recovery_chain_handoff": False,
            "recovery_chain_result": "skipped",
            "recovery_chain_terminal_stage": self._last_recovery_stage,
            "recovery_chain_terminal_error_code": self._last_recovery_error_code,
            "recovery_chain_terminal_error_message": self._last_recovery_error_message,
            "auto_runtime_reload_triggered": bool(
                kwargs.get("auto_runtime_reload_triggered", False)
            ),
            "auto_runtime_reload_source": kwargs.get("auto_runtime_reload_source", ""),
            "auto_runtime_reload_reason": kwargs.get("auto_runtime_reload_reason", ""),
            "auto_runtime_reload_skipped_reason": kwargs.get(
                "auto_runtime_reload_skipped_reason", ""
            ),
            "auto_runtime_reload_result": "ok" if success else "failed",
            "backoff_blocked": time.time() < self._cooldown_until,
            "cooldown_blocked": time.time() < self._cooldown_until,
            "singleflight_role": "leader"
            if self._recovery_task and not self._recovery_task.done()
            else "idle",
            "missing_long_lived_fields": [],
            "missing_short_session_fields": [],
            "timestamps": {
                "saveTime": int(self._last_login_ts * 1000)
                if self._last_login_ts > 0
                else None,
                "last_ok_ts": int(self._last_ok_ts * 1000)
                if self._last_ok_ts > 0
                else None,
                "last_refresh_ts": int(time.time() * 1000),
            },
        }

    def auth_recovery_debug_state(self) -> dict[str, Any]:
        """认证恢复调试状态"""
        return {
            "state": self._state,
            "last_error": self._last_error,
            "final_auth_mode": self._state,
            "recovery_task_running": self._recovery_task is not None
            and not self._recovery_task.done(),
            "backoff_blocked": time.time() < self._cooldown_until,
            "cooldown_blocked": time.time() < self._cooldown_until,
            "singleflight_role": "leader"
            if self._recovery_task is not None and not self._recovery_task.done()
            else "idle",
            "terminal_stage": self._last_recovery_stage,
            "terminal_error_code": self._last_recovery_error_code,
            "terminal_error_message": self._last_recovery_error_message,
            "retry_count": self._retry_count,
            "locked_until": int(self._locked_until * 1000)
            if self._locked_until > 0
            else 0,
            "cooldown_until": int(self._cooldown_until * 1000)
            if self._cooldown_until > 0
            else 0,
        }

    def miaccount_login_trace_debug_state(self) -> dict[str, Any]:
        """登录追踪调试状态"""
        return {
            "last_login_ts": int(self._last_login_ts * 1000)
            if self._last_login_ts > 0
            else 0,
            "last_login_trace": self._last_login_trace,
        }

    def auth_rebuild_debug_state(self) -> dict[str, Any]:
        """重建调试状态"""
        return self.auth_debug_state()

    def auth_short_session_rebuild_debug_state(self) -> dict[str, Any]:
        """短期会话重建调试状态"""
        return {
            "state": self._state,
            "cooldown_until": self._cooldown_until,
            "last_short_session_rebuild": self._last_short_session_rebuild_state,
            "last_auth_recovery_flow": self._last_short_session_rebuild_state,
        }

    def auth_runtime_reload_debug_state(self) -> dict[str, Any]:
        """运行时重载调试状态"""
        payload = {
            "last_reload_runtime": self._last_runtime_reload_state.get(
                "last_reload_runtime", {}
            ),
            "last_auto_runtime_reload": self._last_auto_runtime_reload_state.get(
                "last_auto_runtime_reload", {}
            ),
            "state": self._state,
            "last_error": self._last_error,
        }
        return payload

    def auth_rebuild_debug_state(self) -> dict[str, Any]:
        """重建调试状态"""
        return self.auth_debug_state()

    def auth_status_snapshot(self) -> dict[str, Any]:
        """获取认证状态快照"""
        auth_data = self._get_auth_data()
        return {
            "state": self._state,
            "auth_mode": self._state,
            "locked": self.is_auth_locked(),
            "locked_until_ts": int(self._locked_until * 1000)
            if self._locked_until > 0
            else 0,
            "lock_reason": self._last_error,
            "last_ok_ts": int(self._last_ok_ts * 1000) if self._last_ok_ts > 0 else 0,
            "cooldown_until_ts": int(self._cooldown_until * 1000)
            if self._cooldown_until > 0
            else 0,
            "persistent_auth_available": self._has_persistent_auth_fields(auth_data),
            "short_session_available": bool(
                auth_data.get("serviceToken") or auth_data.get("yetAnotherServiceToken")
            ),
            "retry_count": self._retry_count,
        }

    def auth_debug_state(self) -> dict[str, Any]:
        """获取调试状态"""
        auth_data = self._get_auth_data()
        return {
            "last_auth_error": self._last_error,
            "state": self._state,
            "auth_mode": self._state,
            "retry_count": self._retry_count,
            "last_ok_ts": int(self._last_ok_ts * 1000) if self._last_ok_ts > 0 else 0,
            "last_login_ts": int(self._last_login_ts * 1000)
            if self._last_login_ts > 0
            else 0,
            "locked_until_ts": int(self._locked_until * 1000)
            if self._locked_until > 0
            else 0,
            "cooldown_until_ts": int(self._cooldown_until * 1000)
            if self._cooldown_until > 0
            else 0,
            "persistent_auth_available": self._has_persistent_auth_fields(auth_data),
            "short_session_available": bool(
                auth_data.get("serviceToken") or auth_data.get("yetAnotherServiceToken")
            ),
        }

    def clear_auth_lock(self, reason: str = "", mode: str = "degraded"):
        """清除认证锁定"""
        if mode == "healthy":
            self._state = self.STATE_HEALTHY
        else:
            self._state = self.STATE_DEGRADED
        self._locked_until = 0
        self.log.info(f"认证锁定已清除: {reason}, 模式: {mode}")


AuthManager = SimpleAuthManager
