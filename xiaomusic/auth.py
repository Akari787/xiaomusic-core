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
from urllib.parse import parse_qs, urlsplit

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

AUTH_STRICT_ERROR_KEYWORDS = (
    "login failed",
    "unauthorized",
    "invalid token",
    "service token expired",
    "servicetoken invalid",
    "servicetoken expired",
    "token expired",
    "refresh token expired",
    "passport token expired",
)

LONG_TERM_AUTH_FAILURE_HINTS = (
    "refresh token expired",
    "passport token expired",
    "service token expired",
    "servicetoken expired",
    "need qr",
    "scan qr",
    "qr login",
    "account locked",
    "login required",
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


def is_auth_error_strict(exc=None, resp=None, body=None) -> bool:
    """更严格的认证错误判断。"""
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
    return any(word in lowered for word in AUTH_STRICT_ERROR_KEYWORDS)


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
        self._retry_count_effective: int = 0
        self._lock_counter: int = 0
        self._max_retries: int = 3
        self._lock_counter_threshold: int = self._max_retries
        self._probe_failure_count: int = 0
        self._recovery_failure_count: int = 0
        self._last_retry_count_before: int = 0
        self._last_retry_count_after: int = 0
        self._last_retry_count_effective: int = 0
        self._last_retry_increment_reason: str = ""
        self._last_lock_transition_reason: str = ""
        self._last_status_mapping_source: str = ""
        self._last_manual_login_required_reason: str = ""
        self._last_runtime_not_ready_reason: str = ""
        self._health_probe_attempted: bool = False
        self._keepalive_probe_attempted: bool = False
        self._background_recovery_attempted: bool = False
        self._background_recovery_result: str = ""
        self._background_recovery_error: str = ""
        self._last_health_probe_result: str = ""
        self._last_health_probe_error: str = ""
        self._last_keepalive_probe_result: str = ""
        self._last_keepalive_probe_error: str = ""
        self._last_degraded_entry_reason: str = ""

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
        self._last_auth_recovery_flow_state: dict[str, Any] = {}

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
        preserve_healthy_runtime: bool = False,
        **kwargs,
    ) -> bool:
        """兼容旧入口：统一委托给 ensure_auth。"""
        _ = prefer_refresh, recovery_owner, kwargs
        return await self.ensure_auth(
            force=force,
            reason=reason,
            preserve_healthy_runtime=preserve_healthy_runtime,
        )

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

    def _classify_auth_failure(
        self, err_text: str, auth_data: dict[str, Any]
    ) -> dict[str, Any]:
        """将失败分类为网络/认证/运行时错误。"""
        lowered = err_text.lower()
        if is_network_error(exc=RuntimeError(err_text)):
            return {
                "error_type": "network_error",
                "long_term_expired": False,
                "need_qr_scan": False,
                "user_action_required": False,
            }

        if not auth_data or not self._has_persistent_auth_fields(auth_data):
            return {
                "error_type": "missing_long_term_auth",
                "long_term_expired": True,
                "need_qr_scan": True,
                "user_action_required": True,
            }

        if is_auth_error_strict(exc=RuntimeError(err_text)):
            long_term_expired = any(
                hint in lowered for hint in LONG_TERM_AUTH_FAILURE_HINTS
            )
            return {
                "error_type": "auth_error",
                "long_term_expired": long_term_expired,
                "need_qr_scan": long_term_expired,
                "user_action_required": long_term_expired,
            }

        return {
            "error_type": "runtime_error",
            "long_term_expired": False,
            "need_qr_scan": False,
            "user_action_required": False,
        }

    async def ensure_auth(
        self,
        force: bool = False,
        reason: str = "ensure_auth",
        preserve_healthy_runtime: bool = False,
    ) -> bool:
        """
        确保认证可用，如果不可用则尝试恢复

        这是核心入口，所有需要认证的操作都应该先调用此方法
        """
        # 如果在冷却期，检查是否过期
        if not force and time.time() < self._cooldown_until:
            return False

        if force:
            return await self._try_login(
                reason=reason or "ensure_auth",
                preserve_healthy_runtime=preserve_healthy_runtime,
            )

        # 如果状态健康，快速返回
        if self._state == self.STATE_HEALTHY:
            if self.mina_service is None:
                self._state = self.STATE_DEGRADED
                self._last_error = "mina service unavailable"
                self._probe_failure_count += 1
                self._last_health_probe_result = "mina_service_missing"
                self._last_health_probe_error = self._last_error
                self._last_degraded_entry_reason = "mina_service_missing"
                return await self._try_login(
                    reason=reason or "ensure_auth",
                    preserve_healthy_runtime=preserve_healthy_runtime,
                )
            try:
                self._health_probe_attempted = True
                await self.mina_service.device_list()
                self._last_ok_ts = time.time()
                self._last_health_probe_result = "ok"
                self._last_health_probe_error = ""
                return True
            except Exception as e:
                self._last_error = str(e)[:200]
                self._probe_failure_count += 1
                self._last_health_probe_error = self._last_error
                if is_network_error(exc=e):
                    self._state = self.STATE_DEGRADED
                    self._last_health_probe_result = "network_error"
                    self._last_degraded_entry_reason = "health_probe_network_error"
                    self._start_cooldown()
                    return False
                self._state = self.STATE_DEGRADED
                self._last_health_probe_result = "auth_error"
                self._last_recovery_stage = "probe"
                self._last_recovery_error_code = "auth_error"
                self._last_recovery_error_message = self._last_error
                self._last_degraded_entry_reason = "health_probe_auth_error"
                return await self._try_login(
                    reason=reason or "ensure_auth",
                    preserve_healthy_runtime=preserve_healthy_runtime,
                )

        # 状态不健康，尝试恢复
        if self._state in (self.STATE_DEGRADED, self.STATE_LOCKED):
            # 如果已锁定，检查是否过期
            if (
                self._state == self.STATE_LOCKED
                and time.time() < self._locked_until
                and not force
            ):
                return False

            success = await self._try_login(
                reason=reason or "ensure_auth",
                preserve_healthy_runtime=preserve_healthy_runtime,
            )
            if success:
                self._state = self.STATE_HEALTHY
                return True
            else:
                # 恢复失败，仅在有效恢复失败累计到阈值时才锁定
                if self._lock_counter >= self._lock_counter_threshold:
                    self._state = self.STATE_LOCKED
                    self._locked_until = time.time() + 300  # 锁定5分钟
                    self._last_lock_transition_reason = (
                        self._last_lock_transition_reason
                        or f"ensure_auth:{self._last_recovery_stage}:{self._last_recovery_error_code}"
                    )
                else:
                    self._state = self.STATE_DEGRADED
                return False

        return False

    # ==================== 核心恢复逻辑 ====================

    async def _try_login(
        self, reason: str = "", preserve_healthy_runtime: bool = False
    ) -> bool:
        """
        简化的登录逻辑

        核心思路：
        1. 读取持久化 token
        2. 创建候选 runtime
        3. 先验证候选 runtime
        4. 验证成功后再原子替换当前 runtime
        """
        previous_state = self._state
        previous_locked_until = self._locked_until
        previous_cooldown_until = self._cooldown_until
        previous_retry_count = self._retry_count
        previous_retry_count_effective = self._retry_count_effective
        previous_lock_counter = self._lock_counter
        auth_data = self._get_auth_data()
        old_mi_session = self.mi_session
        runtime_swap_attempted = False
        runtime_swap_applied = False
        verify_attempted = False
        verify_method = "device_list"
        verify_error_text = ""
        login_error_text = ""
        login_result = False
        login_session = None
        token_changed_after_login = False
        candidate_runtime_account_ready = False
        candidate_runtime_cookie_ready = False
        pre_login_serviceToken_present = bool(auth_data.get("serviceToken"))
        pre_login_yetAnotherServiceToken_present = bool(
            auth_data.get("yetAnotherServiceToken")
        )
        pre_login_micoapi_present = bool(auth_data.get("micoapi"))
        failure_classification: dict[str, Any] = {
            "error_type": "runtime_error",
            "long_term_expired": False,
            "need_qr_scan": False,
            "user_action_required": False,
        }

        try:
            if not auth_data:
                self._last_error = "no auth data"
                self._last_recovery_result = "failed"
                self._last_recovery_stage = "read_auth"
                self._last_recovery_error_code = "missing_auth_data"
                self._last_recovery_error_message = self._last_error
                failure_classification = self._classify_auth_failure(
                    self._last_error, auth_data
                )
                return False

            user_id = auth_data.get("userId", "")
            if not user_id:
                self._last_error = "no userId"
                self._last_recovery_result = "failed"
                self._last_recovery_stage = "read_auth"
                self._last_recovery_error_code = "missing_auth_fields"
                self._last_recovery_error_message = self._last_error
                failure_classification = self._classify_auth_failure(
                    self._last_error, auth_data
                )
                return False

            if self._has_persistent_auth_fields(auth_data) and not (
                auth_data.get("serviceToken") or auth_data.get("yetAnotherServiceToken")
            ):
                rebuild_out = await self.rebuild_short_session_from_persistent_auth(
                    reason=reason or "ensure_auth"
                )
                if rebuild_out.get("ok"):
                    self._last_ok_ts = time.time()
                    self._last_login_ts = time.time()
                    self._last_error = ""
                    self._retry_count = 0
                    self._retry_count_effective = 0
                    self._lock_counter = 0
                    self._probe_failure_count = 0
                    self._recovery_failure_count = 0
                    self._state = self.STATE_HEALTHY
                    self._last_recovery_result = "ok"
                    self._last_recovery_stage = "verify"
                    self._last_recovery_error_code = ""
                    self._last_recovery_error_message = ""
                    self._last_lock_transition_reason = ""
                    self._last_retry_increment_reason = ""
                    self._last_login_trace = {
                        **self._last_login_trace,
                        "stage": "short_session_rebuild",
                        "result": "ok",
                        "reason": reason,
                        "used_path": rebuild_out.get("used_path", ""),
                        "login_result": False,
                        "runtime_swap_attempted": True,
                        "runtime_swap_applied": True,
                        "verify_attempted": True,
                        "verify_method": verify_method,
                        "verify_error_text": "",
                        "verify_auth_failure_detected": False,
                    }
                    self.log.info("短期会话重建成功")
                    return True
                self._last_error = str(
                    rebuild_out.get("failed_reason")
                    or rebuild_out.get("error_code")
                    or "short session rebuild failed"
                )[:200]
                self._last_recovery_result = "failed"
                self._last_recovery_stage = "short_session_rebuild"
                self._last_recovery_error_code = str(
                    rebuild_out.get("error_code") or "short_session_rebuild_failed"
                )
                self._last_recovery_error_message = self._last_error
                self._last_login_trace = {
                    **self._last_login_trace,
                    "stage": "short_session_rebuild",
                    "result": "failed",
                    "reason": reason,
                    "used_path": rebuild_out.get("used_path", ""),
                    "login_result": False,
                    "runtime_swap_attempted": bool(
                        rebuild_out.get("runtime_rebind_result") not in ("", "skipped")
                    ),
                    "runtime_swap_applied": False,
                    "verify_attempted": bool(
                        rebuild_out.get("verify_result") not in ("", "skipped")
                    ),
                    "verify_method": verify_method,
                    "verify_error_text": self._last_error,
                    "verify_auth_failure_detected": False,
                }
                raise RuntimeError(self._last_error)

            login_session = ClientSession()
            login_session_used = False
            try:
                new_account = MiAccount()
            except TypeError:
                login_session_used = True
                new_account = MiAccount(
                    login_session,
                    user_id,
                    "",
                    str(self.mi_token_home),
                )

            self.set_token(new_account)

            def _snapshot_runtime_token_state(account):
                token = dict(getattr(account, "token", {}) or {})
                micoapi_value = token.get("micoapi")
                has_micoapi = bool(
                    isinstance(micoapi_value, (tuple, list))
                    and len(micoapi_value) >= 2
                    and micoapi_value[0]
                    and micoapi_value[1]
                )
                cookie_ready = False
                try:
                    session = getattr(account, "session", None)
                    cookie_jar = getattr(session, "cookie_jar", None)
                    if cookie_jar is not None:
                        cookie_ready = bool(
                            cookie_jar.filter_cookies("https://api2.mina.mi.com")
                        )
                except Exception:
                    cookie_ready = False
                return {
                    "has_passToken": bool(token.get("passToken")),
                    "has_yetAnotherServiceToken": bool(token.get("yetAnotherServiceToken")),
                    "has_serviceToken": bool(token.get("serviceToken")),
                    "has_micoapi": has_micoapi,
                    "cookie_ready": cookie_ready,
                    "signature": (
                        bool(token.get("passToken")),
                        bool(token.get("userId")),
                        bool(token.get("deviceId")),
                        bool(token.get("psecurity")),
                        bool(token.get("ssecurity")),
                        bool(token.get("cUserId")),
                        bool(token.get("serviceToken")),
                        bool(token.get("yetAnotherServiceToken")),
                        has_micoapi,
                        cookie_ready,
                    ),
                }

            pre_login_snapshot = _snapshot_runtime_token_state(new_account)
            self._last_login_trace = {
                "stage": "login_input_snapshot",
                "reason": reason,
                "has_passToken": bool(auth_data.get("passToken")),
                "has_psecurity": bool(auth_data.get("psecurity")),
                "has_ssecurity": bool(auth_data.get("ssecurity")),
                "has_userId": bool(auth_data.get("userId")),
                "has_cUserId": bool(auth_data.get("cUserId")),
                "has_deviceId": bool(auth_data.get("deviceId")),
                "has_serviceToken": pre_login_serviceToken_present,
                "has_yetAnotherServiceToken": pre_login_yetAnotherServiceToken_present,
                "pre_login_serviceToken_present": pre_login_serviceToken_present,
                "pre_login_yetAnotherServiceToken_present": pre_login_yetAnotherServiceToken_present,
                "pre_login_micoapi_present": pre_login_micoapi_present,
                "post_login_serviceToken_present": False,
                "post_login_yetAnotherServiceToken_present": False,
                "post_login_micoapi_present": False,
                "token_changed_after_login": False,
                "candidate_runtime_account_ready": False,
                "candidate_runtime_cookie_ready": False,
                "login_result": False,
                "login_error": "",
                "verify_method": verify_method,
                "ts": int(time.time() * 1000),
                "runtime_swap_attempted": False,
                "runtime_swap_applied": False,
                "verify_attempted": False,
                "verify_error_text": "",
                "verify_auth_failure_detected": False,
                "login_error_text": "",
            }

            try:
                login_result = bool(await new_account.login("micoapi"))
            except Exception as login_err:
                login_error_text = str(login_err)[:200]
                self.log.warning(f"login failed, trying direct: {login_err}")
                self._last_login_trace = {
                    **self._last_login_trace,
                    "stage": "login_http_exchange",
                    "result": "failed",
                    "error": login_error_text,
                    "login_error": login_error_text,
                    "login_error_text": login_error_text,
                }
            else:
                if not login_result:
                    login_error_text = "login returned false"

            post_login_snapshot = _snapshot_runtime_token_state(new_account)
            token_changed_after_login = (
                pre_login_snapshot["signature"] != post_login_snapshot["signature"]
            )
            candidate_runtime_account_ready = bool(
                login_result
                and post_login_snapshot["has_micoapi"]
                and post_login_snapshot["has_serviceToken"]
            )
            candidate_runtime_cookie_ready = bool(
                login_result and post_login_snapshot["cookie_ready"]
            )
            self._last_login_trace = {
                **self._last_login_trace,
                "login_result": bool(login_result),
                "login_error": login_error_text,
                "post_login_serviceToken_present": post_login_snapshot["has_serviceToken"],
                "post_login_yetAnotherServiceToken_present": post_login_snapshot["has_yetAnotherServiceToken"],
                "post_login_micoapi_present": post_login_snapshot["has_micoapi"],
                "token_changed_after_login": token_changed_after_login,
                "candidate_runtime_account_ready": candidate_runtime_account_ready,
                "candidate_runtime_cookie_ready": candidate_runtime_cookie_ready,
            }

            if not candidate_runtime_account_ready:
                self._last_error = "Login failed"
                raise RuntimeError(login_error_text or self._last_error)

            try:
                new_mina_service = MiNAService(new_account)
            except TypeError:
                new_mina_service = MiNAService()
            try:
                new_miio_service = MiIOService(new_account)
            except TypeError:
                new_miio_service = MiIOService()

            runtime_swap_attempted = True
            self._last_login_trace = {
                **self._last_login_trace,
                "runtime_swap_attempted": True,
            }

            verify_attempted = True
            try:
                await new_mina_service.device_list()
            except Exception as verify_err:
                verify_error_text = str(verify_err)[:200]
                raise

            self.mina_service = new_mina_service
            self.miio_service = new_miio_service
            self.login_account = new_account
            if login_session_used:
                self.mi_session = login_session
                self.cookie_jar = self.mi_session.cookie_jar
                if old_mi_session is not login_session:
                    try:
                        await old_mi_session.close()
                    except Exception:
                        pass
            else:
                try:
                    await login_session.close()
                except Exception:
                    pass
            self.login_signature = self._get_login_signature()
            self._last_ok_ts = time.time()
            self._last_login_ts = time.time()
            self._last_error = ""
            self._retry_count = 0
            self._retry_count_effective = 0
            self._lock_counter = 0
            self._probe_failure_count = 0
            self._recovery_failure_count = 0
            self._state = self.STATE_HEALTHY
            self._last_recovery_result = "ok"
            self._last_recovery_stage = "verify"
            self._last_recovery_error_code = ""
            self._last_recovery_error_message = ""
            self._last_lock_transition_reason = ""
            self._last_retry_increment_reason = ""
            self._last_login_trace = {
                **self._last_login_trace,
                "stage": "post_login_runtime_seed",
                "result": "ok",
                "login_result": bool(login_result),
                "login_error": login_error_text,
                "post_login_serviceToken_present": bool(
                    new_account.token.get("serviceToken")
                ),
                "post_login_yetAnotherServiceToken_present": bool(
                    new_account.token.get("yetAnotherServiceToken")
                ),
                "post_login_micoapi_present": bool(
                    isinstance(new_account.token.get("micoapi"), (tuple, list))
                    and len(new_account.token.get("micoapi", (None, None))) >= 2
                    and bool(new_account.token.get("micoapi", (None, None))[0])
                    and bool(new_account.token.get("micoapi", (None, None))[1])
                ),
                "token_changed_after_login": token_changed_after_login,
                "candidate_runtime_account_ready": candidate_runtime_account_ready,
                "candidate_runtime_cookie_ready": candidate_runtime_cookie_ready,
                "runtime_seed_has_serviceToken": bool(
                    new_account.token.get("serviceToken")
                ),
                "runtime_seed_has_yetAnotherServiceToken": bool(
                    new_account.token.get("yetAnotherServiceToken")
                ),
                "runtime_seed_has_ssecurity": bool(
                    new_account.token.get("micoapi", (None, None))[0]
                ),
                "runtime_swap_attempted": runtime_swap_attempted,
                "runtime_swap_applied": True,
                "verify_method": verify_method,
                "verify_attempted": verify_attempted,
                "verify_error_text": "",
                "verify_auth_failure_detected": False,
                "login_error_text": login_error_text,
            }

            self._persist_auth_data(auth_data, new_account, "login")
            self.log.info("认证成功")
            return True

        except Exception as e:
            self._last_error = str(e)[:200]
            self.log.error(f"认证失败: {e}")
            failure_classification = self._classify_auth_failure(
                self._last_error, auth_data
            )
            self._last_recovery_result = "failed"
            self._last_recovery_stage = "verify" if verify_attempted else "login"
            self._last_recovery_error_code = failure_classification["error_type"]
            self._last_recovery_error_message = self._last_error
            self._last_login_trace = {
                **self._last_login_trace,
                "stage": self._last_recovery_stage,
                "result": "failed",
                "runtime_swap_attempted": runtime_swap_attempted,
                "runtime_swap_applied": False,
                "verify_method": verify_method,
                "verify_attempted": verify_attempted,
                "verify_error_text": verify_error_text or self._last_error,
                "verify_auth_failure_detected": bool(
                    verify_attempted and not runtime_swap_applied
                ),
                "login_error_text": login_error_text,
                **failure_classification,
            }

            if login_session is not None:
                try:
                    await login_session.close()
                except Exception:
                    pass

            self._recovery_failure_count += 1
            counted, increment_reason = self._should_count_lock_failure(
                failure_classification, self._last_recovery_stage
            )
            self._apply_retry_result(
                counted=counted,
                reason=f"{self._last_recovery_stage}:{increment_reason}",
                failure_classification=failure_classification,
            )

            if preserve_healthy_runtime and previous_state == self.STATE_HEALTHY:
                self._state = previous_state
                self._locked_until = previous_locked_until
                self._cooldown_until = previous_cooldown_until
                self._retry_count = previous_retry_count
                self._retry_count_effective = previous_retry_count_effective
                self._lock_counter = previous_lock_counter
                return False

            self._state = self.STATE_DEGRADED
            if counted and self._lock_counter >= self._lock_counter_threshold:
                self._state = self.STATE_LOCKED
                self._locked_until = time.time() + 300
                self._last_lock_transition_reason = (
                    f"{self._last_recovery_stage}:{increment_reason}:threshold_reached"
                )
            else:
                self._start_cooldown()
            return False

    def _start_cooldown(self):
        """开始冷却期"""
        self._cooldown_until = time.time() + self._cooldown_sec

    def _should_count_lock_failure(
        self, failure_classification: dict[str, Any], stage: str = ""
    ) -> tuple[bool, str]:
        """判定此次失败是否应该推进 lock 计数。"""
        error_type = str(failure_classification.get("error_type", "") or "")
        long_term_expired = bool(failure_classification.get("long_term_expired"))
        need_qr_scan = bool(failure_classification.get("need_qr_scan"))
        user_action_required = bool(failure_classification.get("user_action_required"))

        # 探测失败、网络错误、普通 runtime 错误都不直接推进 lock。
        if error_type in ("network_error", "runtime_error"):
            return False, error_type or "runtime_error"

        # 只有明确的人为介入/长周期认证失效才推进 lock。
        if error_type == "missing_long_term_auth":
            return True, error_type
        if error_type == "auth_error" and (
            long_term_expired or need_qr_scan or user_action_required
        ):
            return True, error_type

        # 其它失败先只降级，不推进锁。
        _ = stage
        return False, error_type or "unknown_failure"

    def _apply_retry_result(
        self,
        *,
        counted: bool,
        reason: str,
        failure_classification: dict[str, Any],
    ) -> None:
        """记录 retry / lock 的最小调试状态。"""
        self._last_retry_count_before = self._retry_count
        self._retry_count += 1
        self._last_retry_count_after = self._retry_count
        if counted:
            self._retry_count_effective += 1
            self._lock_counter = self._retry_count_effective
        self._last_retry_count_effective = self._retry_count_effective
        self._last_retry_increment_reason = reason
        self._last_status_mapping_source = str(
            failure_classification.get("error_type", "") or reason or "unknown"
        )

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

    def _record_short_session_rebuild_state(self, payload: dict[str, Any]) -> None:
        state = dict(payload or {})
        state["ts"] = int(time.time() * 1000)
        self._last_short_session_rebuild_state = state

    def _record_auth_recovery_flow_state(self, payload: dict[str, Any]) -> None:
        state = dict(payload or {})
        self._last_auth_recovery_flow_state = state

    async def _try_miaccount_persistent_auth_relogin(
        self, before: dict[str, Any] | None = None, reason: str = "", sid: str = "micoapi"
    ) -> dict[str, Any]:
        auth_data = dict(before or {})
        if not self._has_persistent_auth_fields(auth_data):
            return {
                "ok": False,
                "used_path": "miaccount_persistent_auth_login",
                "error_code": "missing_persistent_auth_fields",
                "failed_reason": "missing_persistent_auth_fields",
                "error_message": "missing_persistent_auth_fields",
                "http_stage": "serviceLogin",
                "writeback_target": "none",
                "sid": sid,
                "diagnostic": {
                    "reason": reason,
                    "via": "miaccount_persistent_auth_login",
                    "response_valid": False,
                },
            }

        login_session = ClientSession()
        login_session_used = False
        try:
            try:
                account = MiAccount()
            except TypeError:
                login_session_used = True
                account = MiAccount(
                    login_session,
                    auth_data.get("userId", ""),
                    "",
                    str(self.mi_token_home),
                )
            self.set_token(account)
            resp = await account._serviceLogin(f"serviceLogin?sid={sid}&_json=true")
            if not isinstance(resp, dict):
                return {
                    "ok": False,
                    "used_path": "miaccount_persistent_auth_login",
                    "error_code": "invalid_service_login_response",
                    "failed_reason": "invalid_service_login_response",
                    "error_message": "invalid_service_login_response",
                    "http_stage": "serviceLogin",
                    "writeback_target": "none",
                    "sid": sid,
                    "diagnostic": {
                        "reason": reason,
                        "via": "miaccount_persistent_auth_login",
                        "response_valid": False,
                    },
                }

            location = str(resp.get("location", "") or "")
            nonce = resp.get("nonce")
            if not nonce and location:
                query = parse_qs(urlsplit(location).query)
                nonce = (query.get("nonce") or [""])[0]
            ssecurity = str(resp.get("ssecurity", "") or auth_data.get("ssecurity", "") or "")
            diagnostic = {
                "reason": reason,
                "via": "miaccount_persistent_auth_login",
                "service_login_code": resp.get("code"),
                "has_location": bool(location),
                "has_nonce": bool(nonce),
                "has_ssecurity": bool(ssecurity),
                "blocked_before_security_token_service": False,
                "security_token_service_invoked": False,
            }
            if int(resp.get("code", -1)) != 0:
                return {
                    "ok": False,
                    "used_path": "miaccount_persistent_auth_login",
                    "error_code": "service_login_failed",
                    "failed_reason": f"service_login_code_{resp.get('code')}",
                    "error_message": str(resp),
                    "http_stage": "serviceLogin",
                    "writeback_target": "none",
                    "sid": sid,
                    "diagnostic": diagnostic,
                }
            if not location:
                return {
                    "ok": False,
                    "used_path": "miaccount_persistent_auth_login",
                    "error_code": "redirect_missing_location",
                    "failed_reason": "service_login_response_missing_location",
                    "error_message": "serviceLogin response missing location",
                    "http_stage": "redirect",
                    "writeback_target": "none",
                    "sid": sid,
                    "diagnostic": diagnostic,
                }
            if not nonce:
                diagnostic["blocked_before_security_token_service"] = True
                return {
                    "ok": False,
                    "used_path": "miaccount_persistent_auth_login",
                    "error_code": "redirect_missing_nonce",
                    "failed_reason": "service_login_response_missing_nonce",
                    "error_message": "serviceLogin response missing nonce; skip _securityTokenService",
                    "http_stage": "redirect",
                    "writeback_target": "none",
                    "sid": sid,
                    "diagnostic": diagnostic,
                }
            if not ssecurity:
                diagnostic["blocked_before_security_token_service"] = True
                return {
                    "ok": False,
                    "used_path": "miaccount_persistent_auth_login",
                    "error_code": "redirect_missing_ssecurity",
                    "failed_reason": "service_login_response_missing_ssecurity",
                    "error_message": "serviceLogin response missing ssecurity",
                    "http_stage": "redirect",
                    "writeback_target": "none",
                    "sid": sid,
                    "diagnostic": diagnostic,
                }

            diagnostic["security_token_service_invoked"] = True
            try:
                service_token = await account._securityTokenService(location, nonce, ssecurity)
            except Exception as exc:
                return {
                    "ok": False,
                    "used_path": "miaccount_persistent_auth_login",
                    "error_code": "security_token_service_failed",
                    "failed_reason": str(exc)[:200],
                    "error_message": str(exc)[:200],
                    "http_stage": "redirect",
                    "writeback_target": "none",
                    "sid": sid,
                    "diagnostic": diagnostic,
                }
            if not service_token:
                return {
                    "ok": False,
                    "used_path": "miaccount_persistent_auth_login",
                    "error_code": "empty_service_token",
                    "failed_reason": "empty_service_token",
                    "error_message": "empty_service_token",
                    "http_stage": "redirect",
                    "writeback_target": "none",
                    "sid": sid,
                    "diagnostic": diagnostic,
                }

            if not getattr(account, "token", None):
                account.token = {}
            account.token["serviceToken"] = service_token
            account.token["yetAnotherServiceToken"] = service_token
            account.token[sid] = (ssecurity, service_token)
            merged = dict(auth_data)
            merged["ssecurity"] = ssecurity
            merged["serviceToken"] = service_token
            merged["yetAnotherServiceToken"] = service_token
            merged["saveTime"] = int(time.time() * 1000)
            if self.token_store is not None:
                self.token_store.update(merged, reason=reason or "persistent_auth_relogin")
                self.token_store.flush()
                writeback_target = "token_store"
            else:
                writeback_target = "none"
            return {
                "ok": True,
                "used_path": "miaccount_persistent_auth_login",
                "serviceToken": service_token,
                "yetAnotherServiceToken": service_token,
                "ssecurity": ssecurity,
                "sid": sid,
                "http_stage": "redirect",
                "writeback_target": writeback_target,
                "diagnostic": diagnostic,
            }
        finally:
            if not login_session_used:
                try:
                    await login_session.close()
                except Exception:
                    pass

    async def _try_mijia_persistent_auth_relogin(
        self, auth_dir: str | None = None, sid: str = "micoapi"
    ) -> dict[str, Any]:
        auth_data = self._get_auth_data()
        if not self._has_persistent_auth_fields(auth_data):
            return {
                "ok": False,
                "used_path": "mijia_persistent_auth_login",
                "error_code": "missing_persistent_auth_fields",
                "failed_reason": "missing_persistent_auth_fields",
                "error_message": "missing_persistent_auth_fields",
                "http_stage": "serviceLogin",
                "writeback_target": "none",
                "sid": sid,
                "diagnostic": {
                    "via": "mijia_persistent_auth_login",
                    "response_valid": False,
                },
            }
        try:
            from xiaomusic.qrcode_login import MiJiaAPI

            api = MiJiaAPI(
                auth_data_path=auth_dir or os.path.dirname(self.auth_token_path),
                token_store=self.token_store,
            )
            out = await asyncio.to_thread(
                api.rebuild_service_cookies_from_persistent_auth, sid
            )
        except Exception as exc:
            return {
                "ok": False,
                "used_path": "mijia_persistent_auth_login",
                "error_code": "mijia_persistent_auth_login_failed",
                "failed_reason": str(exc)[:200],
                "error_message": str(exc)[:200],
                "http_stage": "serviceLogin",
                "writeback_target": "none",
                "sid": sid,
                "diagnostic": {
                    "via": "mijia_persistent_auth_login",
                    "response_valid": False,
                },
            }
        if not isinstance(out, dict):
            return {
                "ok": False,
                "used_path": "mijia_persistent_auth_login",
                "error_code": "invalid_mijia_relogin_response",
                "failed_reason": "invalid_mijia_relogin_response",
                "error_message": "invalid_mijia_relogin_response",
                "http_stage": "serviceLogin",
                "writeback_target": "none",
                "sid": sid,
                "diagnostic": {
                    "via": "mijia_persistent_auth_login",
                    "response_valid": False,
                },
            }
        latest = self._get_auth_data()
        normalized = dict(out)
        normalized.setdefault("used_path", "mijia_persistent_auth_login")
        normalized.setdefault("sid", sid)
        normalized.setdefault("diagnostic", {"via": "mijia_persistent_auth_login", "response_valid": True})
        if normalized.get("ok"):
            if latest.get("serviceToken"):
                normalized["serviceToken"] = latest.get("serviceToken")
            if latest.get("yetAnotherServiceToken"):
                normalized["yetAnotherServiceToken"] = latest.get("yetAnotherServiceToken")
            if latest.get("ssecurity"):
                normalized["ssecurity"] = latest.get("ssecurity")
        return normalized

    async def _rebind_runtime_from_auth_data(self, auth_data: dict[str, Any]) -> dict[str, Any]:
        try:
            login_session = ClientSession()
            login_session_used = False
            try:
                new_account = MiAccount()
            except TypeError:
                login_session_used = True
                new_account = MiAccount(
                    login_session,
                    auth_data.get("userId", ""),
                    "",
                    str(self.mi_token_home),
                )
            self.set_token(new_account)
            try:
                new_mina_service = MiNAService(new_account)
            except TypeError:
                new_mina_service = MiNAService()
            try:
                new_miio_service = MiIOService(new_account)
            except TypeError:
                new_miio_service = MiIOService()
            if login_session_used:
                old_session = self.mi_session
                self.mi_session = login_session
                self.cookie_jar = self.mi_session.cookie_jar
                if old_session is not login_session:
                    try:
                        await old_session.close()
                    except Exception:
                        pass
            else:
                try:
                    await login_session.close()
                except Exception:
                    pass
            self.login_account = new_account
            self.mina_service = new_mina_service
            self.miio_service = new_miio_service
            self.login_signature = self._get_login_signature()
            return {"ok": True, "result": "ok"}
        except Exception as exc:
            return {
                "ok": False,
                "result": "failed",
                "error_code": "runtime_rebind_failed",
                "failed_reason": str(exc)[:200],
            }

    async def rebuild_short_session_from_persistent_auth(self, reason: str = "") -> dict[str, Any]:
        auth_data = self._get_auth_data()
        started_at = int(time.time() * 1000)
        flow: dict[str, Any] = {
            "reason": reason,
            "started_at": started_at,
            "primary_attempt": {"result": "skipped"},
            "fallback_attempt": {"result": "skipped"},
            "rebind": {"result": "skipped"},
            "verify": {"result": "skipped"},
            "result": "running",
            "used_path": "",
            "finished_at": 0,
        }
        if not self._has_persistent_auth_fields(auth_data):
            out = {
                "ok": False,
                "result": "failed",
                "used_path": "miaccount_persistent_auth_login",
                "error_code": "missing_persistent_auth_fields",
                "failed_reason": "missing_persistent_auth_fields",
                "service_token_written": False,
                "runtime_rebind_result": "skipped",
                "verify_result": "skipped",
            }
            flow["primary_attempt"] = {
                "attempt_at": started_at,
                "used_path": "miaccount_persistent_auth_login",
                "error_code": "missing_persistent_auth_fields",
                "result": "failed",
            }
            flow["result"] = "failed"
            flow["used_path"] = "miaccount_persistent_auth_login"
            flow["finished_at"] = int(time.time() * 1000)
            self._record_short_session_rebuild_state(out)
            self._record_auth_recovery_flow_state(flow)
            return out

        primary = await self._try_miaccount_persistent_auth_relogin(
            before=auth_data,
            reason=reason,
            sid="micoapi",
        )
        flow["primary_attempt"] = {
            "attempt_at": int(time.time() * 1000),
            "used_path": str(primary.get("used_path", "") or "miaccount_persistent_auth_login"),
            "error_code": str(primary.get("error_code", "") or ""),
            "result": "ok" if primary.get("ok") else "failed",
        }

        relogin = primary
        if not bool(primary.get("ok", False)):
            fallback = await self._try_mijia_persistent_auth_relogin(
                auth_dir=os.path.dirname(self.auth_token_path),
                sid="micoapi",
            )
            flow["fallback_attempt"] = {
                "attempt_at": int(time.time() * 1000),
                "used_path": str(fallback.get("used_path", "") or "mijia_persistent_auth_login"),
                "error_code": str(fallback.get("error_code", "") or ""),
                "result": "ok" if fallback.get("ok") else "failed",
            }
            if bool(fallback.get("ok", False)):
                relogin = fallback
            else:
                relogin = fallback if fallback else primary

        used_path = str(relogin.get("used_path", "") or "miaccount_persistent_auth_login")
        flow["used_path"] = used_path
        if not bool(relogin.get("ok", False)):
            out = {
                "ok": False,
                "result": "failed",
                "used_path": used_path,
                "error_code": str(relogin.get("error_code", "persistent_auth_relogin_failed") or "persistent_auth_relogin_failed"),
                "failed_reason": str(relogin.get("failed_reason", "persistent_auth_relogin_failed") or "persistent_auth_relogin_failed"),
                "service_token_written": False,
                "runtime_rebind_result": "skipped",
                "verify_result": "skipped",
            }
            flow["result"] = "failed"
            flow["finished_at"] = int(time.time() * 1000)
            self._record_short_session_rebuild_state(out)
            self._record_auth_recovery_flow_state(flow)
            return out

        latest_auth_data = self._get_auth_data()
        merged = dict(auth_data)
        merged.update(latest_auth_data)
        for key in ("serviceToken", "yetAnotherServiceToken", "ssecurity", "cUserId", "deviceId", "userId", "passToken", "psecurity"):
            if relogin.get(key):
                merged[key] = relogin.get(key)
        service_token_written = bool(
            merged.get("serviceToken") or merged.get("yetAnotherServiceToken")
        )
        if self.token_store is not None and service_token_written:
            merged["saveTime"] = int(time.time() * 1000)
            self.token_store.update(merged, reason=reason or "short_session_rebuild")
            self.token_store.flush()

        if not service_token_written:
            out = {
                "ok": False,
                "result": "failed",
                "used_path": used_path,
                "error_code": "service_token_not_written",
                "failed_reason": "service_token_not_written",
                "service_token_written": False,
                "runtime_rebind_result": "skipped",
                "verify_result": "skipped",
            }
            flow["result"] = "failed"
            flow["finished_at"] = int(time.time() * 1000)
            self._record_short_session_rebuild_state(out)
            self._record_auth_recovery_flow_state(flow)
            return out

        rebind = await self._rebind_runtime_from_auth_data(merged)
        flow["rebind"] = {
            "attempt_at": int(time.time() * 1000),
            "used_path": used_path,
            "error_code": str(rebind.get("error_code", "") or ""),
            "result": "ok" if rebind.get("ok") else "failed",
        }
        if not bool(rebind.get("ok", False)):
            out = {
                "ok": False,
                "result": "failed",
                "used_path": used_path,
                "error_code": str(rebind.get("error_code", "runtime_rebind_failed") or "runtime_rebind_failed"),
                "failed_reason": str(rebind.get("failed_reason", "runtime_rebind_failed") or "runtime_rebind_failed"),
                "service_token_written": True,
                "runtime_rebind_result": "failed",
                "verify_result": "skipped",
            }
            flow["result"] = "failed"
            flow["finished_at"] = int(time.time() * 1000)
            self._record_short_session_rebuild_state(out)
            self._record_auth_recovery_flow_state(flow)
            return out

        try:
            if self.mina_service is None:
                raise RuntimeError("mina service unavailable")
            await self.mina_service.device_list()
            self._last_ok_ts = time.time()
            flow["verify"] = {
                "attempt_at": int(time.time() * 1000),
                "used_path": used_path,
                "error_code": "",
                "result": "ok",
            }
            out = {
                "ok": True,
                "result": "ok",
                "used_path": used_path,
                "error_code": "",
                "failed_reason": "",
                "service_token_written": True,
                "runtime_rebind_result": "ok",
                "verify_result": "ok",
            }
            flow["result"] = "ok"
            flow["finished_at"] = int(time.time() * 1000)
            self._record_short_session_rebuild_state(out)
            self._record_auth_recovery_flow_state(flow)
            return out
        except Exception as exc:
            flow["verify"] = {
                "attempt_at": int(time.time() * 1000),
                "used_path": used_path,
                "error_code": "verify_failed",
                "result": "failed",
            }
            out = {
                "ok": False,
                "result": "failed",
                "used_path": used_path,
                "error_code": "verify_failed",
                "failed_reason": str(exc)[:200],
                "service_token_written": True,
                "runtime_rebind_result": "ok",
                "verify_result": "failed",
            }
            flow["result"] = "failed"
            flow["finished_at"] = int(time.time() * 1000)
            self._record_short_session_rebuild_state(out)
            self._record_auth_recovery_flow_state(flow)
            return out

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
                    last_recovery_code = self._last_recovery_error_code
                    if last_recovery_code == "network_error" or is_network_error(
                        exc=RuntimeError(self._last_error)
                    ):
                        if attempt < retry:
                            await asyncio.sleep(1)
                            continue
                        raise RuntimeError("认证不可用")
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
            self._background_recovery_attempted = True
            try:
                success = await self._try_login()
                if success:
                    self._background_recovery_result = "ok"
                    self._background_recovery_error = ""
                    self._state = self.STATE_HEALTHY
                    self.log.info("后台恢复成功")
                else:
                    self._background_recovery_result = "failed"
                    self._background_recovery_error = self._last_error
                    if self._lock_counter >= self._lock_counter_threshold:
                        self._state = self.STATE_LOCKED
                        self._locked_until = time.time() + 300
                        self._last_lock_transition_reason = (
                            self._last_lock_transition_reason
                            or f"background_recovery:{self._last_recovery_stage}:{self._last_recovery_error_code}"
                        )
                        self.log.warning("后台恢复失败，已进入锁定状态")
                    else:
                        self._state = self.STATE_DEGRADED
                        self._start_cooldown()
                        self.log.warning("后台恢复失败，保持降级并进入冷却")
            except Exception as e:
                self._background_recovery_result = "exception"
                self._background_recovery_error = str(e)[:200]
                self.log.error(f"后台恢复异常: {e}")
                self._recovery_failure_count += 1
                self._last_status_mapping_source = "background_recovery_exception"
                if self._lock_counter >= self._lock_counter_threshold:
                    self._state = self.STATE_LOCKED
                    self._locked_until = time.time() + 300
                    self._last_lock_transition_reason = (
                        self._last_lock_transition_reason
                        or f"background_recovery_exception:{type(e).__name__}"
                    )
                else:
                    self._state = self.STATE_DEGRADED
                    self._start_cooldown()
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
                    self._last_keepalive_probe_result = "ensure_auth_failed"
                    self._last_keepalive_probe_error = str(self._last_error or "")[:200]
                    self.log.warning("Keepalive: 认证不健康，触发恢复")
                    # 不等待恢复完成，下一轮再检查
                else:
                    # 认证健康，尝试调用 device_list 保持连接
                    try:
                        self._keepalive_probe_attempted = True
                        self._last_keepalive_probe_result = "ok"
                        self._last_keepalive_probe_error = ""
                        await self.mina_service.device_list()
                        self._last_ok_ts = time.time()
                    except Exception as e:
                        self._last_keepalive_probe_result = "probe_failed"
                        self._last_keepalive_probe_error = str(e)[:200]
                        self._probe_failure_count += 1
                        self._last_degraded_entry_reason = "keepalive_probe_failed"
                        self.log.warning(f"Keepalive probe failed: {e}")
                        self._state = self.STATE_DEGRADED
                        self._start_cooldown()

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
        state_before = self._state
        mode_before = self._state
        locked_before = self._locked_until
        cooldown_before = self._cooldown_until
        preserve_healthy_runtime = state_before == self.STATE_HEALTHY
        success = await self.ensure_logged_in(
            force=True,
            reason=reason,
            prefer_refresh=True,
            preserve_healthy_runtime=preserve_healthy_runtime,
        )
        trace = dict(self._last_login_trace or {})
        failure_info = self._classify_auth_failure(
            self._last_error, self._get_auth_data()
        )
        if (
            not success
            and preserve_healthy_runtime
            and not failure_info["long_term_expired"]
        ):
            self._state = state_before
            self._locked_until = locked_before
            self._cooldown_until = cooldown_before
        runtime_auth_ready = bool(
            success
            or (preserve_healthy_runtime and not failure_info["long_term_expired"])
        )
        runtime_reload_state = {
            "reason": reason,
            "result": "ok" if success else "failed",
            "error_code": ""
            if success
            else (
                self._last_recovery_error_code
                or failure_info["error_type"]
                or "login_failed"
            ),
            "error_type": ""
            if success
            else (failure_info["error_type"] or "runtime_error"),
            "error_message": self._last_error,
            "state_before": state_before,
            "state_after": self._state,
            "mode_before": mode_before,
            "mode_after": self._state,
            "runtime_swap_attempted": bool(trace.get("runtime_swap_attempted", False)),
            "runtime_swap_applied": bool(trace.get("runtime_swap_applied", False)),
            "verify_attempted": bool(trace.get("verify_attempted", False)),
            "verify_error_text": str(trace.get("verify_error_text", "") or ""),
            "need_qr_scan": bool(failure_info["need_qr_scan"]),
            "user_action_required": bool(failure_info["user_action_required"]),
            "long_term_expired": bool(failure_info["long_term_expired"]),
            "verify_auth_failure_detected": bool(
                trace.get("verify_attempted", False) and not success
            ),
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
            "runtime_auth_ready": runtime_auth_ready,
            "token_saved": success,
            "token_loaded": bool(self._get_auth_data()),
            "token_store_reloaded": self.token_store is not None,
            "runtime_rebound": success,
            "device_map_refreshed": success,
            "verify_result": "ok"
            if success
            else ("failed" if trace.get("verify_attempted") else "skipped"),
            "last_error": self._last_error,
            "state_before": state_before,
            "state_after": self._state,
            "mode_before": mode_before,
            "mode_after": self._state,
            "error_code": ""
            if success
            else (
                self._last_recovery_error_code
                or failure_info["error_type"]
                or "login_failed"
            ),
            "error_type": ""
            if success
            else (failure_info["error_type"] or "runtime_error"),
            "runtime_seed_incomplete": bool(
                not success and not trace.get("verify_attempted", False)
            ),
            "runtime_rebind_attempted": bool(
                trace.get("runtime_swap_attempted", False) or success
            ),
            "verify_attempted": bool(trace.get("verify_attempted", False)),
            "recovery_chain_handoff": False,
            "recovery_chain_result": "skipped",
            "recovery_chain_terminal_stage": self._last_recovery_stage,
            "recovery_chain_terminal_error_code": self._last_recovery_error_code,
            "recovery_chain_terminal_error_message": self._last_recovery_error_message,
            "runtime_swap_attempted": bool(trace.get("runtime_swap_attempted", False)),
            "runtime_swap_applied": bool(trace.get("runtime_swap_applied", False)),
            "verify_error_text": str(trace.get("verify_error_text", "") or ""),
            "need_qr_scan": bool(failure_info["need_qr_scan"]),
            "user_action_required": bool(failure_info["user_action_required"]),
            "long_term_expired": bool(failure_info["long_term_expired"]),
            "verify_auth_failure_detected": bool(
                trace.get("verify_attempted", False) and not success
            ),
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
            "error_type": self._last_recovery_error_code,
            "need_qr_scan": bool(self._last_login_trace.get("need_qr_scan")),
            "user_action_required": bool(
                self._last_login_trace.get("user_action_required")
            ),
            "long_term_expired": bool(self._last_login_trace.get("long_term_expired")),
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
            "retry_count_effective": self._retry_count_effective,
            "lock_counter": self._lock_counter,
            "lock_counter_threshold": self._lock_counter_threshold,
            "probe_failure_count": self._probe_failure_count,
            "recovery_failure_count": self._recovery_failure_count,
            "health_probe_attempted": self._health_probe_attempted,
            "health_probe_result": self._last_health_probe_result,
            "health_probe_error": self._last_health_probe_error,
            "keepalive_probe_attempted": self._keepalive_probe_attempted,
            "keepalive_probe_result": self._last_keepalive_probe_result,
            "keepalive_probe_error": self._last_keepalive_probe_error,
            "background_recovery_attempted": self._background_recovery_attempted,
            "background_recovery_result": self._background_recovery_result,
            "background_recovery_error": self._background_recovery_error,
            "lock_transition_reason": self._last_lock_transition_reason,
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

    def auth_short_session_rebuild_debug_state(self) -> dict[str, Any]:
        """短期会话重建调试状态"""
        flow = dict(self._last_auth_recovery_flow_state or {})
        return {
            "state": self._state,
            "cooldown_until": self._cooldown_until,
            "last_short_session_rebuild": self._last_short_session_rebuild_state,
            "last_persistent_auth_relogin": (
                flow.get("fallback_attempt")
                if str(flow.get("used_path", "")).startswith("mijia")
                else flow.get("primary_attempt", {})
            ),
            "last_runtime_rebind": flow.get("rebind", {}),
            "last_verify": flow.get("verify", {}),
            "last_auth_recovery_flow": flow,
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

    def auth_public_status_snapshot(
        self, runtime_auth_ready: bool | None = None
    ) -> dict[str, Any]:
        """聚合对外认证状态所需的最小快照。"""
        status = self.auth_status_snapshot()
        debug = self.auth_debug_state()
        rebuild = self.auth_short_session_rebuild_debug_state()
        last_rebuild = rebuild.get("last_short_session_rebuild", {})
        last_flow = rebuild.get("last_auth_recovery_flow", {})
        auth_mode = str(
            status.get("auth_mode")
            or status.get("mode")
            or debug.get("auth_mode")
            or debug.get("mode")
            or self._state
            or "unknown"
        )
        runtime_ready = (
            bool(runtime_auth_ready)
            if runtime_auth_ready is not None
            else bool(self.mina_service is not None and self.login_signature == self._get_login_signature())
        )
        rebuild_error_code = str(
            last_rebuild.get("error_code", "") or last_flow.get("error_code", "")
        )
        rebuild_failed_reason = str(
            last_rebuild.get("failed_reason", "")
            or last_rebuild.get("error_message", "")
            or last_flow.get("failed_reason", "")
            or last_flow.get("error_message", "")
        )
        rebuild_failed = (
            str(last_rebuild.get("result", "")) == "failed"
            or str(last_flow.get("result", "")) == "failed"
        )
        return {
            "status_state": auth_mode,
            "auth_mode": auth_mode,
            "status_locked": bool(status.get("locked", False)),
            "auth_locked": bool(status.get("locked", False)),
            "auth_lock_until": int(status.get("locked_until_ts") or 0),
            "auth_lock_reason": str(status.get("lock_reason", "") or ""),
            "auth_lock_transition_reason": str(
                status.get("lock_transition_reason", "") or ""
            ),
            "auth_lock_counter": int(status.get("lock_counter") or 0),
            "auth_lock_counter_threshold": int(
                status.get("lock_counter_threshold") or 0
            ),
            "persistent_auth_available": bool(
                status.get("persistent_auth_available", False)
            ),
            "short_session_available": bool(
                status.get("short_session_available", False)
            ),
            "runtime_auth_ready": runtime_ready,
            "recovery_failure_count": int(status.get("recovery_failure_count") or 0),
            "need_qr_scan": bool(status.get("need_qr_scan", False)),
            "user_action_required": bool(status.get("user_action_required", False)),
            "long_term_expired": bool(status.get("long_term_expired", False)),
            "manual_login_required_reason": str(
                status.get("manual_login_required_reason", "")
                or debug.get("manual_login_required_reason", "")
                or ""
            ),
            "runtime_not_ready_reason": str(
                status.get("runtime_not_ready_reason", "")
                or debug.get("runtime_not_ready_reason", "")
                or ""
            ),
            "last_error": str(debug.get("last_auth_error", "") or self._last_error or ""),
            "rebuild_failed": rebuild_failed,
            "rebuild_error_code": rebuild_error_code,
            "rebuild_failed_reason": rebuild_failed_reason[:200]
            if rebuild_failed_reason
            else "",
        }

    def map_auth_public_status(
        self, runtime_auth_ready: bool | None = None
    ) -> dict[str, Any]:
        """将内部认证状态映射为对外稳定口径。"""
        snapshot = self.auth_public_status_snapshot(runtime_auth_ready=runtime_auth_ready)
        auth_mode = str(snapshot.get("auth_mode") or "unknown")
        status_reason = "healthy"
        status_reason_detail = ""
        status_mapping_source = "healthy"
        manual_login_required_reason = str(
            snapshot.get("manual_login_required_reason", "") or ""
        )
        runtime_not_ready_reason = str(
            snapshot.get("runtime_not_ready_reason", "") or ""
        )

        if bool(snapshot.get("auth_locked", False)):
            if bool(snapshot.get("need_qr_scan", False)) or bool(
                snapshot.get("long_term_expired", False)
            ) or bool(snapshot.get("user_action_required", False)):
                status_reason = "manual_login_required"
                manual_login_required_reason = manual_login_required_reason or str(
                    snapshot.get("auth_lock_transition_reason", "") or "manual auth required"
                )
                status_reason_detail = str(
                    snapshot.get("auth_lock_reason", "") or manual_login_required_reason
                )
                status_mapping_source = "locked_manual"
            else:
                status_reason = "temporarily_locked"
                status_reason_detail = str(
                    snapshot.get("auth_lock_transition_reason", "")
                    or snapshot.get("auth_lock_reason", "")
                    or f"retry threshold reached ({snapshot.get('auth_lock_counter', 0)}/{snapshot.get('auth_lock_counter_threshold', 0)})"
                )
                status_mapping_source = "locked_temporary"
        elif not bool(snapshot.get("persistent_auth_available", False)):
            status_reason = "persistent_auth_missing"
            status_reason_detail = "all long-lived auth fields missing from token"
            status_mapping_source = "persistent_auth_missing"
        elif bool(snapshot.get("persistent_auth_available", False)) and not bool(
            snapshot.get("short_session_available", False)
        ):
            if bool(snapshot.get("rebuild_failed", False)):
                status_reason = "short_session_rebuild_failed"
                status_reason_detail = (
                    f"rebuild failed: {snapshot.get('rebuild_error_code', '')}"
                )
                status_mapping_source = "short_session_rebuild_failed"
            else:
                status_reason = "short_session_missing"
                status_reason_detail = "short-lived session tokens missing"
                status_mapping_source = "short_session_missing"
        elif bool(snapshot.get("persistent_auth_available", False)) and bool(
            snapshot.get("short_session_available", False)
        ) and not bool(snapshot.get("runtime_auth_ready", False)):
            status_reason = "runtime_not_ready"
            runtime_not_ready_reason = runtime_not_ready_reason or "runtime auth ready but not verified"
            status_reason_detail = runtime_not_ready_reason
            status_mapping_source = "runtime_not_ready"

        public_status = "unknown"
        if status_reason == "healthy":
            public_status = "ok"
        elif auth_mode == self.STATE_LOCKED:
            public_status = "failed"
        elif auth_mode in (self.STATE_HEALTHY, self.STATE_DEGRADED):
            public_status = "degraded"

        return {
            "status": public_status,
            "auth_mode": auth_mode,
            "status_reason": status_reason,
            "status_reason_detail": status_reason_detail,
            "status_mapping_source": status_mapping_source,
            "recovery_failure_count": int(snapshot.get("recovery_failure_count") or 0),
            "persistent_auth_available": bool(snapshot.get("persistent_auth_available", False)),
            "short_session_available": bool(snapshot.get("short_session_available", False)),
            "runtime_auth_ready": bool(snapshot.get("runtime_auth_ready", False)),
            "auth_locked": bool(snapshot.get("auth_locked", False)),
            "auth_lock_until": int(snapshot.get("auth_lock_until") or 0),
            "auth_lock_reason": str(snapshot.get("auth_lock_reason", "") or ""),
            "auth_lock_transition_reason": str(snapshot.get("auth_lock_transition_reason", "") or ""),
            "auth_lock_counter": int(snapshot.get("auth_lock_counter") or 0),
            "auth_lock_counter_threshold": int(snapshot.get("auth_lock_counter_threshold") or 0),
            "manual_login_required_reason": manual_login_required_reason,
            "runtime_not_ready_reason": runtime_not_ready_reason,
            "last_error": str(snapshot.get("last_error", "") or ""),
            "rebuild_failed": bool(snapshot.get("rebuild_failed", False)),
            "rebuild_error_code": str(snapshot.get("rebuild_error_code", "") or ""),
            "rebuild_failed_reason": str(snapshot.get("rebuild_failed_reason", "") or ""),
        }

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
            "lock_transition_reason": self._last_lock_transition_reason,
            "lock_counter": self._lock_counter,
            "lock_counter_threshold": self._lock_counter_threshold,
            "last_ok_ts": int(self._last_ok_ts * 1000) if self._last_ok_ts > 0 else 0,
            "cooldown_until_ts": int(self._cooldown_until * 1000)
            if self._cooldown_until > 0
            else 0,
            "persistent_auth_available": self._has_persistent_auth_fields(auth_data),
            "short_session_available": bool(
                auth_data.get("serviceToken") or auth_data.get("yetAnotherServiceToken")
            ),
            "retry_count": self._retry_count,
            "retry_count_effective": self._retry_count_effective,
            "probe_failure_count": self._probe_failure_count,
            "recovery_failure_count": self._recovery_failure_count,
            "error_type": self._last_recovery_error_code,
            "need_qr_scan": bool(self._last_login_trace.get("need_qr_scan")),
            "user_action_required": bool(
                self._last_login_trace.get("user_action_required")
            ),
            "long_term_expired": bool(self._last_login_trace.get("long_term_expired")),
            "degraded_entry_reason": self._last_degraded_entry_reason,
            "status_mapping_source": self._last_status_mapping_source,
            "manual_login_required_reason": self._last_manual_login_required_reason,
            "runtime_not_ready_reason": self._last_runtime_not_ready_reason,
        }

    def auth_debug_state(self) -> dict[str, Any]:
        """获取调试状态"""
        auth_data = self._get_auth_data()
        return {
            "last_auth_error": self._last_error,
            "state": self._state,
            "auth_mode": self._state,
            "retry_count": self._retry_count,
            "retry_count_effective": self._retry_count_effective,
            "lock_counter": self._lock_counter,
            "lock_counter_threshold": self._lock_counter_threshold,
            "probe_failure_count": self._probe_failure_count,
            "recovery_failure_count": self._recovery_failure_count,
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
            "error_type": self._last_recovery_error_code,
            "need_qr_scan": bool(self._last_login_trace.get("need_qr_scan")),
            "user_action_required": bool(
                self._last_login_trace.get("user_action_required")
            ),
            "long_term_expired": bool(self._last_login_trace.get("long_term_expired")),
            "degraded_entry_reason": self._last_degraded_entry_reason,
            "status_mapping_source": self._last_status_mapping_source,
            "manual_login_required_reason": self._last_manual_login_required_reason,
            "runtime_not_ready_reason": self._last_runtime_not_ready_reason,
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
