"""
简化版认证管理模块

核心设计原则：
1. 恢复失败就重登，不尝试复用旧 short session
2. 简化状态机：HEALTHY → DEGRADED → LOCKED
3. 播放前主动探测，超过30秒没活动先探测
4. 失败自动重试一次，重试前触发后台恢复
"""

import asyncio
import errno
import json
import math
import os
import socket
import time
from collections.abc import Callable
from typing import Any, TypeVar
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
    "70016",
    "87001",
    "service_login_failed",
    "service_login_code_",
)

LONG_TERM_AUTH_FAILURE_HINTS = (
    "refresh token expired",
    "passport token expired",
    "service token expired",
    "servicetoken expired",
)

# 网络错误关键词
NETWORK_ERROR_ERRNOS = frozenset(
    value
    for value in (
        getattr(socket, "EAI_AGAIN", None),
        getattr(socket, "EAI_FAIL", None),
        errno.ECONNRESET,
        errno.ECONNABORTED,
        errno.ECONNREFUSED,
        errno.ENETDOWN,
        errno.ENETUNREACH,
        errno.EHOSTUNREACH,
        errno.ETIMEDOUT,
    )
    if value is not None
)

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


class _MemoryTokenStore:
    """MiAccount-compatible token store that can never touch .mi.token."""

    def __init__(self, token: dict[str, Any] | None = None):
        self.token = dict(token or {})

    def load_token(self):
        return dict(self.token) or None

    def save_token(self, token=None):
        self.token = dict(token or {})


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


