"""认证管理模块

本模块负责小米账号认证与会话管理，包括：
- 小米账号登录
- Cookie管理
- 会话维护
- 设备ID更新
"""

import json
import os
import time
import asyncio
from copy import deepcopy
from typing import Any

from aiohttp import ClientSession
from miservice import MiAccount, MiIOService, MiNAService

from xiaomusic.config import Device
from xiaomusic.const import COOKIE_TEMPLATE
from xiaomusic.utils.system_utils import (
    get_random,
    parse_cookie_string_to_dict,
    parse_cookie_string,
)


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

AUTH_REFRESH_RELOGIN_KEYWORDS = (
    "refresh token failed",
    "refresh token missing",
    "missing refresh",
    "invalid_grant",
    "re-login",
    "relogin",
    "please login",
    "请重新登录",
    "刷新token失败",
)


def is_auth_error(exc=None, resp=None, body=None) -> bool:
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
            for key in ("code", "message", "msg", "error", "detail", "description"):
                val = body.get(key)
                if val is not None:
                    text_parts.append(str(val))
        else:
            text_parts.append(str(body))
    if exc is not None:
        text_parts.append(str(exc))
    lowered = " ".join(text_parts).lower()
    if any(word in lowered for word in NETWORK_ERROR_KEYWORDS):
        return False
    return any(word in lowered for word in AUTH_ERROR_KEYWORDS)


def is_network_error(exc=None, resp=None, body=None) -> bool:
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


class AuthManager:
    """认证管理器

    负责处理小米账号的登录、认证和会话管理。
    """

    def __init__(self, config, log, device_manager, token_store=None):
        """初始化认证管理器

        Args:
            config: 配置对象
            log: 日志对象
        """
        self.config = config
        self.log = log
        self.mi_token_home = os.path.join(self.config.conf_path, ".mi.token")
        self.oauth2_token_path = self.config.oauth2_token_path
        self.token_store = token_store

        # 认证状态
        self.mina_service = None
        self.miio_service = None
        self.login_acount = None
        self.login_signature = None
        self.cookie_jar = None
        self._last_login_ts = 0
        self._login_cooldown_sec = 300
        self._relogin_lock = asyncio.Lock()
        self._relogin_inflight_ts = 0.0
        self._last_ok_ts = 0.0
        self._last_relogin_reason = ""
        self._relogin_fail_streak = 0
        self._next_relogin_allowed_ts = 0.0
        self._keepalive_fail_streak = 0
        self._keepalive_degraded = False
        self._last_refresh_ts = 0.0
        self._last_refresh_error = ""
        self._auth_mode = "healthy"
        self._auth_locked_until_ts = 0.0
        self._auth_lock_reason = ""

        self._high_freq_methods = {
            "device_list",
            "get_latest_ask",
        }
        self._hf_last_call_ts: dict[str, float] = {}
        self._hf_auth_fail_streak: dict[str, int] = {}
        self._hf_cooldown_until_ts: dict[str, float] = {}

        # 当前设备DID（用于设备ID更新）
        self._cur_did = None
        self.device_id = get_random(16).upper()
        self.mi_session = ClientSession()
        self.device_manager = device_manager

    def _auth_log(self, reason: str, action: str, result: str, err: str = "") -> None:
        err_text = (err or "").replace("\n", " ")[:200]
        self.log.info(
            "auth_flow reason=%s action=%s result=%s error=%s",
            reason,
            action,
            result,
            err_text,
        )

    def _set_auth_mode(self, mode: str, reason: str = "") -> None:
        if self._auth_mode == mode:
            return
        self._auth_mode = mode
        self._auth_log(
            reason=reason or "auth_state",
            action="auth_mode",
            result=mode,
        )

    def _is_auth_locked(self) -> bool:
        return time.time() < self._auth_locked_until_ts

    @property
    def _auth_lock_sec(self) -> int:
        return max(300, int(getattr(self.config, "auth_lock_seconds", 1800)))

    def _enter_auth_lock(self, reason: str = "") -> None:
        self._auth_locked_until_ts = time.time() + self._auth_lock_sec
        self._auth_lock_reason = (reason or "auth failed")[:200]
        self._set_auth_mode("locked", reason=reason or "auth_lock")
        self._auth_log(
            reason=reason or "auth_lock",
            action="lock",
            result="fail",
            err=f"until={int(self._auth_locked_until_ts)} reason={self._auth_lock_reason}",
        )

    def clear_auth_lock(self, reason: str = "", mode: str = "degraded") -> None:
        self._auth_locked_until_ts = 0.0
        self._auth_lock_reason = ""
        self._relogin_fail_streak = 0
        self._next_relogin_allowed_ts = 0.0
        if mode in {"healthy", "degraded"}:
            self._set_auth_mode(mode, reason=reason or "auth_unlock")
        self._auth_log(
            reason=reason or "auth_unlock",
            action="unlock",
            result="success",
        )

    def auth_status_snapshot(self) -> dict[str, Any]:
        locked = self._is_auth_locked()
        if not locked and self._auth_mode == "locked":
            self._set_auth_mode("degraded", reason="auth_lock_expired")
        return {
            "mode": self._auth_mode,
            "locked": locked,
            "locked_until_ts": int(self._auth_locked_until_ts * 1000)
            if self._auth_locked_until_ts
            else None,
            "lock_reason": self._auth_lock_reason,
            "relogin_fail_streak": self._relogin_fail_streak,
            "next_relogin_allowed_ts": int(self._next_relogin_allowed_ts * 1000)
            if self._next_relogin_allowed_ts
            else None,
        }

    @property
    def _hf_min_interval_sec(self) -> int:
        return max(1, int(getattr(self.config, "mina_high_freq_min_interval_seconds", 8)))

    @property
    def _hf_auth_fail_threshold(self) -> int:
        return max(1, int(getattr(self.config, "mina_auth_fail_threshold", 3)))

    @property
    def _hf_auth_cooldown_sec(self) -> int:
        return max(60, int(getattr(self.config, "mina_auth_cooldown_seconds", 600)))

    @property
    def _refresh_interval_hours(self) -> float:
        return max(0.01, float(getattr(self.config, "oauth2_refresh_interval_hours", 6.0)))

    @property
    def _refresh_min_interval_sec(self) -> int:
        mins = max(1, int(getattr(self.config, "oauth2_refresh_min_interval_minutes", 30)))
        return mins * 60

    def is_auth_error(self, exc=None, resp=None, body=None) -> bool:
        return is_auth_error(exc=exc, resp=resp, body=body)

    def is_auth_locked(self) -> bool:
        return self._is_auth_locked()

    async def init_all_data(self):
        """初始化所有数据

        检查登录状态，如需要则登录，然后更新设备ID和Cookie。

        """
        self.mi_token_home = os.path.join(self.config.conf_path, ".mi.token")
        self.oauth2_token_path = self.config.oauth2_token_path

        # 先注入 OAuth2 cookie，避免后续健康检查触发不必要的账号登录流程
        cookie_jar = self.get_cookie()
        if cookie_jar:
            self.mi_session.cookie_jar.update_cookies(cookie_jar)

        is_need_login = await self.need_login()
        is_can_login = await self.can_login()
        if is_need_login and is_can_login:
            now = int(time.time())
            if now - self._last_login_ts >= self._login_cooldown_sec:
                self.log.info("try login")
                self._last_login_ts = now
                await self.login_miboy()
            else:
                self.log.info("skip login due cooldown")
        else:
            self.log.info(
                f"Maybe already logined is_need_login:{is_need_login} is_can_login:{is_can_login}"
            )
        await self.device_manager.update_device_info(self)
        cookie_jar = self.get_cookie()
        if cookie_jar:
            self.mi_session.cookie_jar.update_cookies(cookie_jar)
        self.cookie_jar = self.mi_session.cookie_jar

    async def can_login(self):
        if self._get_oauth2_auth_data():
            return True
        self.log.warning("没有 OAuth2 Token，无法登录")
        return False

    async def need_login(self):
        """检查是否需要登录

        Returns:
            bool: True表示需要登录，False表示已登录
        """
        if self.mina_service is None:
            return True
        if self.login_signature != self._get_login_signature():
            return True
        return False

    def _refresh_failed_requires_relogin(self, err: Exception | str) -> bool:
        text = str(err).lower()
        return any(word in text for word in AUTH_REFRESH_RELOGIN_KEYWORDS)

    def _is_high_freq_request(self, method_name: str, ctx: str) -> bool:
        if method_name == "get_latest_ask":
            return True
        if method_name != "device_list":
            return False
        c = (ctx or "").lower()
        # Only poll-like paths should be throttled/circuit-broken.
        return ("keepalive" in c) or ("poll" in c)

    def _should_skip_high_freq(self, method_name: str) -> tuple[bool, str]:
        now = time.time()
        cooldown_until = self._hf_cooldown_until_ts.get(method_name, 0.0)
        if now < cooldown_until:
            return True, "circuit_open"
        last_ts = self._hf_last_call_ts.get(method_name, 0.0)
        if now - last_ts < self._hf_min_interval_sec:
            return True, "rate_limited"
        self._hf_last_call_ts[method_name] = now
        return False, ""

    def _record_high_freq_auth_failure(self, method_name: str) -> None:
        streak = self._hf_auth_fail_streak.get(method_name, 0) + 1
        self._hf_auth_fail_streak[method_name] = streak
        if streak >= self._hf_auth_fail_threshold:
            cooldown_until = time.time() + self._hf_auth_cooldown_sec
            self._hf_cooldown_until_ts[method_name] = cooldown_until
            self._auth_log(
                reason="auth_error",
                action="circuit_open",
                result="fail",
                err=f"method={method_name} streak={streak} cooldown={self._hf_auth_cooldown_sec}s",
            )

    def _record_high_freq_success(self, method_name: str) -> None:
        self._hf_auth_fail_streak[method_name] = 0
        self._hf_cooldown_until_ts[method_name] = 0.0

    def _degraded_high_freq_result(self, method_name: str):
        if method_name == "device_list":
            return []
        if method_name == "get_latest_ask":
            return []
        return []

    def _token_save_ts(self) -> float:
        try:
            data = self._get_oauth2_auth_data()
            save_ms = int(data.get("saveTime") or 0)
            if save_ms > 0:
                return save_ms / 1000.0
        except Exception:
            return 0.0
        return 0.0

    @staticmethod
    def _token_fp(data: dict[str, Any]) -> str:
        if not isinstance(data, dict):
            return "none"
        uid = str(data.get("userId") or "")
        pt = str(data.get("passToken") or "")
        st = str(data.get("yetAnotherServiceToken") or data.get("serviceToken") or "")
        save = str(data.get("saveTime") or "")
        uid_tail = uid[-6:] if uid else ""
        return f"uid={uid_tail}|pt={len(pt)}|st={len(st)}|save={save}"

    async def _verify_runtime_auth_ready(self) -> bool:
        try:
            if self.mina_service is None:
                return False
            await self.mina_service.device_list()
            self._last_ok_ts = time.time()
            return True
        except Exception:
            return False

    async def rebuild_services(self, reason: str, allow_login_fallback: bool = False) -> bool:
        self.mark_session_invalid(reason or "rebuild")
        await self.login_miboy(
            allow_login_fallback=allow_login_fallback,
            reason=reason,
        )
        ready = await self._verify_runtime_auth_ready()
        self._auth_log(
            reason=reason,
            action="rebuild",
            result="success" if ready else "fail",
            err="",
        )
        return ready

    async def refresh_oauth2_token_if_needed(self, reason: str, force: bool = False) -> dict[str, Any]:
        now = time.time()
        if not force and self._last_refresh_ts and (now - self._last_refresh_ts) < self._refresh_min_interval_sec:
            return {
                "refreshed": False,
                "token_saved": False,
                "last_error": "refresh skipped by min interval",
                "fallback_allowed": False,
            }

        from xiaomusic.qrcode_login import MiJiaAPI

        token_path = self.oauth2_token_path
        auth_dir = os.path.dirname(token_path) if token_path else None
        before = self._get_oauth2_auth_data()
        before_save_ms = int(before.get("saveTime") or 0)
        before_pass = str(before.get("passToken") or "")
        try:
            api = MiJiaAPI(auth_data_path=auth_dir, token_store=self.token_store)
            if not hasattr(api, "_refresh_token"):
                raise RuntimeError("refresh api unavailable")
            await asyncio.to_thread(api._refresh_token, force)

            # Ensure persisted token snapshot is updated and visible to runtime.
            if self.token_store is not None:
                self.token_store.reload_from_disk()
            after = self._get_oauth2_auth_data()
            after_save_ms = int(after.get("saveTime") or 0)
            after_pass = str(after.get("passToken") or "")
            token_saved = bool(after.get("userId") and after.get("passToken") and after_save_ms >= before_save_ms)
            refresh_rotated = bool(before_pass and after_pass and before_pass != after_pass)

            self._last_refresh_ts = time.time()
            self._last_refresh_error = ""
            self._auth_log(
                reason=reason,
                action="refresh",
                result="success",
                err=(
                    f"saved={token_saved} rotated={refresh_rotated} "
                    f"before={self._token_fp(before)} after={self._token_fp(after)}"
                ),
            )
            return {
                "refreshed": True,
                "token_saved": token_saved,
                "last_error": None,
                "fallback_allowed": False,
            }
        except Exception as e:
            self._last_refresh_error = str(e)
            fallback_allowed = self._refresh_failed_requires_relogin(e)
            self._auth_log(
                reason=reason,
                action="refresh",
                result="fail",
                err=f"{type(e).__name__}:{e}",
            )
            return {
                "refreshed": False,
                "token_saved": False,
                "last_error": str(e),
                "fallback_allowed": fallback_allowed,
            }

    async def manual_refresh(self, reason: str = "manual_refresh") -> dict[str, Any]:
        async with self._relogin_lock:
            ref = await self.refresh_oauth2_token_if_needed(reason=reason, force=True)
            runtime_ready = False
            if ref.get("refreshed"):
                runtime_ready = await self.rebuild_services(
                    reason=reason,
                    allow_login_fallback=False,
                )
            return {
                "refreshed": bool(ref.get("refreshed")),
                "runtime_auth_ready": bool(runtime_ready),
                "token_saved": bool(ref.get("token_saved")),
                "last_error": ref.get("last_error"),
                "timestamps": {
                    "saveTime": int(self._token_save_ts() * 1000) if self._token_save_ts() else None,
                    "last_ok_ts": int(self._last_ok_ts * 1000) if self._last_ok_ts else None,
                    "last_refresh_ts": int(self._last_refresh_ts * 1000) if self._last_refresh_ts else None,
                },
            }

    async def _maybe_scheduled_refresh(self) -> None:
        now = time.time()
        if self._last_refresh_ts and (now - self._last_refresh_ts) < self._refresh_min_interval_sec:
            return
        save_ts = self._token_save_ts()
        if not save_ts:
            return
        if now - save_ts < self._refresh_interval_hours * 3600:
            return
        async with self._relogin_lock:
            ref = await self.refresh_oauth2_token_if_needed(
                reason="scheduled_refresh",
                force=False,
            )
            if ref.get("refreshed"):
                await self.rebuild_services(
                    reason="scheduled_refresh",
                    allow_login_fallback=False,
                )

    async def ensure_logged_in(self, force=False, reason="", prefer_refresh=False):
        async with self._relogin_lock:
            now = time.time()
            self._relogin_inflight_ts = now
            if reason:
                self._last_relogin_reason = reason

            if self._is_auth_locked():
                wait_sec = int(self._auth_locked_until_ts - now)
                raise RuntimeError(f"auth locked, manual relogin required ({wait_sec}s)")

            need = await self.need_login()
            if force and not need and self._last_ok_ts and (now - self._last_ok_ts < 30):
                return False

            if not force and not need:
                return False

            if now < self._next_relogin_allowed_ts:
                wait_sec = int(self._next_relogin_allowed_ts - now)
                raise RuntimeError(f"relogin backoff active {wait_sec}s")

            if reason:
                self.log.warning(f"ensure_logged_in start reason={reason} force={force}")

            try:
                if prefer_refresh:
                    refreshed = await self.refresh_oauth2_token_if_needed(
                        reason=reason or "auth_error",
                        force=True,
                    )
                    if refreshed.get("refreshed"):
                        ready = await self.rebuild_services(
                            reason=reason or "auth_error",
                            allow_login_fallback=False,
                        )
                        if not ready:
                            raise RuntimeError("rebuild after refresh failed")
                    elif refreshed.get("fallback_allowed"):
                        ready = await self.rebuild_services(
                            reason=reason or "login_fallback",
                            allow_login_fallback=True,
                        )
                        if not ready:
                            raise RuntimeError("fallback login rebuild failed")
                    else:
                        raise RuntimeError(refreshed.get("last_error") or "refresh failed")
                else:
                    self.mark_session_invalid(reason or "ensure_logged_in")
                    await self.init_all_data()
                    if await self.need_login():
                        raise RuntimeError("relogin completed but service still unavailable")
                self._last_ok_ts = time.time()
                self._relogin_fail_streak = 0
                self._next_relogin_allowed_ts = 0.0
                self._auth_locked_until_ts = 0.0
                self._auth_lock_reason = ""
                self._set_auth_mode("healthy", reason=reason or "ensure_logged_in")
                self.log.info("ensure_logged_in success")
                return True
            except Exception as e:
                self._relogin_fail_streak += 1
                delay = min(30 * (2 ** max(self._relogin_fail_streak - 1, 0)), 300)
                self._next_relogin_allowed_ts = time.time() + delay
                self._set_auth_mode("degraded", reason=reason or "ensure_logged_in")
                err_text = str(e).lower()
                if (
                    self._refresh_failed_requires_relogin(e)
                    or "runtime verify after login failed" in err_text
                    or "login failed" in err_text
                ):
                    self._enter_auth_lock(reason=reason or str(e))
                self.log.warning(
                    "ensure_logged_in failed. streak=%d next_retry_after=%ss",
                    self._relogin_fail_streak,
                    delay,
                )
                raise

    async def auth_call(self, fn, *, retry=1, ctx=""):
        if self._is_auth_locked():
            raise RuntimeError("auth locked, manual relogin required")
        try:
            return await fn()
        except Exception as e:
            if is_network_error(exc=e):
                self._auth_log(
                    reason=ctx or "unknown",
                    action="network_backoff",
                    result="fail",
                    err=f"{type(e).__name__}:{e}",
                )
                raise

            if not is_auth_error_strict(exc=e):
                raise

            self._auth_log(
                reason=ctx or "auth_error",
                action="auth_error_detected",
                result="fail",
                err=f"{type(e).__name__}:{e}",
            )
            await self.ensure_logged_in(
                force=True,
                reason=ctx or str(e),
                prefer_refresh=True,
            )
            if retry <= 0:
                raise
            return await fn()

    async def mina_call(self, method_name: str, *args, retry=1, ctx="", **kwargs):
        if self._is_high_freq_request(method_name, ctx):
            skip, skip_reason = self._should_skip_high_freq(method_name)
            if skip:
                self._auth_log(
                    reason=f"mina:{method_name}:{ctx}",
                    action=skip_reason,
                    result="degraded",
                )
                return self._degraded_high_freq_result(method_name)

        async def _call():
            if self.mina_service is None:
                raise RuntimeError("mina service unavailable")
            method = getattr(self.mina_service, method_name)
            return await method(*args, **kwargs)

        try:
            ret = await self.auth_call(_call, retry=retry, ctx=f"mina:{method_name}:{ctx}")
            if self._is_high_freq_request(method_name, ctx):
                self._record_high_freq_success(method_name)
            return ret
        except Exception as e:
            if self._is_high_freq_request(method_name, ctx) and is_auth_error_strict(exc=e):
                self._record_high_freq_auth_failure(method_name)
                return self._degraded_high_freq_result(method_name)
            raise

    async def miio_call(self, fn, *, retry=1, ctx=""):
        return await self.auth_call(fn, retry=retry, ctx=f"miio:{ctx}")

    async def keepalive_loop(self, interval_sec=300):
        while True:
            try:
                await self._maybe_scheduled_refresh()
                await self.ensure_logged_in(force=False, reason="keepalive")
                await self.mina_call("device_list", retry=1, ctx="keepalive")
                self._last_ok_ts = time.time()
                self._keepalive_fail_streak = 0
                if self._keepalive_degraded:
                    self.log.info("auth keepalive recovered from degraded state")
                    self._keepalive_degraded = False
                await asyncio.sleep(interval_sec)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._keepalive_fail_streak += 1
                self.log.warning(
                    "auth keepalive failed streak=%d reason=%s",
                    self._keepalive_fail_streak,
                    e,
                )

                # Auto-heal without waiting for WebUI getalldevices trigger.
                # After repeated keepalive failures, proactively run the same
                # relogin/rebuild path used by getalldevices.
                if self._keepalive_fail_streak >= 2:
                    try:
                        await self.ensure_logged_in(
                            force=True,
                            reason="keepalive_auto_recover",
                            prefer_refresh=True,
                        )
                        await self.mina_call("device_list", retry=0, ctx="keepalive-auto-recover")
                        self._keepalive_fail_streak = 0
                        if self._keepalive_degraded:
                            self.log.info("auth keepalive recovered from degraded state")
                            self._keepalive_degraded = False
                        self.log.info("auth keepalive auto-recover success")
                        await asyncio.sleep(interval_sec)
                        continue
                    except Exception as recover_err:
                        self.log.warning("auth keepalive auto-recover failed: %s", recover_err)

                if self._keepalive_fail_streak < 3:
                    delay = min(30 * (2 ** (self._keepalive_fail_streak - 1)), 120)
                    await asyncio.sleep(delay)
                    continue

                if not self._keepalive_degraded:
                    self.log.warning("auth keepalive enter degraded mode")
                    self._keepalive_degraded = True
                await asyncio.sleep(max(interval_sec, 300))

    def mark_session_invalid(self, reason=""):
        """标记当前会话失效，触发下次 init_all_data 强制登录。"""
        if reason:
            self.log.warning(f"mark_session_invalid: {reason}")
        self.mina_service = None
        self.miio_service = None
        self.login_signature = None
        self.cookie_jar = None
        # 立即允许下一次 init_all_data 触发登录
        self._last_login_ts = 0
        self._last_relogin_reason = reason or self._last_relogin_reason

    def _get_login_signature(self):
        oauth2_mtime = None
        token_path = self.oauth2_token_path
        if self.token_store is not None:
            token_path = str(getattr(self.token_store, "path", token_path))
        if token_path and os.path.isfile(token_path):
            oauth2_mtime = int(os.path.getmtime(token_path))
        return (oauth2_mtime, self.config.mi_did)

    async def login_miboy(self, allow_login_fallback: bool = True, reason: str = ""):
        """登录小米账号

        使用 OAuth2 token 登录小米账号，并初始化相关服务。
        """
        try:
            auth_data = self._get_oauth2_auth_data()
            account_name = auth_data.get("userId", "")
            if not account_name:
                self.log.warning("OAuth2 token 文件缺少 userId，无法登录")
                return
            mi_account = MiAccount(
                self.mi_session,
                account_name,
                "",
                str(self.mi_token_home),
            )
            self.set_token(mi_account)

            # If OAuth2 token file already contains micoapi serviceToken, inject it into MiAccount
            # to avoid triggering MiAccount.login() (which may require captcha).
            oauth_service_token = auth_data.get("yetAnotherServiceToken") or auth_data.get(
                "serviceToken"
            )
            oauth_ssecurity = auth_data.get("ssecurity")
            if oauth_service_token and oauth_ssecurity:
                try:
                    token_data = getattr(mi_account, "token", None)
                    if isinstance(token_data, dict):
                        token_data["micoapi"] = (oauth_ssecurity, oauth_service_token)
                except Exception:
                    # keep fallback login path
                    pass

            # OAuth2 扫码场景优先使用 serviceToken，避免触发账号二次风控验证。
            # 但 serviceToken 可能已失效：先走免登录路径，失败后回退到显式 login。
            has_service_token = bool(oauth_service_token and oauth_ssecurity)

            self.mina_service = MiNAService(mi_account)
            self.miio_service = MiIOService(mi_account)

            runtime_verified = False
            if has_service_token:
                try:
                    await self.mina_service.device_list()
                    runtime_verified = True
                except Exception as verify_err:
                    self._auth_log(
                        reason=reason or "login_verify",
                        action="verify_service_token",
                        result="fail",
                        err=f"{type(verify_err).__name__}:{verify_err}",
                    )
                    if not allow_login_fallback:
                        raise RuntimeError("service token verify failed and login fallback disabled")
                    self._auth_log(
                        reason=reason or "auth_error",
                        action="login_fallback",
                        result="start",
                    )
                    await mi_account.login("micoapi")
                    self.mina_service = MiNAService(mi_account)
                    self.miio_service = MiIOService(mi_account)
            else:
                if not allow_login_fallback:
                    raise RuntimeError("missing service token and login fallback disabled")
                self._auth_log(
                    reason=reason or "auth_error",
                    action="login_fallback",
                    result="start",
                )
                await mi_account.login("micoapi")
                self.mina_service = MiNAService(mi_account)
                self.miio_service = MiIOService(mi_account)

            if not runtime_verified:
                try:
                    await self.mina_service.device_list()
                    self._auth_log(
                        reason=reason or "login_verify",
                        action="verify_runtime_after_login",
                        result="success",
                    )
                except Exception as verify_err:
                    self._auth_log(
                        reason=reason or "login_verify",
                        action="verify_runtime_after_login",
                        result="fail",
                        err=f"{type(verify_err).__name__}:{verify_err}",
                    )
                    raise RuntimeError(f"runtime verify after login failed: {verify_err}")

            self.login_acount = account_name
            self._persist_oauth2_token(auth_data=auth_data, mi_account=mi_account, reason="login")
            # token 落盘后再计算签名，避免 mtime 变化导致运行时一直被判定为 need_login。
            self.login_signature = self._get_login_signature()
            # Clear relogin backoff immediately after a successful login/reinit.
            self._relogin_fail_streak = 0
            self._next_relogin_allowed_ts = 0.0
            self._last_ok_ts = time.time()
            self.log.info(f"登录完成. {self.login_acount}")
            self._auth_log(
                reason=reason or "login",
                action="login",
                result="success",
            )
        except Exception as e:
            self.mina_service = None
            self.miio_service = None
            self.log.warning(f"可能登录失败. {e}")
            self._auth_log(
                reason=reason or "login",
                action="login",
                result="fail",
                err=f"{type(e).__name__}:{e}",
            )

    async def try_update_device_id(self):
        """更新设备ID

        从小米服务获取设备列表，更新配置中的设备信息。

        Returns:
            dict: 更新后的设备字典 {did: Device}
        """
        try:
            if self.mina_service is None:
                self.log.warning("mina_service is None, skip try_update_device_id")
                return {}
            mi_dids = self.config.mi_did.split(",")
            hardware_data = await self.mina_call(
                "device_list",
                ctx="try_update_device_id",
            )
            devices = {}
            for h in hardware_data:
                device_id = h.get("deviceID", "")
                hardware = h.get("hardware", "")
                did = h.get("miotDID", "")
                name = h.get("alias", "")
                if not name:
                    name = h.get("name", "未知名字")
                if device_id and hardware and did and (did in mi_dids):
                    device = self.config.devices.get(did, Device())
                    device.did = did
                    # 将did存一下 方便其他地方调用
                    self._cur_did = did
                    device.device_id = device_id
                    device.hardware = hardware
                    device.name = name
                    devices[did] = device
            self.config.devices = devices
            self.log.info(f"选中的设备: {devices}")
            return devices
        except Exception as e:
            if "Login failed" in str(e):
                self.mark_session_invalid(str(e))
            self.log.warning(f"可能登录失败. {e}")
            return {}

    def set_token(self, account):
        """
        设置token到account
        """
        user_data = self._get_oauth2_auth_data()
        if user_data:
            self.device_id = user_data.get("deviceId", self.device_id)
            account.token = {
                "passToken": user_data["passToken"],
                "userId": user_data["userId"],
                "deviceId": self.device_id,
            }
        else:
            return

    def _persist_oauth2_token(self, auth_data: dict, mi_account, reason: str = "") -> None:
        if self.token_store is None:
            return
        merged = deepcopy(auth_data or {})
        merged["saveTime"] = int(time.time() * 1000)
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
        except Exception as e:
            self.log.warning("persist token merge failed: %s", e)

        self.token_store.update(merged, reason=reason or "login")
        self.token_store.flush()

    def _get_oauth2_auth_data(self):
        if self.token_store is not None:
            user_data = self.token_store.get()
        else:
            if not os.path.isfile(self.oauth2_token_path):
                return {}
            with open(self.oauth2_token_path, encoding="utf-8") as f:
                user_data = json.loads(f.read())
        required_fields = {"passToken", "userId"}
        if not required_fields.issubset(user_data):
            self.log.warning(
                f"OAuth2 token 文件缺少字段: {required_fields - set(user_data.keys())}"
            )
            return {}
        return user_data

    def get_cookie(self):
        """获取Cookie

        从配置或token文件中获取Cookie。

        Returns:
            CookieJar: Cookie容器，失败返回None
        """
        auth_data = self._get_oauth2_auth_data()
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
            cookie_string += f"; cUserId={c_user_id}; yetAnotherServiceToken={service_token}"
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
        auth_data = self._get_oauth2_auth_data()
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
            cookie_string += f"; cUserId={c_user_id}; yetAnotherServiceToken={service_token}"
            return parse_cookie_string_to_dict(cookie_string)
        return {}