def is_long_term_auth_failure_text(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(
        marker in lowered
        for marker in (
            "refresh token expired",
            "passport token expired",
            "required field missing",
            "missing_persistent_auth_fields",
        )
    )


def classify_auth_challenge(evidence: Any) -> str:
    """Classify structured service-login evidence without null-key false positives."""
    payload = evidence
    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            payload = parsed

    if isinstance(payload, dict):
        code = payload.get("code")
        try:
            code = int(code) if code is not None else None
        except (TypeError, ValueError):
            code = None
        captcha_url = payload.get("captchaUrl") or payload.get("captchaurl")
        if code == 87001 or bool(captcha_url):
            return "interactive_captcha_challenge"
        if code == 70016:
            return "credential_session_rejected"
        return ""

    lowered = str(evidence or "").lower()
    if "87001" in lowered:
        return "interactive_captcha_challenge"
    if "70016" in lowered:
        return "credential_session_rejected"
    return ""


def is_network_error(exc=None, resp=None, body=None) -> bool:
    """Classify transport/DNS failures without treating auth failures as network errors.

    aiohttp wraps DNS and connector failures several layers deep, so inspect the
    exception chain and errno rather than relying on one aiohttp version's classes.
    """
    status = None
    if resp is not None:
        status = getattr(resp, "status", None)
    if status is None and exc is not None:
        status = getattr(exc, "status", None)
    if status is not None:
        try:
            if 500 <= int(status) <= 599:
                return True
        except (TypeError, ValueError):
            pass

    current = exc
    seen: set[int] = set()
    text_parts = []
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        text_parts.append(str(current))
        if isinstance(current, (ConnectionError, TimeoutError, asyncio.TimeoutError)):
            return True
        if isinstance(current, socket.gaierror):
            return True
        for candidate in (current, getattr(current, "os_error", None)):
            if candidate is None:
                continue
            for attr in ("errno", "winerror", "code"):
                value = getattr(candidate, attr, None)
                if value in NETWORK_ERROR_ERRNOS:
                    return True
        current = getattr(current, "__cause__", None) or getattr(
            current, "__context__", None
        )

    if body is not None:
        text_parts.append(str(body))
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
        # 三个时间轴保持独立：会话建立/刷新成功、runtime 健康验证、计划刷新尝试。
        # _last_login_ts 保留为兼容字段，语义限定为会话候选提交成功时间。
        self._last_ok_ts: float = 0
        self._last_login_ts: float = 0
        self._last_session_success_ts: float = 0
        self._last_runtime_verify_ts: float = 0
        self._last_refresh_attempt_ts: float = 0

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
        self._scheduled_refresh_suspended: bool = False
        self._scheduled_refresh_suspend_reason: str = ""
        self._scheduled_refresh_suspend_code: str = ""
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

        # 认证事务锁：候选快照、verify、token/runtime 提交必须串行。
        # _transition_owned 参数用于避免 _try_login -> atomic rebuild 重入死锁。
        self._auth_transition_lock = asyncio.Lock()
        self._runtime_generation: int = 0

        # singleflight 并发控制
        self._recovery_lock = asyncio.Lock()
        self._recovery_complete_event = asyncio.Event()
        self._recovery_backoff_until_ts: float = 0.0
        self._recovery_backoff_sec: int = 10
        self._recovery_leader_ctx: str = ""

        # TTL / scheduled refresh 状态
        self._login_at: float = 0.0
        self._expires_at: float = 0.0
        self._ttl_remaining_seconds: int = 0
        self._last_refresh_trigger: str = ""
        self._last_auth_mode_transition: str = ""
        self._last_auth_error: str = ""
        self._token_save_ts = self._read_save_time
        self._auth_refresh_mode: str = "unknown"
        self._auth_refresh_elapsed_seconds: float = 0.0
        self._auth_refresh_threshold: float | None = None
        self._auth_refresh_interval_seconds: float | None = None

        # keepalive 退化跟踪
        self._keepalive_degraded: bool = False
        self._keepalive_fail_streak: int = 0
        self._keepalive_recovery_cooldown_ts: float = 0.0

        # 兼容性调试状态
        self._last_recovery_result: str = "skipped"
        self._last_recovery_stage: str = ""
        self._last_recovery_error_code: str = ""
        self._last_recovery_error_message: str = ""
        self._last_login_trace: dict[str, Any] = {}
        self._last_runtime_reload_state: dict[str, Any] = {}
        self._last_auto_runtime_reload_state: dict[str, Any] = {}
        self._last_short_session_rebuild_state: dict[str, Any] = {}
        self._last_fast_rebind_state: dict[str, Any] = {}
        self._last_auth_recovery_flow_state: dict[str, Any] = {}

    def _get_random_device_id(self) -> str:
        """生成随机设备ID"""
        import random
        import string

        chars = string.ascii_uppercase + string.digits
        return "".join(random.choices(chars, k=16))

    def _read_save_time(self) -> float:
        """从 token_store 读取 saveTime（秒），用于 TTL 计算。"""
        data = self._get_auth_data()
        st = data.get("saveTime")
        if st is not None:
            return float(st) / 1000.0
        return 0.0

    def _has_persistent_auth_fields(self, auth_data: dict[str, Any]) -> bool:
        """Minimal exchange capability: userId + passToken + deviceId."""
        return bool(
            auth_data.get("userId")
            and auth_data.get("passToken")
            and auth_data.get("deviceId")
        )

    def _has_complete_auth_diagnostics(self, auth_data: dict[str, Any]) -> bool:
        return bool(
            auth_data.get("psecurity")
            and auth_data.get("ssecurity")
            and auth_data.get("cUserId")
        )

    def _new_isolated_mi_account(
        self, session: ClientSession, user_id: str, password: str = "", token: dict[str, Any] | None = None
    ):
        """Construct MiAccount without exposing the canonical persistent token path."""
        memory_store = _MemoryTokenStore(token)
        try:
            return MiAccount(session, user_id, password, memory_store)
        except TypeError:
            # Older miservice builds/test doubles may only expose MiAccount().
            # Explicitly replace any constructor default store with our memory store.
            account = MiAccount()
            account.token_store = memory_store
            if getattr(account, "token", None) is None:
                account.token = memory_store.load_token() or {}
            return account

    # The current Config intentionally has no Xiaomi username/password fields.

    # ==================== 公共接口 ====================

    @property
    def is_auth_error(self):
        """保持向后兼容的属性"""
        return is_auth_error

    async def init_all_data(
        self,
        verified_runtime_only: bool = False,
        refresh_device_map: bool = True,
    ):
        """初始化所有数据，检查登录状态。

        ``verified_runtime_only`` is used after a QR current-auth rebind.  The
        runtime has already passed verification, so this branch must never probe
        auth state or enter any recovery/login path; it only refreshes data that
        depends on the verified runtime.
        """
        self.mi_token_home = os.path.join(self.config.conf_path, ".mi.token")

        if verified_runtime_only:
            if refresh_device_map:
                try:
                    await self.device_manager.update_device_info(self)
                except Exception as exc:
                    self.log.warning("verified-only device refresh failed: %s", exc)
            self._apply_runtime_cookie()
            return

        # 检查是否可以登录
        if not await self.can_login():
            self.log.warning("没有认证 Token，无法登录")
            return

        # 在任何登录/快速重绑定判断前，从持久化认证数据恢复诊断时间轴。
        # env 凭据的年龄不可由磁盘 saveTime 代替；保持 unknown，交给 env rebind。
        auth_data = self._get_auth_data()
        if os.getenv("AUTH_ACCESS_TOKEN") or os.getenv("AUTH_REFRESH_TOKEN"):
            self._sync_auth_ttl({})
        elif auth_data:
            self._sync_auth_ttl(auth_data)

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
        self._apply_runtime_cookie()

    def _apply_runtime_cookie(self) -> None:
        """Apply persisted cookies without invoking authentication recovery."""
        cookie_jar = self.get_cookie()
        if cookie_jar and self.mi_session is not None:
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
        """保持向后兼容的锁定判定；人工认证要求需显式清除。"""
        if self._state == self.STATE_LOCKED and self._last_manual_login_required_reason:
            return True
        return self._state == self.STATE_LOCKED and time.time() < self._locked_until

    def _enter_manual_login_gate(self, reason: str) -> None:
        """Enter the persistent manual-login gate without a temporary expiry."""
        self._state = self.STATE_LOCKED
        self._locked_until = 0
        self._last_manual_login_required_reason = str(reason or "manual auth required")
        self._last_lock_transition_reason = "manual_login_required"

    def _preserve_manual_login_gate(self) -> bool:
        """Keep manual gate authoritative over ordinary failure finalization."""
        if not self._last_manual_login_required_reason:
            return False
        self._state = self.STATE_LOCKED
        self._locked_until = 0
        return True

    def _mark_verified_runtime_recovered(self) -> None:
        """Verified runtime success clears manual/scheduled gates and old errors."""
        self._state = self.STATE_HEALTHY
        self._locked_until = 0
        self._last_manual_login_required_reason = ""
        self._last_lock_transition_reason = ""
        self._scheduled_refresh_suspended = False
        self._scheduled_refresh_suspend_reason = ""
        self._scheduled_refresh_suspend_code = ""
        self._retry_count = 0
        self._retry_count_effective = 0
        self._lock_counter = 0
        self._probe_failure_count = 0
        self._recovery_failure_count = 0
        self._cooldown_until = 0.0
        self._last_retry_increment_reason = ""
        self._last_health_probe_result = "ok"
        self._last_health_probe_error = ""
        self._last_error = ""
        self._last_recovery_error_code = ""
        self._last_recovery_error_message = ""

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
                "kwargs_keys": sorted(kwargs.keys()),
                "ts": int(time.time() * 1000),
            },
        }

    def _classify_auth_failure(
        self,
        err_text: str,
        auth_data: dict[str, Any],
        auth_evidence: Any = None,
    ) -> dict[str, Any]:
        """Classify failures without treating private SSO codes as token expiry."""
        lowered = str(err_text or "").lower()
        challenge = classify_auth_challenge(
            auth_evidence if auth_evidence is not None else lowered
        )
        if is_network_error(exc=RuntimeError(err_text)):
            return {
                "error_type": "network_error",
                "auth_class": "network_error",
                "long_term_expired": False,
                "need_qr_scan": False,
                "user_action_required": False,
            }

        if challenge:
            return {
                "error_type": "auth_error",
                "auth_class": challenge,
                "long_term_expired": False,
                "need_qr_scan": True,
                "user_action_required": True,
            }

        if not auth_data or not self._has_persistent_auth_fields(auth_data):
            return {
                "error_type": "missing_long_term_auth",
                "auth_class": "missing_persistent_capability",
                "long_term_expired": True,
                "need_qr_scan": True,
                "user_action_required": True,
            }

        if is_auth_error_strict(exc=RuntimeError(err_text)):
            long_term_expired = is_long_term_auth_failure_text(lowered) or any(
                hint in lowered for hint in LONG_TERM_AUTH_FAILURE_HINTS
            )
            return {
                "error_type": "auth_error",
                "auth_class": "token_expired" if long_term_expired else "auth_error",
                "long_term_expired": long_term_expired,
                "need_qr_scan": long_term_expired,
                "user_action_required": long_term_expired,
            }

        return {
            "error_type": "runtime_error",
            "auth_class": "runtime_error",
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
        # Manual gate is persistent and outranks cooldown/temporary lock handling.
        if self._last_manual_login_required_reason and not force:
            self._preserve_manual_login_gate()
            return False
        # 如果在冷却期，检查是否过期
        if not force and time.time() < self._cooldown_until:
            return False

        if force:
            # force 是候选恢复/刷新，不代表当前 runtime 已失效。
            # 健康 runtime 上的候选失败不得覆盖现状；真实失效仍由下面的 probe
            # 路径先把状态置为 DEGRADED，再进入可破坏性的恢复流程。
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
                now = time.time()
                self._last_ok_ts = now
                self._last_runtime_verify_ts = now
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
                    self._last_recovery_stage = "probe"
                    self._last_recovery_error_code = "network_error"
                    self._last_recovery_error_message = self._last_error
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

        # 网络冷却结束后，先验证仍在内存中的 runtime。网络恢复不应触发
        # destructive login，也不能清除独立的 scheduled-refresh suspension 门。
        if (
            self._state == self.STATE_DEGRADED
            and not force
            and self.mina_service is not None
            and self._last_degraded_entry_reason == "health_probe_network_error"
        ):
            try:
                self._health_probe_attempted = True
                await self.mina_service.device_list()
            except Exception as e:
                self._last_error = str(e)[:200]
                self._probe_failure_count += 1
                self._last_health_probe_error = self._last_error
                if is_network_error(exc=e):
                    self._last_health_probe_result = "network_error"
                    self._last_recovery_stage = "probe"
                    self._last_recovery_error_code = "network_error"
                    self._last_recovery_error_message = self._last_error
                    self._start_cooldown()
                    return False
                self._last_health_probe_result = "auth_error"
                self._last_recovery_stage = "probe"
                self._last_recovery_error_code = "auth_error"
                self._last_recovery_error_message = self._last_error
            else:
                now = time.time()
                self._state = self.STATE_HEALTHY
                self._last_ok_ts = now
                self._last_runtime_verify_ts = now
                self._last_health_probe_result = "ok"
                self._last_health_probe_error = ""
                self._last_error = ""
                self._last_recovery_stage = ""
                self._last_recovery_error_code = ""
                self._last_recovery_error_message = ""
                self._probe_failure_count = 0
                self._retry_count_effective = 0
                self._cooldown_until = 0.0
                return True

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
                # Manual gate is authoritative; ordinary threshold/cooldown finalization
                # must not downgrade it or add a temporary expiry.
                if self._preserve_manual_login_gate():
                    return False
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
        self,
        reason: str = "",
        preserve_healthy_runtime: bool = False,
        _transition_owned: bool = False,
    ) -> bool:
        """
        简化的登录逻辑

        核心思路：
        1. 读取持久化 token
        2. 创建候选 runtime
        3. 先验证候选 runtime
        4. 验证成功后再原子替换当前 runtime
        """
        if not _transition_owned:
            generation_before = self._runtime_generation
            async with self._auth_transition_lock:
                if (
                    generation_before != self._runtime_generation
                    and self._state == self.STATE_HEALTHY
                    and self.mina_service is not None
                ):
                    return True
                return await self._try_login(
                    reason=reason,
                    preserve_healthy_runtime=preserve_healthy_runtime,
                    _transition_owned=True,
                )

        previous_state = self._state
        previous_locked_until = self._locked_until
        previous_cooldown_until = self._cooldown_until
        previous_retry_count = self._retry_count
        previous_retry_count_effective = self._retry_count_effective
        previous_lock_counter = self._lock_counter
        auth_data = self._get_auth_data()
        runtime_swap_attempted = False
        runtime_swap_applied = False
        verify_attempted = False
        verify_method = "device_list"
        verify_error_text = ""
        login_error_text = ""
        failure_classification: dict[str, Any] = {
            "error_type": "runtime_error",
            "long_term_expired": False,
            "need_qr_scan": False,
            "user_action_required": False,
        }
        structured_failure_classification: dict[str, Any] | None = None

        try:
            if not auth_data:
                self._last_error = "no auth data"
                self._last_recovery_result = "failed"
                self._last_recovery_stage = "read_auth"
                self._last_recovery_error_code = "missing_auth_data"
                self._last_recovery_error_message = self._last_error
                # Route structural absence through the common finalizer so reactive
                # recovery exposes a durable manual gate without any network call.
                failure_classification = self._classify_auth_failure(
                    self._last_error, auth_data
                )
                raise RuntimeError(self._last_error)

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
                raise RuntimeError(self._last_error)

            if os.getenv("AUTH_ACCESS_TOKEN") or os.getenv("AUTH_REFRESH_TOKEN"):
                env_rebind = await self._atomic_runtime_rebind_current_auth(
                    reason=reason or "env_override_rebind",
                    _transition_owned=True,
                )
                if env_rebind.get("ok"):
                    now = time.time()
                    self._last_ok_ts = now
                    self._last_runtime_verify_ts = now
                    self._last_session_success_ts = now
                    self._last_login_ts = now
                    self._mark_verified_runtime_recovered()
                    self._last_recovery_result = "ok"
                    self._last_recovery_stage = "verify"
                    self._last_recovery_error_code = ""
                    self._last_recovery_error_message = ""
                    return True
                self._last_error = str(
                    env_rebind.get("failed_reason") or "env runtime rebind failed"
                )[:200]
                self._last_recovery_stage = "env_override_rebind"
                self._last_recovery_error_code = str(
                    env_rebind.get("error_code") or "env_runtime_rebind_failed"
                )
                self._last_recovery_error_message = self._last_error
                raise RuntimeError(self._last_error)

            # 快速路径：已持有会话 token、但运行时未绑定（典型场景＝进程重启）。
            # 先用持久 token 构造候选运行时并校验，**通过之后才提交到 self**；
            # 成功则完全不需要 login —— 既让重启免扫码恢复，也避免无谓的 login
            # 触发小米风控（2026-09-19 事故根因之一）。
            # 原子性很重要：校验失败时 self 必须保持原样，否则会与并发的成功认证
            # 互相覆盖（把别人刚建好的运行时置空 → 复现"认证不可用"）。
            if self.mina_service is None and (
                auth_data.get("serviceToken") or auth_data.get("yetAnotherServiceToken")
            ):
                candidate = await self._build_verified_runtime_candidate(auth_data)
                if candidate.get("ok"):
                    old_session = self.mi_session
                    if candidate.get("session") is not None:
                        self.mi_session = candidate["session"]
                        self.cookie_jar = self.mi_session.cookie_jar
                    self.device_id = candidate.get("device_id") or self.device_id
                    self.login_account = candidate["account"]
                    self.mina_service = candidate["mina_service"]
                    self.miio_service = candidate["miio_service"]
                    self.login_signature = self._get_login_signature()
                    self._runtime_generation += 1
                    # 旧 session 仍可见期间不 await close；先完成 runtime 引用替换。
                    if old_session is not None and old_session is not self.mi_session:
                        try:
                            await old_session.close()
                        except Exception:
                            pass
                    now = time.time()
                    self._last_ok_ts = now
                    self._last_runtime_verify_ts = now
                    self._last_session_success_ts = now
                    self._last_login_ts = now
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
                    self._last_health_probe_result = "ok"
                    self._last_health_probe_error = ""
                    self._mark_verified_runtime_recovered()
                    self._last_fast_rebind_state = {
                        "result": "ok",
                        "reason": reason,
                        "used_path": "rebind_runtime_from_persisted_session",
                        "ts": int(time.time() * 1000),
                    }
                    self._last_login_trace = {
                        **self._last_login_trace,
                        "stage": "runtime_rebind_fast_path",
                        "result": "ok",
                        "reason": reason,
                        "used_path": "rebind_runtime_from_persisted_session",
                        "login_result": False,
                        "runtime_swap_attempted": True,
                        "runtime_swap_applied": True,
                        "verify_attempted": True,
                        "verify_method": verify_method,
                        "verify_error_text": "",
                        "verify_auth_failure_detected": False,
                        # 显式清掉上一轮可能残留的"需人工"判定，
                        # 避免已 HEALTHY 仍报 need_qr_scan
                        "need_qr_scan": False,
                        "user_action_required": False,
                        "long_term_expired": False,
                    }
                    self.log.info(
                        "认证成功（已持有会话，直接重绑运行时，未发起登录）"
                    )
                    return True

                # 校验失败：self 未被改动，只记审计，落回原有登录/重建流程
                verify_error_text = str(candidate.get("error") or "")[:200]
                self._last_error = verify_error_text or "runtime rebind verify failed"
                self._last_recovery_result = "failed"
                self._last_recovery_stage = "runtime_rebind_fast_path"
                self._last_recovery_error_code = "runtime_rebind_verify_failed"
                self._last_recovery_error_message = self._last_error
                self._last_fast_rebind_state = {
                    "result": "failed",
                    "reason": reason,
                    "used_path": "rebind_runtime_from_persisted_session",
                    "error": self._last_error,
                    "ts": int(time.time() * 1000),
                }
                self._last_login_trace = {
                    **self._last_login_trace,
                    "stage": "runtime_rebind_fast_path",
                    "result": "failed",
                    "reason": reason,
                    "used_path": "rebind_runtime_from_persisted_session",
                    "login_result": False,
                    "runtime_swap_attempted": False,
                    "runtime_swap_applied": False,
                    "verify_attempted": True,
                    "verify_method": verify_method,
                    "verify_error_text": self._last_error,
                    "verify_auth_failure_detected": False,
                }

            if self._has_persistent_auth_fields(auth_data):
                rebuild_out = await self.rebuild_short_session_from_persistent_auth(
                    reason=reason or "ensure_auth",
                    atomic=True,
                    _transition_owned=True,
                )
                if rebuild_out.get("ok"):
                    now = time.time()
                    self._last_ok_ts = now
                    self._last_runtime_verify_ts = now
                    self._last_session_success_ts = now
                    self._last_login_ts = now
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
                    self._mark_verified_runtime_recovered()
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
                rebuild_classification = {
                    key: rebuild_out[key]
                    for key in (
                        "auth_class",
                        "error_type",
                        "long_term_expired",
                        "need_qr_scan",
                        "user_action_required",
                    )
                    if key in rebuild_out
                }
                if rebuild_classification.get("auth_class") and not rebuild_classification.get(
                    "error_type"
                ):
                    rebuild_classification["error_type"] = "auth_error"
                structured_failure_classification = (
                    rebuild_classification
                    if (
                        rebuild_classification.get("auth_class")
                        or any(
                            rebuild_classification.get(key)
                            for key in (
                                "long_term_expired",
                                "need_qr_scan",
                                "user_action_required",
                            )
                        )
                    )
                    else None
                )
                if structured_failure_classification:
                    failure_classification.update(structured_failure_classification)
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
                # 三字段最小能力存在时，atomic rebuild 是唯一恢复路径；失败不得
                # 降级到不存在于当前 Config 的 legacy full-login。
                raise RuntimeError(self._last_error)

            # Config exposes only HTTP Basic credentials, not Xiaomi account/password.
            # There is therefore no legacy full-login capability in this application.
            # Missing the three-field exchange capability goes straight to QR/manual flow;
            # never invoke a password-based full login.
            self._last_error = "persistent auth capability unavailable; QR login required"
            self._last_recovery_stage = "read_auth"
            self._last_recovery_error_code = "missing_persistent_auth_fields"
            failure_classification = self._classify_auth_failure(self._last_error, {})
            raise RuntimeError(self._last_error)

        except Exception as e:
            self._last_error = str(e)[:200]
            self.log.error(f"认证失败: {e}")
            preserved_stage = self._last_recovery_stage
            if structured_failure_classification is None:
                failure_classification = self._classify_auth_failure(
                    self._last_error, auth_data
                )
            else:
                failure_classification = {
                    **failure_classification,
                    **structured_failure_classification,
                }
            self._last_recovery_result = "failed"
            self._last_recovery_stage = (
                preserved_stage
                if preserved_stage in ("short_session_rebuild", "env_override_rebind")
                else ("verify" if verify_attempted else "login")
            )
            self._last_recovery_error_code = failure_classification["error_type"]
            self._last_recovery_error_message = self._last_error
            auth_class = str(failure_classification.get("auth_class") or "")
            scheduled_refresh = "scheduled" in str(reason or "").lower()
            manual_gate = bool(
                failure_classification.get("long_term_expired")
                or auth_class in {"credential_session_rejected", "interactive_captcha_challenge"}
                or (
                    failure_classification.get("need_qr_scan")
                    and not scheduled_refresh
                )
            )
            if manual_gate and not scheduled_refresh:
                self._enter_manual_login_gate(
                    auth_class or self._last_recovery_error_code or "manual auth required"
                )
            fatal_auth = bool(
                failure_classification.get("long_term_expired")
                or failure_classification.get("need_qr_scan")
                or failure_classification.get("user_action_required")
            )
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

            self._recovery_failure_count += 1
            counted, increment_reason = self._should_count_lock_failure(
                failure_classification, self._last_recovery_stage
            )
            self._apply_retry_result(
                counted=counted,
                reason=f"{self._last_recovery_stage}:{increment_reason}",
                failure_classification=failure_classification,
            )

            if (
                (preserve_healthy_runtime or scheduled_refresh)
                and previous_state == self.STATE_HEALTHY
                and not manual_gate
            ):
                self._state = previous_state
                self._locked_until = previous_locked_until
                self._cooldown_until = previous_cooldown_until
                self._retry_count = previous_retry_count
                self._retry_count_effective = previous_retry_count_effective
                self._lock_counter = previous_lock_counter
                return False

            if fatal_auth:
                return False

            if self._preserve_manual_login_gate():
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
        """持久化认证数据。

        仅在 token 内容发生变化时才更新 saveTime，防止每次调用都刷新 TTL。
        """
        if self.token_store is None:
            return
        # 环境凭据只属于运行时接管，任何认证路径都不得固化到 token_store。
        if os.getenv("AUTH_ACCESS_TOKEN") or os.getenv("AUTH_REFRESH_TOKEN"):
            self.log.info("persist_auth_data: skipped because env credentials override runtime")
            return

        merged = dict(auth_data or {})
        old_token = self.token_store.get() if self.token_store else {}
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

            # 仅在新 token 与旧 token 不同时才更新 saveTime
            new_service_token = merged.get("serviceToken") or merged.get("yetAnotherServiceToken")
            old_service_token = old_token.get("serviceToken") or old_token.get("yetAnotherServiceToken")
            if new_service_token and new_service_token != old_service_token:
                merged["saveTime"] = int(time.time() * 1000)
                self.log.info(
                    f"persist_auth_data: token changed, updating saveTime reason={reason}"
                )
            elif "saveTime" not in merged and old_token.get("saveTime"):
                # 保留旧的 saveTime，避免丢失
                merged["saveTime"] = old_token["saveTime"]
        except Exception as e:
            self.log.warning(f"persist token merge failed: {e}")

        self.token_store.commit(merged, reason=reason or "login")

    def _record_short_session_rebuild_state(self, payload: dict[str, Any]) -> None:
        state = dict(payload or {})
        state["ts"] = int(time.time() * 1000)
        self._last_short_session_rebuild_state = state

    def _record_auth_recovery_flow_state(self, payload: dict[str, Any]) -> None:
        state = dict(payload or {})
        self._last_auth_recovery_flow_state = state

    @staticmethod
    def _positive_finite_float(value: Any) -> float | None:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(parsed) or parsed <= 0:
            return None
        return parsed

    def _auth_refresh_threshold_value(self) -> float:
        try:
            value = float(getattr(self.config, "auth_refresh_threshold", 0.3))
        except (TypeError, ValueError):
            value = 0.3
        if not math.isfinite(value):
            value = 0.3
        return min(0.99, max(0.01, value))

    def _auth_refresh_interval_seconds_value(self) -> float:
        value = self._positive_finite_float(
            getattr(self.config, "auth_refresh_interval_hours", 12.0)
        )
        hours = max(0.01, value if value is not None else 12.0)
        return hours * 3600

    def _auth_refresh_min_interval_seconds_value(self) -> float:
        try:
            value = float(
                getattr(self.config, "auth_refresh_min_interval_minutes", 30)
            )
        except (TypeError, ValueError):
            value = 30.0
        if not math.isfinite(value):
            value = 30.0
        return max(0.0, value * 60)

    def _sync_auth_ttl(
        self, auth_data: dict[str, Any] | None = None, login_at_ts: float | None = None
    ) -> None:
        """同步 saveTime 锚点和显式 TTL；未知 TTL 不伪造 expires_at。"""
        data = self._get_auth_data() if auth_data is None else auth_data
        self._auth_refresh_mode = "unknown"
        self._auth_refresh_elapsed_seconds = 0.0
        self._auth_refresh_threshold = None
        self._auth_refresh_interval_seconds = None
        if not data:
            self._login_at = 0.0
            self._expires_at = 0.0
            self._ttl_remaining_seconds = 0
            return

        if login_at_ts is not None:
            try:
                parsed_login_at = float(login_at_ts)
            except (TypeError, ValueError):
                parsed_login_at = 0.0
            self._login_at = parsed_login_at if math.isfinite(parsed_login_at) else 0.0
        else:
            st = data.get("saveTime")
            try:
                parsed_login_at = float(st) / 1000.0 if st is not None else 0.0
            except (TypeError, ValueError):
                parsed_login_at = 0.0
            self._login_at = parsed_login_at if math.isfinite(parsed_login_at) else 0.0

        expires_in = self._positive_finite_float(data.get("expires_in"))
        if expires_in is not None and self._login_at > 0:
            self._auth_refresh_mode = "ttl_ratio"
            self._auth_refresh_threshold = self._auth_refresh_threshold_value()
            self._expires_at = self._login_at + expires_in
            self._ttl_remaining_seconds = max(
                0, int(self._expires_at - time.time())
            )
            return

        self._auth_refresh_mode = "interval_fallback"
        self._auth_refresh_interval_seconds = self._auth_refresh_interval_seconds_value()
        self._expires_at = 0.0
        self._ttl_remaining_seconds = 0
        if self._login_at > 0:
            self._auth_refresh_elapsed_seconds = max(0.0, time.time() - self._login_at)

    async def _maybe_scheduled_refresh(self) -> bool:
        """计划内刷新候选；认证拒绝/captcha 后挂起到 verified recovery。"""
        if self._state != self.STATE_HEALTHY:
            return False
        if self._scheduled_refresh_suspended:
            self._last_refresh_trigger = "scheduled_refresh_suspended"
            return False

        if os.getenv("AUTH_ACCESS_TOKEN") or os.getenv("AUTH_REFRESH_TOKEN"):
            now = time.time()
            self._last_refresh_attempt_ts = now
            self._last_refresh_trigger = "scheduled_env_override_skip"
            return False

        now = time.time()
        self._sync_auth_ttl(login_at_ts=None)
        if self._login_at <= 0:
            return False

        threshold = self._auth_refresh_threshold
        if self._auth_refresh_mode == "ttl_ratio":
            total_ttl = self._expires_at - self._login_at
            if total_ttl <= 0 or threshold is None:
                return False
            remaining_ratio = self._ttl_remaining_seconds / total_ttl
            due = remaining_ratio <= threshold
            trigger_detail = (
                f"mode=ttl_ratio ratio={remaining_ratio:.2f} "
                f"threshold={threshold:.2f} ttl_remaining={self._ttl_remaining_seconds}s"
            )
        else:
            interval_seconds = self._auth_refresh_interval_seconds or 12 * 3600
            elapsed = max(0.0, now - self._login_at)
            self._auth_refresh_elapsed_seconds = elapsed
            due = elapsed >= interval_seconds
            trigger_detail = (
                f"mode=interval_fallback elapsed={elapsed:.0f}s "
                f"interval={interval_seconds:.0f}s"
            )
        if not due:
            return False

        min_interval = self._auth_refresh_min_interval_seconds_value()
        # 节流基准是尝试，不是成功；失败和 capability skip 也占用冷却窗口。
        if (
            self._last_refresh_attempt_ts > 0
            and now - self._last_refresh_attempt_ts < min_interval
        ):
            self.log.info(
                f"_maybe_scheduled_refresh: skip, attempt cooldown not met "
                f"({now - self._last_refresh_attempt_ts:.0f}s < {min_interval}s)"
            )
            return False

        if not self._has_persistent_auth_fields(self._get_auth_data()):
            self._last_refresh_attempt_ts = now
            self._last_refresh_trigger = "scheduled_capability_skip"
            self.log.info(
                "_maybe_scheduled_refresh: skip, persistent login capability unavailable"
            )
            return False

        self._last_refresh_attempt_ts = now
        self._last_refresh_trigger = "scheduled"
        self.log.info(
            f"_maybe_scheduled_refresh: triggering scheduled refresh {trigger_detail}"
        )
        refresh = await self.rebuild_short_session_from_persistent_auth(
            reason="_maybe_scheduled_refresh",
            atomic=True,
        )
        if refresh.get("ok"):
            now = time.time()
            self._last_session_success_ts = now
            self._last_login_ts = now
            self._last_ok_ts = now
            self._last_runtime_verify_ts = now
            self._last_error = ""
            self._last_recovery_result = "ok"
            self._last_recovery_stage = "verify"
            self._last_recovery_error_code = ""
            self._last_recovery_error_message = ""
            self._last_refresh_trigger = "scheduled"
            self._sync_auth_ttl(login_at_ts=None)
            return True

        self._last_error = str(refresh.get("failed_reason") or "scheduled refresh failed")[:200]
        self._last_recovery_result = "failed"
        self._last_recovery_stage = "scheduled_refresh"
        self._last_recovery_error_code = str(
            refresh.get("error_code") or "scheduled_refresh_failed"
        )
        self._last_recovery_error_message = self._last_error
        failure_classification = dict(refresh)
        auth_class = str(failure_classification.get("auth_class") or "")
        if auth_class in {"credential_session_rejected", "interactive_captcha_challenge"}:
            self._scheduled_refresh_suspended = True
            self._scheduled_refresh_suspend_code = auth_class
            self._scheduled_refresh_suspend_reason = self._last_error
            self._last_refresh_trigger = "scheduled_refresh_suspended"
            self.log.warning(
                "scheduled refresh suspended after %s: %s", auth_class, self._last_error
            )
        return False

    async def _try_miaccount_persistent_auth_relogin(
        self,
        before: dict[str, Any] | None = None,
        reason: str = "",
        sid: str = "micoapi",
        writeback: bool = True,
    ) -> dict[str, Any]:
        auth_data = dict(before or {})
        if not self._has_persistent_auth_fields(auth_data):
            return {
                "ok": False,
                "used_path": "miaccount_persistent_auth_exchange",
                "error_code": "missing_persistent_auth_fields",
                "failed_reason": "missing_persistent_auth_fields",
                "error_message": "missing_persistent_auth_fields",
                "http_stage": "serviceLogin",
                "writeback_target": "none",
                "sid": sid,
                "diagnostic": {
                    "reason": reason,
                    "via": "miaccount_persistent_auth_exchange",
                    "response_valid": False,
                },
            }

        login_session = ClientSession()
        try:
            account = self._new_isolated_mi_account(
                login_session, auth_data.get("userId", ""), token=auth_data
            )
            self.set_token(account, auth_data=auth_data)
            resp = await account._serviceLogin(f"serviceLogin?sid={sid}&_json=true")
            if not isinstance(resp, dict):
                return {
                    "ok": False,
                    "used_path": "miaccount_persistent_auth_exchange",
                    "error_code": "invalid_service_login_response",
                    "failed_reason": "invalid_service_login_response",
                    "error_message": "invalid_service_login_response",
                    "http_stage": "serviceLogin",
                    "writeback_target": "none",
                    "sid": sid,
                    "diagnostic": {
                        "reason": reason,
                        "via": "miaccount_persistent_auth_exchange",
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
                "via": "miaccount_persistent_auth_exchange",
                "service_login_code": resp.get("code"),
                "has_location": bool(location),
                "has_nonce": bool(nonce),
                "has_ssecurity": bool(ssecurity),
                "blocked_before_security_token_service": False,
                "security_token_service_invoked": False,
            }
            response_text = json.dumps(resp, ensure_ascii=False, default=str)
            response_code = resp.get("code")
            try:
                response_code = int(response_code) if response_code is not None else None
            except (TypeError, ValueError):
                response_code = None
            captcha_url = resp.get("captchaUrl") or resp.get("captchaurl")
            if (response_code is not None and response_code != 0) or bool(captcha_url):
                error_text = f"service_login_code_{resp.get('code')} {response_text[:500]}"
                classification = self._classify_auth_failure(
                    error_text, auth_data, auth_evidence=resp
                )
                return {
                    "ok": False,
                    "used_path": "miaccount_persistent_auth_exchange",
                    "error_code": "service_login_failed",
                    "failed_reason": error_text,
                    "error_message": str(resp),
                    "http_stage": "serviceLogin",
                    "writeback_target": "none",
                    "sid": sid,
                    **classification,
                    "diagnostic": diagnostic,
                }
            if not location:
                return {
                    "ok": False,
                    "used_path": "miaccount_persistent_auth_exchange",
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
                    "used_path": "miaccount_persistent_auth_exchange",
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
                    "used_path": "miaccount_persistent_auth_exchange",
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
                    "used_path": "miaccount_persistent_auth_exchange",
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
                    "used_path": "miaccount_persistent_auth_exchange",
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
            writeback_target = "none"
            if writeback and self.token_store is not None:
                self.token_store.commit(merged, reason=reason or "persistent_auth_relogin")
                writeback_target = "token_store"
            return {
                "ok": True,
                "used_path": "miaccount_persistent_auth_exchange",
                "serviceToken": service_token,
                "yetAnotherServiceToken": service_token,
                "ssecurity": ssecurity,
                "sid": sid,
                "http_stage": "redirect",
                "writeback_target": writeback_target,
                "auth_data": merged,
                "diagnostic": diagnostic,
            }
        finally:
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

    async def _build_verified_runtime_candidate(
        self, auth_data: dict[str, Any]
    ) -> dict[str, Any]:
        """用持久化 token 构造候选运行时，并在**不修改 self 运行时字段**的前提下校验。

        返回 {"ok": True, "account", "mina_service", "miio_service", "session"}
        或 {"ok": False, "error"}。调用方校验通过后自行提交到 self。

        这样做的目的：校验失败时不会留下半成品运行时，也不会覆盖并发成功的运行时。
        注：沿用既有写法调用 self.set_token()，它会按持久数据幂等地写 self.device_id。
        """
        login_session = ClientSession()
        session_used = False
        try:
            session_used = True
            account = self._new_isolated_mi_account(
                login_session, auth_data.get("userId", ""), token=auth_data
            )
            previous_device_id = self.device_id
            self.set_token(account, auth_data=auth_data)
            self.device_id = previous_device_id
            try:
                mina_service = MiNAService(account)
            except TypeError:
                mina_service = MiNAService()
            try:
                miio_service = MiIOService(account)
            except TypeError:
                miio_service = MiIOService()
            await mina_service.device_list()
            if not session_used:
                try:
                    await login_session.close()
                except Exception:
                    pass
            return {
                "ok": True,
                "account": account,
                "mina_service": mina_service,
                "miio_service": miio_service,
                "session": login_session if session_used else None,
                "device_id": auth_data.get("deviceId") or previous_device_id,
            }
        except Exception as exc:
            try:
                await login_session.close()
            except Exception:
                pass
            return {"ok": False, "error": str(exc)[:200]}

    async def _rebind_runtime_from_auth_data(self, auth_data: dict[str, Any]) -> dict[str, Any]:
        """Build, verify, then commit a runtime candidate without clobbering self."""
        candidate = await self._build_verified_runtime_candidate(auth_data)
        if not candidate.get("ok"):
            return {
                "ok": False,
                "result": "failed",
                "error_code": "runtime_rebind_failed",
                "failed_reason": str(candidate.get("error") or "runtime candidate verify failed"),
            }

        old_session = self.mi_session
        self.device_id = candidate.get("device_id") or self.device_id
        self.login_account = candidate["account"]
        self.mina_service = candidate["mina_service"]
        self.miio_service = candidate["miio_service"]
        if candidate.get("session") is not None:
            self.mi_session = candidate["session"]
            self.cookie_jar = self.mi_session.cookie_jar
        self.login_signature = self._get_login_signature()
        self._runtime_generation += 1
        if self.mi_session is not old_session:
            try:
                await old_session.close()
            except Exception:
                pass
        return {"ok": True, "result": "ok", "verify_result": "ok"}

    async def _atomic_persistent_auth_refresh(
        self, reason: str = "", _transition_owned: bool = False
    ) -> dict[str, Any]:
        """刷新短会话并在 verify 后一次性提交 token/runtime。

        scheduled refresh 专用：不走 password-based full login，也不启用 MiJia 的 destructive
        fallback。primary persistent-auth 调用只生成候选 auth_data；候选 runtime
        验证失败时，token_store、saveTime 和当前 runtime 均保持不变。
        """
        if not _transition_owned:
            async with self._auth_transition_lock:
                return await self._atomic_persistent_auth_refresh(
                    reason=reason, _transition_owned=True
                )

        auth_data = self._get_auth_data()
        if not self._has_persistent_auth_fields(auth_data):
            return {
                "ok": False,
                "result": "failed",
                "error_code": "missing_persistent_auth_fields",
                "primary_error_code": "missing_persistent_auth_fields",
                "primary_result": "failed",
                "failed_reason": "missing_persistent_auth_fields",
                "used_path": "miaccount_persistent_auth_exchange",
                "atomic": True,
            }

        try:
            primary = await self._try_miaccount_persistent_auth_relogin(
                before=auth_data,
                reason=reason,
                sid="micoapi",
                writeback=False,
            )
        except Exception as exc:
            primary = {
                "ok": False,
                "error_code": "persistent_auth_relogin_exception",
                "failed_reason": str(exc)[:200],
            }
        if not primary.get("ok"):
            primary_error = str(
                primary.get("failed_reason")
                or primary.get("error_code")
                or "persistent_auth_relogin_failed"
            )
            classification = {
                key: bool(primary.get(key))
                for key in ("long_term_expired", "need_qr_scan", "user_action_required")
            }
            if primary.get("auth_class"):
                classification["auth_class"] = primary["auth_class"]
            if not any(classification.values()):
                classification = self._classify_auth_failure(primary_error, auth_data)
            return {
                "ok": False,
                "result": "failed",
                "error_code": str(primary.get("error_code") or "persistent_auth_relogin_failed"),
                "primary_error_code": str(primary.get("error_code") or "persistent_auth_relogin_failed"),
                "primary_result": "failed",
                "failed_reason": primary_error,
                "used_path": "miaccount_persistent_auth_exchange",
                **classification,
                "atomic": True,
                "fallback": "disabled_for_scheduled_refresh",
            }

        candidate_auth_data = dict(primary.get("auth_data") or auth_data)
        candidate = await self._build_verified_runtime_candidate(candidate_auth_data)
        if not candidate.get("ok"):
            return {
                "ok": False,
                "result": "failed",
                "error_code": "verify_failed",
                "primary_result": "ok",
                "primary_error_code": "",
                "failed_reason": str(candidate.get("error") or "runtime candidate verify failed"),
                "used_path": "miaccount_persistent_auth_exchange",
                "service_token_written": False,
                "runtime_rebind_result": "skipped",
                "verify_result": "failed",
                "atomic": True,
            }

        env_override = bool(
            os.getenv("AUTH_ACCESS_TOKEN") or os.getenv("AUTH_REFRESH_TOKEN")
        )
        candidate_auth_data["saveTime"] = int(time.time() * 1000)
        try:
            if self.token_store is not None and not env_override:
                self.token_store.commit(
                    candidate_auth_data,
                    reason=reason or "scheduled_persistent_auth_refresh",
                )
        except Exception as exc:
            session = candidate.get("session")
            if session is not None:
                try:
                    await session.close()
                except Exception:
                    pass
            return {
                "ok": False,
                "result": "failed",
                "error_code": "token_commit_failed",
                "primary_result": "ok",
                "primary_error_code": "",
                "failed_reason": str(exc)[:200],
                "used_path": "miaccount_persistent_auth_exchange",
                "service_token_written": False,
                "runtime_rebind_result": "skipped",
                "verify_result": "ok",
                "atomic": True,
            }

        old_session = self.mi_session
        new_session = candidate.get("session") or self.mi_session
        new_cookie_jar = (
            new_session.cookie_jar
            if candidate.get("session") is not None
            else self.cookie_jar
        )
        # 一次性完成全部 runtime 引用提交；generation 在任何 close await 前增长。
        self.device_id = candidate.get("device_id") or self.device_id
        self.login_account = candidate["account"]
        self.mina_service = candidate["mina_service"]
        self.miio_service = candidate["miio_service"]
        self.mi_session = new_session
        self.cookie_jar = new_cookie_jar
        self.login_signature = self._get_login_signature()
        self._runtime_generation += 1
        self._mark_verified_runtime_recovered()
        self._last_recovery_result = "ok"
        self._last_recovery_error_code = ""
        self._last_recovery_error_message = ""
        if old_session is not self.mi_session:
            try:
                await old_session.close()
            except Exception:
                pass
        return {
            "ok": True,
            "result": "ok",
            "primary_result": "ok",
            "primary_error_code": "",
            "used_path": "miaccount_persistent_auth_exchange",
            "service_token_written": bool(self.token_store is not None and not env_override),
            "runtime_rebind_result": "ok",
            "verify_result": "ok",
            "atomic": True,
        }

    async def rebuild_short_session_from_persistent_auth(
        self,
        reason: str = "",
        atomic: bool = True,
        _transition_owned: bool = False,
    ) -> dict[str, Any]:
        if atomic:
            started_at = int(time.time() * 1000)
            out = await self._atomic_persistent_auth_refresh(
                reason=reason, _transition_owned=_transition_owned
            )
            finished_at = int(time.time() * 1000)
            self._record_short_session_rebuild_state(out)
            self._record_auth_recovery_flow_state({
                "reason": reason,
                "started_at": started_at,
                "primary_attempt": {
                    "attempt_at": started_at,
                    "used_path": out.get("used_path", "miaccount_persistent_auth_exchange"),
                    "error_code": out.get("primary_error_code", "")
                    if not out.get("primary_result") == "ok"
                    else "",
                    "result": out.get("primary_result")
                    or ("ok" if out.get("ok") else "failed"),
                },
                "fallback_attempt": {
                    "result": "skipped",
                    "skipped_reason": "disabled_for_scheduled_refresh",
                },
                "rebind": {
                    "result": out.get("runtime_rebind_result", "skipped"),
                },
                "verify": {
                    "result": out.get("verify_result", "skipped"),
                    "error_code": out.get("error_code", "")
                    if out.get("verify_result") == "failed"
                    else "",
                },
                "result": "ok" if out.get("ok") else "failed",
                "used_path": out.get("used_path", "miaccount_persistent_auth_exchange"),
                "atomic": True,
                "finished_at": finished_at,
            })
            return out
        if not _transition_owned:
            async with self._auth_transition_lock:
                return await self.rebuild_short_session_from_persistent_auth(
                    reason=reason, atomic=False, _transition_owned=True
                )

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
                "used_path": "miaccount_persistent_auth_exchange",
                "error_code": "missing_persistent_auth_fields",
                "failed_reason": "missing_persistent_auth_fields",
                "service_token_written": False,
                "runtime_rebind_result": "skipped",
                "verify_result": "skipped",
            }
            flow["primary_attempt"] = {
                "attempt_at": started_at,
                "used_path": "miaccount_persistent_auth_exchange",
                "error_code": "missing_persistent_auth_fields",
                "result": "failed",
            }
            flow["result"] = "failed"
            flow["used_path"] = "miaccount_persistent_auth_exchange"
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
            "used_path": str(primary.get("used_path", "") or "miaccount_persistent_auth_exchange"),
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

        used_path = str(relogin.get("used_path", "") or "miaccount_persistent_auth_exchange")
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
            self.token_store.commit(merged, reason=reason or "short_session_rebuild")

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
            now = time.time()
            self._last_ok_ts = now
            self._last_runtime_verify_ts = now
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

    def set_token(self, account, auth_data: dict[str, Any] | None = None):
        """设置 token 到 account；候选路径可使用未提交的 auth_data。"""
        user_data = dict(auth_data) if auth_data is not None else self._get_auth_data()
        if user_data:
            candidate_device_id = user_data.get("deviceId") or self.device_id
            token_payload = {
                "passToken": user_data["passToken"],
                "userId": user_data["userId"],
                "deviceId": candidate_device_id,
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
            # miservice 的 MiAccount.mi_request(sid) 只在 token 中已存在该 sid 时才直接使用，
            # 否则会退到 login(sid)；而本部署没有账号口令（.env 无密码），那条路必然失败。
            # 持久化的 auth.json 把 micoapi 会话拆成了 ssecurity + serviceToken，这里把它
            # 拼回 miservice 期望的 (ssecurity, serviceToken) 元组，运行时才能在“零登录”
            # 前提下从磁盘重建（2026-09-19 事故：重启后永远只能走注定失败的 login）。
            if token_payload.get("ssecurity") and (
                token_payload.get("serviceToken")
                or token_payload.get("yetAnotherServiceToken")
            ):
                token_payload["micoapi"] = (
                    token_payload["ssecurity"],
                    token_payload.get("serviceToken")
                    or token_payload["yetAnotherServiceToken"],
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
            return None

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
                    if self._last_manual_login_required_reason:
                        raise RuntimeError("认证需要人工登录")
                    if attempt < retry:
                        # 触发后台恢复并等待；manual gate 不走二次快速重试
                        self._schedule_background_recovery()
                        await asyncio.sleep(2)
                        continue
                    raise RuntimeError("认证不可用")

                # 尝试调用
                result = await fn(*args, **kwargs)
                now = time.time()
                self._last_ok_ts = now
                self._last_runtime_verify_ts = now
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
                    # 认证错误，触发恢复；manual gate 不走二次快速重试
                    if self._last_manual_login_required_reason:
                        self._preserve_manual_login_gate()
                        raise
                    self._state = self.STATE_DEGRADED
                    if attempt < retry:
                        self._schedule_background_recovery()
                        await asyncio.sleep(2)
                        continue

                if attempt < retry:
                    continue

        raise last_err

    def _schedule_background_recovery(self, ctx: str = ""):
        """安排后台恢复任务（singleflight 保护）。

        - 如果已有 leader 在执行恢复，新的调用作为 follower 等待
        - 如果处于 backoff 期，跳过
        """
        role, reason = self._try_acquire_recovery_leader(ctx=ctx or "background_recovery")
        if role == "blocked":
            return
        if role == "follower":
            # follower: 确保已有 recovery task 在跑，不重复创建
            if self._recovery_task is not None and not self._recovery_task.done():
                return
            # 如果没有 task 但 inflight 为 True（异常情况），重置
            self._recovery_inflight = False
            return

        # leader: 如果已经有 task 在执行就不再创建
        if self._recovery_task is not None and not self._recovery_task.done():
            return

        async def _do_recovery():
            # 在协程内部使用锁确保互斥
            leader_role, leader_reason = await self._acquire_recovery_leader_lock(
                ctx=ctx or "background_recovery"
            )
            if leader_role != "leader":
                # 被另一个协程抢先了
                if leader_role == "follower":
                    await self._wait_for_recovery_complete()
                return

            self._background_recovery_attempted = True
            result = "failed"
            try:
                success = await self._try_login()
                if success:
                    self._background_recovery_result = "ok"
                    self._background_recovery_error = ""
                    self._state = self.STATE_HEALTHY
                    self.log.info("后台恢复成功")
                    result = "ok"
                else:
                    self._background_recovery_result = "failed"
                    self._background_recovery_error = self._last_error
                    if self._preserve_manual_login_gate():
                        self.log.warning("后台恢复失败，保持人工登录门禁")
                    elif self._lock_counter >= self._lock_counter_threshold:
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
                if self._preserve_manual_login_gate():
                    self.log.warning("后台恢复异常，保持人工登录门禁")
                elif self._lock_counter >= self._lock_counter_threshold:
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
                await self._release_recovery_leader(result=result)
                self._recovery_task = None

        # owner: auth_manager (recovery)
        self._recovery_task = asyncio.create_task(_do_recovery())

    # ==================== singleflight 并发控制 ====================

    def _try_acquire_recovery_leader(self, ctx: str = "") -> tuple[str, str]:
        """尝试获取恢复 leader 角色。

        返回 (role, reason)，其中 role 为 "leader" | "follower" | "blocked"。
        注意：此方法不持有锁，调用者需在获取 leader 后通过
        _release_recovery_leader 释放。
        """
        # 检查 backoff
        if self._recovery_backoff_until_ts > 0 and time.time() < self._recovery_backoff_until_ts:
            remaining = self._recovery_backoff_until_ts - time.time()
            self.log.info(
                "auth_recovery_singleflight: role=blocked action=backoff_skip "
                f"reason=backoff_active remaining={remaining:.1f}s ctx={ctx}"
            )
            return ("blocked", "backoff_active")

        # 检查是否已有 inflight recovery
        if self._recovery_inflight:
            self.log.info(
                "auth_recovery_singleflight: role=follower "
                f"action=join_existing_recovery leader_ctx={self._recovery_leader_ctx}"
            )
            return ("follower", "leader_running")

        return ("leader", "acquired")

    async def _acquire_recovery_leader_lock(self, ctx: str = "") -> tuple[str, str]:
        """带锁的 leader 获取，用于在临界区内判断并设置 inflight。

        返回 (role, reason)，其中 role 为 "leader" | "follower" | "blocked"。
        """
        async with self._recovery_lock:
            # backoff 检查
            if self._recovery_backoff_until_ts > 0 and time.time() < self._recovery_backoff_until_ts:
                remaining = self._recovery_backoff_until_ts - time.time()
                self.log.info(
                    "auth_recovery_singleflight: role=blocked action=backoff_skip "
                    f"reason=backoff_active remaining={remaining:.1f}s ctx={ctx}"
                )
                return ("blocked", "backoff_active")

            if self._recovery_inflight:
                self.log.info(
                    "auth_recovery_singleflight: role=follower "
                    f"action=join_existing_recovery leader_ctx={self._recovery_leader_ctx}"
                )
                return ("follower", "leader_running")

            self._recovery_inflight = True
            self._recovery_leader_ctx = ctx
            self._recovery_complete_event.clear()
            self.log.info(
                f"auth_recovery_singleflight: role=leader action=start ctx={ctx}"
            )
            return ("leader", "acquired")

    async def _release_recovery_leader(self, result: str = "ok") -> None:
        """释放恢复 leader 并通知所有等待的 follower。"""
        async with self._recovery_lock:
            self._recovery_inflight = False
            self._recovery_leader_ctx = ""
            if result != "ok" and not self._last_manual_login_required_reason:
                self._recovery_backoff_until_ts = time.time() + self._recovery_backoff_sec
            self.log.info(
                f"auth_recovery_singleflight: role=leader action=finish result={result}"
            )
        self._recovery_complete_event.set()

    async def _wait_for_recovery_complete(self) -> None:
        """Follower 等待 leader 完成恢复。"""
        try:
            await asyncio.wait_for(
                self._recovery_complete_event.wait(),
                timeout=self._recovery_backoff_sec + 30,
            )
        except asyncio.TimeoutError:
            self.log.warning("auth_recovery_singleflight: follower wait timed out")

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
        如果发现问题，触发恢复；如果健康，尝试 Scheduled Refresh。
        """
        while True:
            try:
                await asyncio.sleep(interval_sec)

                # 检查认证状态
                if not await self.ensure_auth():
                    self._last_keepalive_probe_result = "ensure_auth_failed"
                    self._last_keepalive_probe_error = str(self._last_error or "")[:200]
                    self._keepalive_fail_streak += 1
                    if self._keepalive_fail_streak >= 3:
                        self._keepalive_degraded = True
                    self.log.warning("Keepalive: 认证不健康，触发恢复")
                    # 不等待恢复完成，下一轮再检查
                else:
                    # 认证健康，尝试调用 device_list 保持连接
                    try:
                        self._keepalive_probe_attempted = True
                        self._last_keepalive_probe_result = "ok"
                        self._last_keepalive_probe_error = ""
                        await self.mina_service.device_list()
                        now = time.time()
                        self._last_ok_ts = now
                        self._last_runtime_verify_ts = now
                        # 恢复 keepalive 退化状态
                        if self._keepalive_degraded:
                            self._keepalive_degraded = False
                            self._keepalive_fail_streak = 0
                            self._keepalive_recovery_cooldown_ts = 0.0
                            self.log.info("Keepalive: 从退化状态恢复")
                        # Scheduled Refresh: 在健康状态时尝试计划内刷新
                        if self._state == self.STATE_HEALTHY:
                            await self._maybe_scheduled_refresh()
                    except Exception as e:
                        self._last_keepalive_probe_result = "probe_failed"
                        self._last_keepalive_probe_error = str(e)[:200]
                        self._probe_failure_count += 1
                        self._keepalive_fail_streak += 1
                        if self._keepalive_fail_streak >= 3:
                            self._keepalive_degraded = True
                        self._last_degraded_entry_reason = "keepalive_probe_failed"
                        self.log.warning(f"Keepalive probe failed: {e}")
                        self._state = self.STATE_DEGRADED
                        self._start_cooldown()

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.log.error(f"Keepalive loop error: {e}")

    # ==================== API 兼容接口 ====================

    async def _atomic_runtime_rebind_current_auth(
        self,
        reason: str = "",
        _transition_owned: bool = False,
        used_path: str = "runtime_rebind_env_override",
        record_rebuild: bool = False,
    ) -> dict[str, Any]:
        if not _transition_owned:
            async with self._auth_transition_lock:
                return await self._atomic_runtime_rebind_current_auth(
                    reason=reason,
                    _transition_owned=True,
                    used_path=used_path,
                    record_rebuild=record_rebuild,
                )
        candidate = await self._build_verified_runtime_candidate(self._get_auth_data())
        if not candidate.get("ok"):
            out = {
                "ok": False,
                "result": "failed",
                "error_code": "verify_failed",
                "failed_reason": str(candidate.get("error") or "runtime verify failed"),
                "runtime_rebind_result": "skipped",
                "verify_result": "failed",
                "used_path": used_path,
                "service_token_written": False,
            }
            if record_rebuild:
                self._record_short_session_rebuild_state(out)
            return out
        old_session = self.mi_session
        new_session = candidate.get("session") or self.mi_session
        self.device_id = candidate.get("device_id") or self.device_id
        self.login_account = candidate["account"]
        self.mina_service = candidate["mina_service"]
        self.miio_service = candidate["miio_service"]
        self.mi_session = new_session
        self.cookie_jar = (
            new_session.cookie_jar
            if candidate.get("session") is not None
            else self.cookie_jar
        )
        self.login_signature = self._get_login_signature()
        self._runtime_generation += 1
        verified_at = time.time()
        self._last_ok_ts = verified_at
        self._last_runtime_verify_ts = verified_at
        self._last_session_success_ts = verified_at
        self._last_login_ts = verified_at
        self._mark_verified_runtime_recovered()
        if old_session is not self.mi_session:
            try:
                await old_session.close()
            except Exception:
                pass
        out = {
            "ok": True,
            "result": "ok",
            "runtime_rebind_result": "ok",
            "verify_result": "ok",
            "used_path": used_path,
            "service_token_written": False,
        }
        if record_rebuild:
            self._record_short_session_rebuild_state(out)
            self._record_auth_recovery_flow_state({
                "reason": reason,
                "primary_attempt": {"result": "skipped", "used_path": used_path},
                "fallback_attempt": {"result": "skipped"},
                "rebind": {"result": "ok", "used_path": used_path},
                "verify": {"result": "ok", "used_path": used_path},
                "result": "ok",
                "used_path": used_path,
                "service_token_written": False,
                "finished_at": int(time.time() * 1000),
            })
        return out

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
        started_at = int(time.time() * 1000)
        preserve_healthy_runtime = state_before == self.STATE_HEALTHY
        token_store_reloaded = False
        if self.token_store is not None:
            self.token_store.reload_from_disk()
            token_store_reloaded = True
            self._sync_auth_ttl()
        env_override = bool(
            os.getenv("AUTH_ACCESS_TOKEN") or os.getenv("AUTH_REFRESH_TOKEN")
        )
        rebind_current_auth = bool(kwargs.get("rebind_current_auth", False))
        if rebind_current_auth:
            rebuild_out = await self._atomic_runtime_rebind_current_auth(
                reason=reason,
                used_path="qrcode_persisted_short_session_rebind",
                record_rebuild=True,
            )
        elif env_override:
            rebuild_out = await self._atomic_runtime_rebind_current_auth(
                reason=reason,
                used_path="runtime_rebind_env_override",
                record_rebuild=False,
            )
        else:
            rebuild_out = await self.rebuild_short_session_from_persistent_auth(
                reason=reason,
                atomic=True,
            )
        success = bool(rebuild_out.get("ok"))
        if success:
            self._mark_verified_runtime_recovered()
            self._last_recovery_result = "ok"
            self._last_recovery_stage = "verify"
            self._last_recovery_error_code = ""
            self._last_recovery_error_message = ""
            self._last_error = ""
        else:
            self._last_recovery_result = "failed"
            self._last_recovery_stage = "manual_reload"
            self._last_recovery_error_code = str(
                rebuild_out.get("error_code") or "manual_reload_failed"
            )
            self._last_recovery_error_message = str(
                rebuild_out.get("failed_reason") or "manual reload failed"
            )[:200]
            self._last_error = self._last_recovery_error_message
        device_map_refreshed = False
        if success:
            try:
                update_result = await self.device_manager.update_device_info(self)
                device_map_refreshed = bool(update_result)
            except Exception as exc:
                self.log.warning("runtime reload device refresh failed: %s", exc)
        trace = {
            "runtime_swap_attempted": bool(
                rebuild_out.get("runtime_rebind_result") not in (None, "", "skipped")
            ),
            "runtime_swap_applied": success,
            "verify_attempted": bool(rebuild_out.get("verify_result") != "skipped"),
            "verify_error_text": "" if success else self._last_error,
            "device_map_refreshed": device_map_refreshed,
            "started_at": started_at,
            "finished_at": int(time.time() * 1000),
        }
        if success:
            # A successful runtime verify must not be classified from a stale/empty
            # error string. Keep the login trace explicitly clean and auditable.
            failure_info = {
                "error_type": "",
                "auth_class": "",
                "long_term_expired": False,
                "need_qr_scan": False,
                "user_action_required": False,
            }
            self._last_refresh_trigger = reason
            self._last_login_trace = {
                **self._last_login_trace,
                "stage": "runtime_rebind_current_auth"
                if rebind_current_auth
                else "manual_reload_runtime",
                "result": "ok",
                "reason": reason,
                "used_path": rebuild_out.get("used_path", ""),
                "auth_class": "",
                "error_type": "",
                "need_qr_scan": False,
                "user_action_required": False,
                "long_term_expired": False,
                "login_result": False,
                "runtime_swap_attempted": True,
                "runtime_swap_applied": True,
                "verify_attempted": True,
                "verify_method": "device_list",
                "verify_error_text": "",
                "verify_auth_failure_detected": False,
            }
        else:
            failure_info = self._classify_auth_failure(
                self._last_error, self._get_auth_data()
            )
            if any(
                rebuild_out.get(key)
                for key in ("long_term_expired", "need_qr_scan", "user_action_required")
            ):
                failure_info = {
                    **failure_info,
                    "auth_class": rebuild_out.get("auth_class") or failure_info.get("auth_class", ""),
                    "long_term_expired": bool(rebuild_out.get("long_term_expired")),
                    "need_qr_scan": bool(rebuild_out.get("need_qr_scan")),
                    "user_action_required": bool(rebuild_out.get("user_action_required")),
                }
            self._last_login_trace = {
                **self._last_login_trace,
                "auth_class": failure_info.get("auth_class", ""),
                "error_type": failure_info.get("error_type", ""),
                "need_qr_scan": bool(failure_info["need_qr_scan"]),
                "user_action_required": bool(failure_info["user_action_required"]),
                "long_term_expired": bool(failure_info["long_term_expired"]),
            }
        manual_login_required = bool(
            not success
            and (
                failure_info["long_term_expired"]
                or failure_info["need_qr_scan"]
                or failure_info["user_action_required"]
            )
        )
        if manual_login_required:
            self._enter_manual_login_gate(
                self._last_recovery_error_code or "manual auth required"
            )
        if (
            not success
            and preserve_healthy_runtime
            and not manual_login_required
        ):
            self._state = state_before
            self._locked_until = locked_before
            self._cooldown_until = cooldown_before
        runtime_auth_ready = bool(
            success or (preserve_healthy_runtime and not manual_login_required)
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
            "token_saved": bool(
                success
                and not env_override
                and not rebind_current_auth
                and rebuild_out.get("service_token_written", False)
            ),
            "token_loaded": bool(self._get_auth_data()),
            "token_store_reloaded": token_store_reloaded,
            "runtime_rebound": success,
            "device_map_refreshed": bool(trace.get("device_map_refreshed", False)),
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
                "last_refresh_attempt_ts": int(self._last_refresh_attempt_ts * 1000)
                if self._last_refresh_attempt_ts > 0
                else None,
            },
        }

    def auth_recovery_debug_state(self) -> dict[str, Any]:
        """认证恢复调试状态"""
        return {
            "state": self._state,
            "last_error": self._last_error,
            "error_type": self._last_recovery_error_code,
            "auth_class": self._last_login_trace.get("auth_class", ""),
            "scheduled_refresh_suspended": self._scheduled_refresh_suspended,
            "scheduled_refresh_suspend_reason": self._scheduled_refresh_suspend_reason,
            "scheduled_refresh_suspend_code": self._scheduled_refresh_suspend_code,
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
            "last_fast_rebind": self._last_fast_rebind_state,
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
            "last_session_success_ts": int(self._last_session_success_ts * 1000)
            if self._last_session_success_ts > 0
            else 0,
            "last_runtime_verify_ts": int(self._last_runtime_verify_ts * 1000)
            if self._last_runtime_verify_ts > 0
            else 0,
            "last_refresh_attempt_ts": int(self._last_refresh_attempt_ts * 1000)
            if self._last_refresh_attempt_ts > 0
            else 0,
            "last_refresh_trigger": self._last_refresh_trigger,
            "scheduled_refresh_suspended": self._scheduled_refresh_suspended,
            "scheduled_refresh_suspend_reason": self._scheduled_refresh_suspend_reason,
            "scheduled_refresh_suspend_code": self._scheduled_refresh_suspend_code,
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
            "auth_class": self._last_login_trace.get("auth_class", ""),
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
        """获取调试状态（紧凑版，供测试与 API 使用）。

        包含 TTL 相关字段、模式转换记录等核心调试信息。
        扩展字段请使用 auth_status_snapshot()。
        """
        auth_data = self._get_auth_data()
        return {
            "state": self._state,
            "auth_mode": self._state,
            "last_auth_mode_transition": self._last_auth_mode_transition or {},
            "login_at": self._login_at,
            "expires_at": self._expires_at,
            "ttl_remaining_seconds": self._ttl_remaining_seconds,
            "last_refresh_trigger": self._last_refresh_trigger,
            "scheduled_refresh_suspended": self._scheduled_refresh_suspended,
            "scheduled_refresh_suspend_reason": self._scheduled_refresh_suspend_reason,
            "scheduled_refresh_suspend_code": self._scheduled_refresh_suspend_code,
            "last_auth_error": self._last_error,
            "long_term_expired": bool(self._last_login_trace.get("long_term_expired")),
            "persistent_auth_available": self._has_persistent_auth_fields(auth_data),
            "short_session_available": bool(
                auth_data.get("serviceToken") or auth_data.get("yetAnotherServiceToken")
            ),
        }

    def clear_auth_lock(self, reason: str = "", mode: str = "degraded"):
        """清除认证锁定"""
        self._last_manual_login_required_reason = ""
        if mode == "healthy":
            self._state = self.STATE_HEALTHY
            self._auth_mode = self.STATE_HEALTHY
        else:
            self._state = self.STATE_DEGRADED
            self._auth_mode = self.STATE_DEGRADED
        self._locked_until = 0
        self.log.info(f"认证锁定已清除: {reason}, 模式: {mode}")

    # ==================== 状态机兼容层 ====================

    @property
    def _auth_mode(self) -> str:
        """兼容旧接口：_auth_mode 映射到 _state。"""
        return self._state

    @_auth_mode.setter
    def _auth_mode(self, value: str) -> None:
        self._state = value

    def _transition_auth_mode(self, target: str, reason: str = "") -> None:
        """兼容旧状态机接口：记录模式转换。"""
        prev = self._state
        self._state = target
        self._last_auth_mode_transition = {
            "from": prev,
            "to": target,
            "reason": reason,
            "ts": int(time.time() * 1000),
        }

    def _emit_auth_state(
        self,
        auth_step: str = "",
        auth_result: str = "",
        refresh_trigger: str = "",
        auth_mode_before: str = "",
        auth_mode_after: str = "",
    ) -> None:
        """发出认证状态日志事件。"""
        import json as _json

        payload = {
            "event": "auth_state",
            "auth_session_id": str(int(time.time() * 1000)),
            "login_at": self._login_at,
            "expires_at": self._expires_at,
            "ttl_remaining_seconds": self._ttl_remaining_seconds,
            "estimated_ttl": self._ttl_remaining_seconds,
            "refresh_trigger": refresh_trigger or self._last_refresh_trigger,
            "auth_step": auth_step,
            "auth_result": auth_result,
            "auth_mode_before": auth_mode_before,
            "auth_mode_after": auth_mode_after,
        }
        self.log.info(_json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


AuthManager = SimpleAuthManager
