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
import hashlib
import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

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

SHORT_SESSION_KEYS = ("serviceToken", "yetAnotherServiceToken")

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
        self._auth_login_at_ts = 0.0
        self._auth_expires_at_ts = 0.0
        self._auth_estimated_ttl = True
        self._last_refresh_trigger = ""
        self._last_auth_error = ""
        self._auth_recovery_chain_id = ""
        self._auth_recovery_active_until_ts = 0.0
        self._auth_recovery_state: dict[str, dict[str, Any]] = {
            "last_clear_short_session": {},
            "last_login_exchange": {},
            "last_runtime_rebind": {},
            "last_playback_capability_verify": {},
        }
        self._mi_login_trace_state: dict[str, dict[str, Any]] = {
            "login_input_snapshot": {},
            "login_http_exchange": {},
            "login_response_parse": {},
            "token_writeback": {},
            "post_login_runtime_seed": {},
        }
        self._auth_rebuild_state: dict[str, dict[str, Any]] = {
            "last_clear_short_session": {},
            "last_rebuild_short_session": {},
            "last_runtime_rebind": {},
            "last_verify": {},
        }
        self._oauth_runtime_reload_state: dict[str, dict[str, Any]] = {
            "last_reload_runtime": {},
        }

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

    @staticmethod
    def _iso_utc(ts: float | int | None) -> str | None:
        if not ts:
            return None
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _parse_num(raw: Any) -> float | None:
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _refresh_trigger_from_reason(reason: str) -> str:
        text = str(reason or "").lower()
        if "scheduled" in text:
            return "scheduled"
        if "manual" in text:
            return "manual"
        if "keepalive" in text or "recover" in text:
            return "keepalive_recover"
        return "force_relogin"

    @staticmethod
    def _classify_auth_result(err: Any = None) -> str:
        text = str(err or "").lower()
        if "70016" in text:
            return "70016"
        if "401" in text or "unauthorized" in text:
            return "401"
        if "locked" in text:
            return "locked"
        if any(x in text for x in NETWORK_ERROR_KEYWORDS):
            return "network_error"
        if "refresh" in text and "fail" in text:
            return "refresh_failed"
        return "refresh_failed"

    def _auth_session_id(self) -> str:
        data = self._get_oauth2_auth_data()
        seed = str(
            data.get("passToken")
            or data.get("yetAnotherServiceToken")
            or data.get("serviceToken")
            or data.get("userId")
            or ""
        )
        if not seed:
            return "none"
        return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8]

    def _sync_auth_ttl(self, auth_data: dict[str, Any] | None = None, login_at_ts: float | None = None) -> None:
        data = auth_data if isinstance(auth_data, dict) else self._get_oauth2_auth_data()
        save_ms = int((data or {}).get("saveTime") or 0)
        if login_at_ts is not None:
            base_login = float(login_at_ts)
        elif self._auth_login_at_ts > 0:
            base_login = float(self._auth_login_at_ts)
        elif save_ms > 0:
            base_login = save_ms / 1000.0
        else:
            base_login = time.time()
        self._auth_login_at_ts = base_login

        expires_at_ts: float | None = None
        estimated_ttl = True
        expires_in = self._parse_num((data or {}).get("expires_in"))
        if expires_in and expires_in > 0:
            expires_at_ts = base_login + float(expires_in)
            estimated_ttl = False
        if expires_at_ts is None:
            for key in ("expire", "expiry"):
                raw = self._parse_num((data or {}).get(key))
                if raw is None or raw <= 0:
                    continue
                if raw > 1_000_000_000_000:
                    expires_at_ts = raw / 1000.0
                    estimated_ttl = False
                elif raw > 1_000_000_000:
                    expires_at_ts = raw
                    estimated_ttl = False
                else:
                    expires_at_ts = base_login + raw
                    estimated_ttl = False
                break

        if expires_at_ts is None:
            expires_at_ts = base_login + 86400
            estimated_ttl = True

        self._auth_expires_at_ts = float(expires_at_ts)
        self._auth_estimated_ttl = bool(estimated_ttl)

    def _ttl_remaining_seconds(self) -> int | None:
        if self._auth_expires_at_ts <= 0:
            return None
        return int(self._auth_expires_at_ts - time.time())

    def _emit_auth_state(
        self,
        *,
        auth_step: str,
        auth_result: str,
        refresh_trigger: str | None = None,
        auth_mode_before: str | None = None,
        auth_mode_after: str | None = None,
        clear_reason: str | None = None,
        err: str = "",
    ) -> None:
        if err:
            self._last_auth_error = str(err)[:300]
        event = {
            "event": "auth_state",
            "auth_session_id": self._auth_session_id(),
            "login_at": self._iso_utc(self._auth_login_at_ts),
            "expires_at": self._iso_utc(self._auth_expires_at_ts),
            "ttl_remaining_seconds": self._ttl_remaining_seconds(),
            "estimated_ttl": bool(self._auth_estimated_ttl),
            "refresh_trigger": refresh_trigger or self._last_refresh_trigger or None,
            "auth_step": auth_step,
            "auth_result": auth_result,
            "auth_mode_before": auth_mode_before or self._auth_mode,
            "auth_mode_after": auth_mode_after or self._auth_mode,
        }
        if clear_reason:
            event["clear_reason"] = clear_reason
        self.log.info(json.dumps(event, ensure_ascii=False, separators=(",", ":")))

    @staticmethod
    def _short_reason(reason: str) -> str:
        text = str(reason or "").lower()
        if "70016" in text:
            return "70016"
        if "401" in text or "unauthorized" in text:
            return "401"
        if "runtime" in text and "verify" in text:
            return "runtime_verify_failed"
        if "refresh" in text and "fail" in text:
            return "refresh_failed"
        return "other"

    def _auth_presence_snapshot(self, data: dict[str, Any] | None = None) -> dict[str, bool]:
        payload = data if isinstance(data, dict) else self._get_oauth2_auth_data()
        return {
            "has_passToken": bool(payload.get("passToken")),
            "has_serviceToken": bool(payload.get("serviceToken")),
            "has_yetAnotherServiceToken": bool(payload.get("yetAnotherServiceToken")),
            "has_ssecurity": bool(payload.get("ssecurity")),
            "has_deviceId": bool(payload.get("deviceId")),
        }

    def _short_session_fingerprint(self, data: dict[str, Any] | None = None) -> str:
        payload = data if isinstance(data, dict) else self._get_oauth2_auth_data()
        st = str(payload.get("yetAnotherServiceToken") or payload.get("serviceToken") or "")
        ss = str(payload.get("ssecurity") or "")
        if not st and not ss:
            return "none"
        return hashlib.sha1(f"{st}|{ss}".encode("utf-8")).hexdigest()[:8]

    def _recovery_is_active(self) -> bool:
        return time.time() <= float(self._auth_recovery_active_until_ts or 0)

    def _mark_recovery_active(self, reason: str) -> str:
        self._auth_recovery_chain_id = str(uuid4().hex[:12])
        self._auth_recovery_active_until_ts = time.time() + 900
        return self._auth_recovery_chain_id

    def _emit_recovery_stage(
        self,
        *,
        stage: str,
        result: str,
        reason: str = "",
        auth_mode_before: str | None = None,
        auth_mode_after: str | None = None,
        error_code: str = "",
        error_message: str = "",
        extra: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "event": "auth_recovery_stage",
            "stage": stage,
            "result": result,
            "auth_session_id": self._auth_session_id(),
            "auth_mode_before": auth_mode_before or self._auth_mode,
            "auth_mode_after": auth_mode_after or self._auth_mode,
            "reason": reason or "",
            "recovery_chain_id": self._auth_recovery_chain_id or "",
        }
        if error_code:
            payload["error_code"] = str(error_code)
        if error_message:
            payload["error_message"] = str(error_message).replace("\n", " ")[:200]
        if extra:
            payload.update(extra)
        self.log.info(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))

        key = {
            "clear_short_session": "last_clear_short_session",
            "login_exchange": "last_login_exchange",
            "runtime_rebind": "last_runtime_rebind",
            "playback_capability_verify": "last_playback_capability_verify",
        }.get(stage)
        if key:
            snapshot = dict(payload)
            self._auth_recovery_state[key] = snapshot

    def auth_recovery_debug_state(self) -> dict[str, Any]:
        return deepcopy(self._auth_recovery_state)

    def miaccount_login_trace_debug_state(self) -> dict[str, Any]:
        return deepcopy(self._mi_login_trace_state)

    def auth_rebuild_debug_state(self) -> dict[str, Any]:
        return deepcopy(self._auth_rebuild_state)

    def oauth_runtime_reload_debug_state(self) -> dict[str, Any]:
        return deepcopy(self._oauth_runtime_reload_state)

    def _emit_auth_short_session_rebuild(
        self,
        *,
        result: str,
        reason: str,
        auth_mode_before: str,
        auth_mode_after: str,
        has_pass_token: bool,
        has_service_before: bool,
        has_service_after: bool,
        has_yast_before: bool,
        has_yast_after: bool,
        writeback_target: str,
        runtime_rebind_result: str,
    ) -> None:
        payload = {
            "event": "auth_short_session_rebuild",
            "stage": "rebuild_short_session",
            "result": result,
            "auth_session_id": self._auth_session_id(),
            "auth_mode_before": auth_mode_before,
            "auth_mode_after": auth_mode_after,
            "reason": reason,
            "has_passToken": has_pass_token,
            "has_serviceToken_before": has_service_before,
            "has_serviceToken_after": has_service_after,
            "has_yetAnotherServiceToken_before": has_yast_before,
            "has_yetAnotherServiceToken_after": has_yast_after,
            "writeback_target": writeback_target,
            "runtime_rebind_result": runtime_rebind_result,
        }
        self.log.info(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        self._auth_rebuild_state["last_rebuild_short_session"] = dict(payload)

    @staticmethod
    def _missing_long_auth_fields(data: dict[str, Any]) -> list[str]:
        required = ("passToken", "userId", "cUserId", "ssecurity", "deviceId")
        return [k for k in required if not str(data.get(k) or "").strip()]

    @classmethod
    def _has_long_auth_fields(cls, data: dict[str, Any]) -> bool:
        return len(cls._missing_long_auth_fields(data)) == 0

    @staticmethod
    def _missing_runtime_reload_fields(data: dict[str, Any]) -> list[str]:
        required = ("passToken", "psecurity", "ssecurity", "userId", "cUserId", "deviceId")
        missing = [k for k in required if not str(data.get(k) or "").strip()]
        if not (str(data.get("serviceToken") or "").strip() or str(data.get("yetAnotherServiceToken") or "").strip()):
            missing.append("serviceToken|yetAnotherServiceToken")
        return missing

    def _emit_oauth_runtime_reload(
        self,
        *,
        result: str,
        reason: str,
        token_store_reloaded: bool,
        disk_has_service_token: bool,
        disk_has_yast: bool,
        runtime_seed_has_service_token: bool,
        mina_service_rebuilt: bool,
        miio_service_rebuilt: bool,
        device_map_refreshed: bool,
        verify_result: str,
        error_code: str = "",
        error_message: str = "",
        refresh_token_path_invoked: bool = False,
    ) -> None:
        payload = {
            "event": "oauth_runtime_reload",
            "stage": "reload_runtime",
            "result": result,
            "reason": reason,
            "auth_session_id": self._auth_session_id(),
            "token_store_reloaded": bool(token_store_reloaded),
            "disk_has_serviceToken": bool(disk_has_service_token),
            "disk_has_yetAnotherServiceToken": bool(disk_has_yast),
            "runtime_seed_has_serviceToken": bool(runtime_seed_has_service_token),
            "mina_service_rebuilt": bool(mina_service_rebuilt),
            "miio_service_rebuilt": bool(miio_service_rebuilt),
            "device_map_refreshed": bool(device_map_refreshed),
            "verify_result": verify_result,
            "error_code": error_code,
            "error_message": (error_message or "")[:200],
            "refresh_token_path_invoked": bool(refresh_token_path_invoked),
        }
        self.log.info(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        self._oauth_runtime_reload_state["last_reload_runtime"] = dict(payload)

    async def _rebuild_short_session_from_long_auth(self, reason: str) -> bool:
        mode_before = self._auth_mode
        before = self._get_oauth2_auth_data()
        has_service_before = bool(before.get("serviceToken"))
        has_yast_before = bool(before.get("yetAnotherServiceToken"))
        has_pass = bool(before.get("passToken"))
        missing_long_auth = self._missing_long_auth_fields(before)
        if missing_long_auth:
            self._emit_auth_short_session_rebuild(
                result="failed",
                reason=f"long_auth_missing:{','.join(missing_long_auth)}",
                auth_mode_before=mode_before,
                auth_mode_after=self._auth_mode,
                has_pass_token=has_pass,
                has_service_before=has_service_before,
                has_service_after=False,
                has_yast_before=has_yast_before,
                has_yast_after=False,
                writeback_target="none",
                runtime_rebind_result="skipped",
            )
            return False
        refreshed = await self.refresh_oauth2_token_if_needed(
            reason=f"short_session_rebuild:{reason or 'auth_error'}",
            force=True,
        )
        after = self._get_oauth2_auth_data()
        has_service_after = bool(after.get("serviceToken"))
        has_yast_after = bool(after.get("yetAnotherServiceToken"))
        ok = bool(refreshed.get("refreshed") and (has_service_after or has_yast_after))
        self._emit_auth_short_session_rebuild(
            result="ok" if ok else "failed",
            reason=reason or "auth_error",
            auth_mode_before=mode_before,
            auth_mode_after=self._auth_mode,
            has_pass_token=has_pass,
            has_service_before=has_service_before,
            has_service_after=has_service_after,
            has_yast_before=has_yast_before,
            has_yast_after=has_yast_after,
            writeback_target="auth_json" if (has_service_after or has_yast_after) else "none",
            runtime_rebind_result="pending" if ok else "skipped",
        )
        return ok

    @staticmethod
    def _masked_len(value: Any) -> int:
        return len(str(value or ""))

    @staticmethod
    def _parse_login_error(err: Exception | str | None) -> dict[str, str]:
        text = str(err or "")
        code_match = re.search(r"code\s*[:=]\s*['\"]?(\d{3,6})", text, flags=re.IGNORECASE)
        desc_match = re.search(r"description\s*[:=]\s*['\"]([^'\"\n\r]+)", text, flags=re.IGNORECASE)
        status_match = re.search(r"status\s*(\d{3})", text, flags=re.IGNORECASE)
        url_match = re.search(r"https?://([^/\s]+)(/[^\s:]+)?", text)
        return {
            "login_http_status": status_match.group(1) if status_match else "",
            "resp_code": code_match.group(1) if code_match else "",
            "resp_desc": (desc_match.group(1) if desc_match else text[:120]).replace("\n", " "),
            "callback_host": url_match.group(1) if url_match else "",
            "request_path": url_match.group(2) if url_match and url_match.group(2) else "",
        }

    def _mi_login_input_snapshot(self, account: Any) -> dict[str, Any]:
        token = getattr(account, "token", None)
        top_keys = sorted(list(token.keys())) if isinstance(token, dict) else []
        service_token = ""
        yet_another = ""
        if isinstance(token, dict):
            service_token = str(token.get("serviceToken") or "")
            yet_another = str(token.get("yetAnotherServiceToken") or "")
            mico = token.get("micoapi")
            if isinstance(mico, (tuple, list)) and len(mico) >= 2:
                service_token = str(service_token or mico[1] or "")
                yet_another = str(yet_another or mico[1] or "")
        return {
            "has_passToken": bool(isinstance(token, dict) and token.get("passToken")),
            "has_psecurity": bool(isinstance(token, dict) and token.get("psecurity")),
            "has_ssecurity": bool(isinstance(token, dict) and token.get("ssecurity")),
            "has_userId": bool(isinstance(token, dict) and token.get("userId")),
            "has_cUserId": bool(isinstance(token, dict) and token.get("cUserId")),
            "has_deviceId": bool(isinstance(token, dict) and token.get("deviceId")),
            "has_serviceToken": bool(service_token),
            "has_yetAnotherServiceToken": bool(yet_another),
            "passToken_len": self._masked_len(token.get("passToken") if isinstance(token, dict) else ""),
            "serviceToken_len": self._masked_len(service_token),
            "yetAnotherServiceToken_len": self._masked_len(yet_another),
            "token_dict_is_none": token is None,
            "top_level_keys": top_keys,
        }

    @staticmethod
    def _micoapi_tokens(acct: dict[str, Any]) -> tuple[str, str]:
        mico = acct.get("micoapi")
        if isinstance(mico, (tuple, list)) and len(mico) >= 2:
            return str(mico[0] or ""), str(mico[1] or "")
        return "", ""

    def _emit_mi_login_trace(
        self,
        *,
        stage: str,
        result: str,
        sid: str = "micoapi",
        reason: str = "",
        auth_mode_before: str | None = None,
        auth_mode_after: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "event": "miaccount_login_trace",
            "stage": stage,
            "sid": sid,
            "result": result,
            "auth_session_id": self._auth_session_id(),
            "auth_mode_before": auth_mode_before or self._auth_mode,
            "auth_mode_after": auth_mode_after or self._auth_mode,
            "reason": reason or "",
        }
        if extra:
            payload.update(extra)
        self.log.info(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        self._mi_login_trace_state[stage] = dict(payload)

    def record_playback_capability_verify(
        self,
        *,
        result: str,
        verify_method: str,
        playback_capability_level: str,
        transport: str,
        error_code: str = "",
        error_message: str = "",
    ) -> None:
        if not self._recovery_is_active():
            return
        self._emit_recovery_stage(
            stage="playback_capability_verify",
            result=result,
            reason="post_recovery_playback_check",
            error_code=error_code,
            error_message=error_message,
            extra={
                "verify_method": verify_method,
                "playback_capability_level": playback_capability_level,
                "transport": transport or "unknown",
            },
        )

    @staticmethod
    def _is_short_session_failure_signal(reason: str = "", err: Exception | str | None = None) -> bool:
        text = f"{reason or ''} {err or ''}".lower()
        if "70016" in text:
            return True
        if "401" in text or "unauthorized" in text:
            return True
        if "runtime verify after login failed" in text:
            return True
        if "refresh failed" in text or "刷新token失败" in text:
            return True
        if "login failed" in text and "mina" in text:
            return True
        return False

    def _clear_short_lived_session(self, clear_reason: str, err: Exception | str | None = None) -> bool:
        mode_before = self._auth_mode
        summarized_reason = self._short_reason(f"{clear_reason} {err or ''}")
        if not self._is_short_session_failure_signal(clear_reason, err):
            self._emit_auth_state(
                auth_step="clear_short_session",
                auth_result="skipped",
                auth_mode_before=mode_before,
                auth_mode_after=self._auth_mode,
                clear_reason=clear_reason,
            )
            self._emit_recovery_stage(
                stage="clear_short_session",
                result="skipped",
                reason=summarized_reason,
                auth_mode_before=mode_before,
                auth_mode_after=self._auth_mode,
                extra={
                    "cleared_fields": [],
                    **self._auth_presence_snapshot(),
                    "auth_json_writeback": "no",
                },
            )
            return False

        self._mark_recovery_active(summarized_reason)
        removed_keys: list[str] = []
        wrote_back = False
        try:
            # Clear runtime in-memory short-lived injection first.
            for svc in (self.mina_service, self.miio_service):
                if svc is None:
                    continue
                account = getattr(svc, "account", None)
                token = getattr(account, "token", None) if account is not None else None
                if isinstance(token, dict) and "micoapi" in token:
                    token.pop("micoapi", None)
                    removed_keys.append("runtime.micoapi")

            # Clear persisted short-lived fields only, keep long-lived fields.
            data = deepcopy(self._get_oauth2_auth_data())
            if isinstance(data, dict):
                for key in SHORT_SESSION_KEYS:
                    if key in data:
                        data.pop(key, None)
                        removed_keys.append(key)

                if self.token_store is not None:
                    self.token_store.update(data, reason=f"clear_short_session:{clear_reason}")
                    self.token_store.flush()
                    wrote_back = True
                else:
                    with open(self.oauth2_token_path, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)
                    wrote_back = True

            self.cookie_jar = None
            self._auth_log(
                reason=clear_reason,
                action="clear_short_session",
                result="success",
                err=",".join(sorted(set(removed_keys))) if removed_keys else "no_short_fields_present",
            )
            self._emit_auth_state(
                auth_step="clear_short_session",
                auth_result="ok",
                auth_mode_before=mode_before,
                auth_mode_after=self._auth_mode,
                clear_reason=clear_reason,
            )
            self._emit_recovery_stage(
                stage="clear_short_session",
                result="ok",
                reason=summarized_reason,
                auth_mode_before=mode_before,
                auth_mode_after=self._auth_mode,
                extra={
                    "cleared_fields": sorted(set(removed_keys)),
                    **self._auth_presence_snapshot(),
                    "auth_json_writeback": "yes" if wrote_back else "no",
                },
            )
            self._auth_rebuild_state["last_clear_short_session"] = {
                "event": "auth_rebuild_stage",
                "stage": "clear_short_session",
                "result": "ok",
                "reason": summarized_reason,
                "auth_session_id": self._auth_session_id(),
                "cleared_fields": sorted(set(removed_keys)),
            }
            return True
        except Exception as clear_err:
            self._auth_log(
                reason=clear_reason,
                action="clear_short_session",
                result="fail",
                err=f"{type(clear_err).__name__}:{clear_err}",
            )
            self._emit_auth_state(
                auth_step="clear_short_session",
                auth_result="failed",
                auth_mode_before=mode_before,
                auth_mode_after=self._auth_mode,
                clear_reason=clear_reason,
                err=str(clear_err),
            )
            self._emit_recovery_stage(
                stage="clear_short_session",
                result="failed",
                reason=summarized_reason,
                auth_mode_before=mode_before,
                auth_mode_after=self._auth_mode,
                error_code=type(clear_err).__name__,
                error_message=str(clear_err),
                extra={
                    "cleared_fields": sorted(set(removed_keys)),
                    **self._auth_presence_snapshot(),
                    "auth_json_writeback": "yes" if wrote_back else "no",
                },
            )
            return False

    def auth_debug_state(self) -> dict[str, Any]:
        self._sync_auth_ttl()
        return {
            "auth_mode": self._auth_mode,
            "login_at": self._iso_utc(self._auth_login_at_ts),
            "expires_at": self._iso_utc(self._auth_expires_at_ts),
            "ttl_remaining_seconds": self._ttl_remaining_seconds(),
            "last_refresh_trigger": self._last_refresh_trigger,
            "last_auth_error": self._last_auth_error,
        }

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
        mode_before = self._auth_mode
        self._auth_locked_until_ts = time.time() + self._auth_lock_sec
        self._auth_lock_reason = (reason or "auth failed")[:200]
        self._set_auth_mode("locked", reason=reason or "auth_lock")
        self._auth_log(
            reason=reason or "auth_lock",
            action="lock",
            result="fail",
            err=f"until={int(self._auth_locked_until_ts)} reason={self._auth_lock_reason}",
        )
        self._emit_auth_state(
            auth_step="verify_session",
            auth_result="locked",
            refresh_trigger=self._last_refresh_trigger,
            auth_mode_before=mode_before,
            auth_mode_after="locked",
            err=reason or "auth_lock",
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
                auth_data = self._get_oauth2_auth_data()
                if not auth_data.get("serviceToken") and not auth_data.get("yetAnotherServiceToken"):
                    await self._rebuild_short_session_from_long_auth("init_all_data")
                await self.login_miboy(allow_login_fallback=False, reason="init_all_data")
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
        mode_before = self._auth_mode
        try:
            if self.mina_service is None:
                self._emit_auth_state(
                    auth_step="verify_session",
                    auth_result="refresh_failed",
                    auth_mode_before=mode_before,
                    auth_mode_after=self._auth_mode,
                    err="mina_service unavailable",
                )
                return False
            await self.mina_service.device_list()
            self._last_ok_ts = time.time()
            self._emit_auth_state(
                auth_step="verify_session",
                auth_result="ok",
                auth_mode_before=mode_before,
                auth_mode_after=self._auth_mode,
            )
            return True
        except Exception as e:
            self._emit_auth_state(
                auth_step="verify_session",
                auth_result=self._classify_auth_result(e),
                auth_mode_before=mode_before,
                auth_mode_after=self._auth_mode,
                err=str(e),
            )
            return False

    async def rebuild_services(self, reason: str, allow_login_fallback: bool = False) -> bool:
        mode_before = self._auth_mode
        if allow_login_fallback:
            self._emit_mi_login_trace(
                stage="login_http_exchange",
                sid="micoapi",
                result="skipped",
                reason=reason or "rebuild",
                auth_mode_before=mode_before,
                auth_mode_after=self._auth_mode,
                extra={
                    "request_step": "disabled",
                    "disabled_by_policy": True,
                    "resp_desc": "auto login fallback disabled by policy",
                },
            )
        before_session = self._auth_session_id()
        before_mina_id = id(self.mina_service) if self.mina_service is not None else 0
        before_short_fp = self._short_session_fingerprint()
        self.mark_session_invalid(reason or "rebuild")
        try:
            await self.login_miboy(
                allow_login_fallback=allow_login_fallback,
                reason=reason,
            )
        except Exception as e:
            if self._recovery_is_active():
                self._emit_recovery_stage(
                    stage="runtime_rebind",
                    result="failed",
                    reason=self._short_reason(f"{reason} {e}"),
                    auth_mode_before=mode_before,
                    auth_mode_after=self._auth_mode,
                    error_code=self._classify_auth_result(e),
                    error_message=str(e),
                    extra={
                        "auth_session_id_before": before_session,
                        "auth_session_id_after": self._auth_session_id(),
                        "runtime_instance_rebuilt": False,
                        "mina_service_rebound": False,
                        "token_source": "unknown",
                    },
                )
            raise
        ready = await self._verify_runtime_auth_ready()
        after_session = self._auth_session_id()
        after_mina_id = id(self.mina_service) if self.mina_service is not None else 0
        after_short_fp = self._short_session_fingerprint()
        token_source = "unknown"
        if after_short_fp != "none":
            token_source = "new" if before_short_fp != after_short_fp else "old"
        self._auth_log(
            reason=reason,
            action="rebuild",
            result="success" if ready else "fail",
            err="",
        )
        self._emit_auth_state(
            auth_step="rebuild_runtime",
            auth_result="ok" if ready else "refresh_failed",
            refresh_trigger=self._refresh_trigger_from_reason(reason),
            auth_mode_before=mode_before,
            auth_mode_after=self._auth_mode,
            err="" if ready else "runtime verify failed",
        )
        if self._recovery_is_active():
            self._emit_recovery_stage(
                stage="runtime_rebind",
                result="ok" if ready else "failed",
                reason=self._short_reason(reason),
                auth_mode_before=mode_before,
                auth_mode_after=self._auth_mode,
                error_code="" if ready else "runtime_verify_failed",
                error_message="" if ready else "runtime verify failed",
                extra={
                    "auth_session_id_before": before_session,
                    "auth_session_id_after": after_session,
                    "runtime_instance_rebuilt": bool(before_mina_id != after_mina_id and after_mina_id != 0),
                    "mina_service_rebound": bool(after_mina_id != 0),
                    "token_source": token_source,
                },
            )
        return ready

    async def refresh_oauth2_token_if_needed(self, reason: str, force: bool = False) -> dict[str, Any]:
        mode_before = self._auth_mode
        trigger = self._refresh_trigger_from_reason(reason)
        self._last_refresh_trigger = trigger
        now = time.time()
        if not force and self._last_refresh_ts and (now - self._last_refresh_ts) < self._refresh_min_interval_sec:
            self._emit_auth_state(
                auth_step="refresh",
                auth_result="refresh_failed",
                refresh_trigger=trigger,
                auth_mode_before=mode_before,
                auth_mode_after=self._auth_mode,
                err="refresh skipped by min interval",
            )
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
            refresh_fn = getattr(api, "_refresh_token", None)
            if not callable(refresh_fn):
                raise RuntimeError(
                    f"refresh api unavailable: {type(api).__name__} does not have a callable _refresh_token"
                )
            await asyncio.to_thread(refresh_fn, force)

            # Ensure persisted token snapshot is updated and visible to runtime.
            if self.token_store is not None:
                self.token_store.reload_from_disk()
            after = self._get_oauth2_auth_data()
            after_save_ms = int(after.get("saveTime") or 0)
            after_pass = str(after.get("passToken") or "")
            st = after.get("yetAnotherServiceToken") or after.get("serviceToken")
            token_saved = bool(
                after.get("userId")
                and after.get("passToken")
                and after.get("ssecurity")
                and st
                and after_save_ms >= before_save_ms
            )
            refresh_rotated = bool(before_pass and after_pass and before_pass != after_pass)
            self._sync_auth_ttl(after)

            self._last_refresh_ts = time.time()
            self._last_refresh_error = ""
            self._last_auth_error = ""
            self._auth_log(
                reason=reason,
                action="refresh",
                result="success",
                err=(
                    f"saved={token_saved} rotated={refresh_rotated} "
                    f"before={self._token_fp(before)} after={self._token_fp(after)}"
                ),
            )
            self._emit_auth_state(
                auth_step="refresh",
                auth_result="ok",
                refresh_trigger=trigger,
                auth_mode_before=mode_before,
                auth_mode_after=self._auth_mode,
            )
            return {
                "refreshed": True,
                "token_saved": token_saved,
                "last_error": None,
                "fallback_allowed": False,
            }
        except Exception as e:
            self._last_refresh_error = str(e)
            self._last_auth_error = str(e)
            fallback_allowed = self._refresh_failed_requires_relogin(e)
            self._auth_log(
                reason=reason,
                action="refresh",
                result="fail",
                err=f"{type(e).__name__}:{e}",
            )
            self._emit_auth_state(
                auth_step="refresh",
                auth_result=self._classify_auth_result(e),
                refresh_trigger=trigger,
                auth_mode_before=mode_before,
                auth_mode_after=self._auth_mode,
                err=str(e),
            )
            return {
                "refreshed": False,
                "token_saved": False,
                "last_error": str(e),
                "fallback_allowed": fallback_allowed,
            }

    async def manual_reload_runtime(self, reason: str = "manual_refresh_runtime") -> dict[str, Any]:
        async with self._relogin_lock:
            token_store_reloaded = False
            disk_has_service = False
            disk_has_yast = False
            runtime_seed_has_service = False
            mina_rebuilt = False
            miio_rebuilt = False
            device_map_refreshed = False
            verify_result = "failed"
            runtime_auth_ready = False
            token_loaded = False
            error_code = ""
            last_error = ""

            try:
                if self.token_store is not None:
                    self.token_store.reload_from_disk()
                    token_store_reloaded = True

                auth_data = self._get_oauth2_auth_data()
                token_loaded = bool(auth_data)
                disk_has_service = bool(auth_data.get("serviceToken"))
                disk_has_yast = bool(auth_data.get("yetAnotherServiceToken"))
                missing = self._missing_runtime_reload_fields(auth_data)
                if missing:
                    error_code = "missing_runtime_token_fields"
                    last_error = f"missing runtime token fields: {','.join(missing)}"
                    self._set_auth_mode("degraded", reason=reason)
                    self._last_auth_error = last_error
                    self._emit_oauth_runtime_reload(
                        result="failed",
                        reason=reason,
                        token_store_reloaded=token_store_reloaded,
                        disk_has_service_token=disk_has_service,
                        disk_has_yast=disk_has_yast,
                        runtime_seed_has_service_token=False,
                        mina_service_rebuilt=False,
                        miio_service_rebuilt=False,
                        device_map_refreshed=False,
                        verify_result="failed",
                        error_code=error_code,
                        error_message=last_error,
                        refresh_token_path_invoked=False,
                    )
                    return {
                        "refreshed": False,
                        "runtime_auth_ready": False,
                        "token_saved": False,
                        "token_loaded": token_loaded,
                        "token_store_reloaded": token_store_reloaded,
                        "runtime_rebound": False,
                        "device_map_refreshed": False,
                        "verify_result": "failed",
                        "last_error": last_error,
                        "error_code": error_code,
                        "timestamps": {
                            "saveTime": int(self._token_save_ts() * 1000) if self._token_save_ts() else None,
                            "last_ok_ts": int(self._last_ok_ts * 1000) if self._last_ok_ts else None,
                            "last_refresh_ts": int(self._last_refresh_ts * 1000) if self._last_refresh_ts else None,
                        },
                    }

                runtime_auth_ready = await self.rebuild_services(
                    reason=reason,
                    allow_login_fallback=False,
                )
                mina_rebuilt = self.mina_service is not None
                miio_rebuilt = self.miio_service is not None
                runtime_seed_has_service = bool(self._short_session_fingerprint() != "none")

                if runtime_auth_ready:
                    await self.device_manager.update_device_info(self)
                    device_map_refreshed = True
                    self._last_auth_error = ""
                    self._set_auth_mode("healthy", reason=reason)
                    verify_result = "ok"
                else:
                    error_code = "runtime_verify_failed"
                    last_error = "runtime verify failed"
                    self._last_auth_error = last_error
                    self._set_auth_mode("degraded", reason=reason)

            except Exception as e:
                last_error = str(e)
                error_code = type(e).__name__
                self._last_auth_error = last_error
                self._set_auth_mode("degraded", reason=reason)
                runtime_auth_ready = False

            self._emit_oauth_runtime_reload(
                result="ok" if runtime_auth_ready else "failed",
                reason=reason,
                token_store_reloaded=token_store_reloaded,
                disk_has_service_token=disk_has_service,
                disk_has_yast=disk_has_yast,
                runtime_seed_has_service_token=runtime_seed_has_service,
                mina_service_rebuilt=mina_rebuilt,
                miio_service_rebuilt=miio_rebuilt,
                device_map_refreshed=device_map_refreshed,
                verify_result=verify_result,
                error_code=error_code,
                error_message=last_error,
                refresh_token_path_invoked=False,
            )

            return {
                "refreshed": bool(runtime_auth_ready),
                "runtime_auth_ready": bool(runtime_auth_ready),
                "token_saved": False,
                "token_loaded": token_loaded,
                "token_store_reloaded": token_store_reloaded,
                "runtime_rebound": bool(mina_rebuilt and miio_rebuilt),
                "device_map_refreshed": bool(device_map_refreshed),
                "verify_result": verify_result,
                "last_error": last_error or None,
                "error_code": error_code,
                "timestamps": {
                    "saveTime": int(self._token_save_ts() * 1000) if self._token_save_ts() else None,
                    "last_ok_ts": int(self._last_ok_ts * 1000) if self._last_ok_ts else None,
                    "last_refresh_ts": int(self._last_refresh_ts * 1000) if self._last_refresh_ts else None,
                },
            }

    async def manual_refresh(self, reason: str = "manual_refresh") -> dict[str, Any]:
        # Backward-compatible entrypoint used by existing WebUI button.
        return await self.manual_reload_runtime(reason=reason)

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
                    self._clear_short_lived_session(
                        clear_reason=f"auth_recovery:{reason or 'auth_error'}",
                        err=reason or "auth_error",
                    )
                    rebuilt = await self._rebuild_short_session_from_long_auth(reason or "auth_error")
                    if not rebuilt:
                        raise RuntimeError("short session rebuild failed")
                    ready = await self.rebuild_services(
                        reason=reason or "auth_error",
                        allow_login_fallback=False,
                    )
                    self._auth_rebuild_state["last_runtime_rebind"] = {
                        "event": "auth_rebuild_stage",
                        "stage": "runtime_rebind",
                        "result": "ok" if ready else "failed",
                        "reason": reason or "auth_error",
                        "auth_session_id": self._auth_session_id(),
                    }
                    if not ready:
                        raise RuntimeError("rebuild after short session rebuild failed")
                else:
                    self.mark_session_invalid(reason or "ensure_logged_in")
                    await self.init_all_data()
                    if await self.need_login():
                        raise RuntimeError("relogin completed but service still unavailable")
                self._auth_rebuild_state["last_verify"] = {
                    "event": "auth_rebuild_stage",
                    "stage": "verify",
                    "result": "ok",
                    "reason": reason or "ensure_logged_in",
                    "auth_session_id": self._auth_session_id(),
                }
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
                long_auth_ok = self._has_long_auth_fields(self._get_oauth2_auth_data())
                if (
                    not long_auth_ok
                    or self._refresh_failed_requires_relogin(e)
                    or "runtime verify after login failed" in err_text
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
            self._clear_short_lived_session(
                clear_reason=ctx or "auth_error",
                err=e,
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
        step = "keepalive" if "keepalive" in str(ctx or "").lower() else "mina_call"
        mode_before = self._auth_mode
        if self._is_high_freq_request(method_name, ctx):
            skip, skip_reason = self._should_skip_high_freq(method_name)
            if skip:
                self._auth_log(
                    reason=f"mina:{method_name}:{ctx}",
                    action=skip_reason,
                    result="degraded",
                )
                self._emit_auth_state(
                    auth_step=step,
                    auth_result="network_error" if skip_reason == "rate_limited" else "locked",
                    auth_mode_before=mode_before,
                    auth_mode_after=self._auth_mode,
                    err=skip_reason,
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
            self._emit_auth_state(
                auth_step=step,
                auth_result="ok",
                auth_mode_before=mode_before,
                auth_mode_after=self._auth_mode,
            )
            return ret
        except Exception as e:
            self._emit_auth_state(
                auth_step=step,
                auth_result=self._classify_auth_result(e),
                auth_mode_before=mode_before,
                auth_mode_after=self._auth_mode,
                err=str(e),
            )
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
                self.log.info("auth keepalive loop cancelled")
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

    async def login_miboy(self, allow_login_fallback: bool = False, reason: str = ""):
        """登录小米账号

        使用 OAuth2 token 登录小米账号，并初始化相关服务。
        """
        mode_before = self._auth_mode
        try:
            auth_data = self._get_oauth2_auth_data()
            before_session_id = self._auth_session_id()
            before_short_fp = self._short_session_fingerprint(auth_data)
            before_service = str(auth_data.get("serviceToken") or "")
            before_yast = str(auth_data.get("yetAnotherServiceToken") or "")
            before_ssecurity = str(auth_data.get("ssecurity") or "")
            account_name = auth_data.get("userId", "")
            if not account_name:
                raise RuntimeError("oauth2 token missing userId")
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
            login_exchange_called = False
            if has_service_token:
                try:
                    await self.mina_service.device_list()
                    runtime_verified = True
                    if self._recovery_is_active():
                        self._emit_recovery_stage(
                            stage="login_exchange",
                            result="skipped",
                            reason="existing_short_session_verified",
                            auth_mode_before=mode_before,
                            auth_mode_after=self._auth_mode,
                            extra={
                                "provider": "micoapi",
                                "login_http_status": "",
                                "login_error_code": "",
                                "login_error_message": "",
                                "token_changed_serviceToken": False,
                                "token_changed_yetAnotherServiceToken": False,
                                "token_changed_ssecurity": False,
                            },
                        )
                except Exception as verify_err:
                    self._auth_log(
                        reason=reason or "login_verify",
                        action="verify_service_token",
                        result="fail",
                        err=f"{type(verify_err).__name__}:{verify_err}",
                    )
                    if not allow_login_fallback:
                        raise RuntimeError("service token verify failed and login fallback disabled")
                    self._clear_short_lived_session(
                        clear_reason=f"verify_service_token_failed:{reason or 'login_verify'}",
                        err=verify_err,
                    )
                    self._auth_log(
                        reason=reason or "auth_error",
                        action="login_fallback",
                        result="skipped",
                    )
                    login_exchange_called = True
                    self._emit_mi_login_trace(
                        stage="login_input_snapshot",
                        sid="micoapi",
                        result="ok",
                        reason=reason or "login_fallback",
                        auth_mode_before=mode_before,
                        auth_mode_after=self._auth_mode,
                        extra=self._mi_login_input_snapshot(mi_account),
                    )
                    self._emit_mi_login_trace(
                        stage="login_http_exchange",
                        sid="micoapi",
                        result="skipped",
                        reason=reason or "login_fallback",
                        auth_mode_before=mode_before,
                        auth_mode_after=self._auth_mode,
                        extra={
                            "request_step": "disabled",
                            "http_status": "",
                            "resp_code": "",
                            "resp_desc": "auto login fallback disabled by policy",
                            "callback_host": "",
                            "has_location": False,
                            "has_set_cookie": False,
                            "has_service_token_cookie": False,
                            "has_yet_another_service_token_cookie": False,
                            "disabled_by_policy": True,
                        },
                    )
                    raise RuntimeError("service token verify failed and auto login fallback disabled")
            else:
                self._auth_log(
                    reason=reason or "auth_error",
                    action="login_fallback",
                    result="skipped",
                )
                login_exchange_called = True
                self._emit_mi_login_trace(
                    stage="login_input_snapshot",
                    sid="micoapi",
                    result="ok",
                    reason=reason or "login_fallback",
                    auth_mode_before=mode_before,
                    auth_mode_after=self._auth_mode,
                    extra=self._mi_login_input_snapshot(mi_account),
                )
                self._emit_mi_login_trace(
                    stage="login_http_exchange",
                    sid="micoapi",
                    result="skipped",
                    reason=reason or "login_fallback",
                    auth_mode_before=mode_before,
                    auth_mode_after=self._auth_mode,
                    extra={
                        "request_step": "disabled",
                        "http_status": "",
                        "resp_code": "",
                        "resp_desc": "auto login fallback disabled by policy",
                        "callback_host": "",
                        "has_location": False,
                        "has_set_cookie": False,
                        "has_service_token_cookie": False,
                        "has_yet_another_service_token_cookie": False,
                        "disabled_by_policy": True,
                    },
                )
                raise RuntimeError("missing short session token; rebuild from long auth required")

            if login_exchange_called and self._recovery_is_active():
                after_data_for_exchange = self._get_oauth2_auth_data()
                after_service = str(after_data_for_exchange.get("serviceToken") or "")
                after_yast = str(after_data_for_exchange.get("yetAnotherServiceToken") or "")
                after_ssecurity = str(after_data_for_exchange.get("ssecurity") or "")
                self._emit_recovery_stage(
                    stage="login_exchange",
                    result="ok",
                    reason=self._short_reason(reason or "login_exchange"),
                    auth_mode_before=mode_before,
                    auth_mode_after=self._auth_mode,
                    extra={
                        "provider": "micoapi",
                        "login_http_status": "",
                        "login_error_code": "",
                        "login_error_message": "",
                        "token_changed_serviceToken": bool(after_service and after_service != before_service),
                        "token_changed_yetAnotherServiceToken": bool(after_yast and after_yast != before_yast),
                        "token_changed_ssecurity": bool(after_ssecurity and after_ssecurity != before_ssecurity),
                    },
                )

            if login_exchange_called:
                acct = getattr(mi_account, "token", {}) or {}
                mico = acct.get("micoapi") if isinstance(acct, dict) else None
                parsed_service = bool(
                    (isinstance(mico, (tuple, list)) and len(mico) >= 2 and mico[1])
                    or acct.get("serviceToken")
                )
                parsed_yast = bool(
                    (isinstance(mico, (tuple, list)) and len(mico) >= 2 and mico[1])
                    or acct.get("yetAnotherServiceToken")
                )
                parsed_ssec = bool(
                    (isinstance(mico, (tuple, list)) and len(mico) >= 1 and mico[0])
                    or acct.get("ssecurity")
                )
                parse_result = "ok" if (parsed_service or parsed_yast) else "failed"
                self._emit_mi_login_trace(
                    stage="login_response_parse",
                    sid="micoapi",
                    result=parse_result,
                    reason=reason or "login_exchange",
                    auth_mode_before=mode_before,
                    auth_mode_after=self._auth_mode,
                    extra={
                        "parsed_serviceToken": parsed_service,
                        "parsed_yetAnotherServiceToken": parsed_yast,
                        "parsed_ssecurity": parsed_ssec,
                        "parsed_from": "unknown",
                        "parse_error_type": "" if parse_result == "ok" else "missing_short_tokens",
                        "parse_error_message": "" if parse_result == "ok" else "no short token parsed from account token",
                    },
                )
            else:
                self._emit_mi_login_trace(
                    stage="login_response_parse",
                    sid="micoapi",
                    result="skipped",
                    reason="service_token_reused",
                    auth_mode_before=mode_before,
                    auth_mode_after=self._auth_mode,
                    extra={
                        "parsed_serviceToken": bool(oauth_service_token),
                        "parsed_yetAnotherServiceToken": bool(oauth_service_token),
                        "parsed_ssecurity": bool(oauth_ssecurity),
                        "parsed_from": "auth_json",
                        "parse_error_type": "",
                        "parse_error_message": "",
                    },
                )

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
                    self._clear_short_lived_session(
                        clear_reason=f"runtime_verify_failed:{reason or 'login_verify'}",
                        err=verify_err,
                    )
                    raise RuntimeError(f"runtime verify after login failed: {verify_err}")

            self.login_acount = account_name
            self._persist_oauth2_token(auth_data=auth_data, mi_account=mi_account, reason="login")
            self._sync_auth_ttl(login_at_ts=time.time())
            after_short_fp = self._short_session_fingerprint(self._get_oauth2_auth_data())
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
            self._emit_auth_state(
                auth_step="login",
                auth_result="ok",
                refresh_trigger=self._refresh_trigger_from_reason(reason),
                auth_mode_before=mode_before,
                auth_mode_after=self._auth_mode,
            )
            if self._recovery_is_active():
                token_source = "new" if after_short_fp != before_short_fp and after_short_fp != "none" else "old"
                self._emit_recovery_stage(
                    stage="runtime_rebind",
                    result="ok",
                    reason=self._short_reason(reason),
                    auth_mode_before=mode_before,
                    auth_mode_after=self._auth_mode,
                    extra={
                        "auth_session_id_before": before_session_id,
                        "auth_session_id_after": self._auth_session_id(),
                        "runtime_instance_rebuilt": True,
                        "mina_service_rebound": bool(self.mina_service is not None),
                        "token_source": token_source,
                    },
                )
            if self._recovery_is_active() and not login_exchange_called:
                # Keep runtime-rebind token source inference available for verify-only path.
                self._emit_recovery_stage(
                    stage="login_exchange",
                    result="skipped",
                    reason="service_token_reused",
                    auth_mode_before=mode_before,
                    auth_mode_after=self._auth_mode,
                    extra={
                        "provider": "micoapi",
                        "login_http_status": "",
                        "login_error_code": "",
                        "login_error_message": "",
                        "token_changed_serviceToken": bool(after_short_fp != before_short_fp),
                        "token_changed_yetAnotherServiceToken": False,
                        "token_changed_ssecurity": False,
                    },
                )

            acct_after = getattr(mi_account, "token", {}) or {}
            mico_ssec_after, mico_st_after = self._micoapi_tokens(acct_after)
            self._emit_mi_login_trace(
                stage="post_login_runtime_seed",
                sid="micoapi",
                result="ok",
                reason=reason or "login",
                auth_mode_before=mode_before,
                auth_mode_after=self._auth_mode,
                extra={
                    "runtime_seed_has_serviceToken": bool(acct_after.get("serviceToken") or mico_st_after),
                    "runtime_seed_has_yetAnotherServiceToken": bool(acct_after.get("yetAnotherServiceToken") or mico_st_after),
                    "runtime_seed_has_ssecurity": bool(acct_after.get("ssecurity") or mico_ssec_after),
                    "runtime_seed_source": "mi_account.token",
                },
            )
        except Exception as e:
            self.mina_service = None
            self.miio_service = None
            self.log.warning(f"可能登录失败. {e}")
            self._last_auth_error = str(e)
            self._auth_log(
                reason=reason or "login",
                action="login",
                result="fail",
                err=f"{type(e).__name__}:{e}",
            )
            self._emit_auth_state(
                auth_step="login",
                auth_result=self._classify_auth_result(e),
                refresh_trigger=self._refresh_trigger_from_reason(reason),
                auth_mode_before=mode_before,
                auth_mode_after=self._auth_mode,
                err=str(e),
            )
            if self._recovery_is_active():
                self._emit_mi_login_trace(
                    stage="login_response_parse",
                    sid="micoapi",
                    result="failed",
                    reason=reason or "login",
                    auth_mode_before=mode_before,
                    auth_mode_after=self._auth_mode,
                    extra={
                        "parsed_serviceToken": False,
                        "parsed_yetAnotherServiceToken": False,
                        "parsed_ssecurity": False,
                        "parsed_from": "unknown",
                        "parse_error_type": self._classify_auth_result(e),
                        "parse_error_message": str(e)[:120],
                    },
                )
                self._emit_mi_login_trace(
                    stage="token_writeback",
                    sid="micoapi",
                    result="skipped",
                    reason=reason or "login",
                    auth_mode_before=mode_before,
                    auth_mode_after=self._auth_mode,
                    extra={
                        "wrote_serviceToken": False,
                        "wrote_yetAnotherServiceToken": False,
                        "wrote_target": "none",
                        "token_store_flush": "no",
                        "auth_json_has_serviceToken_after": False,
                        "auth_json_has_yetAnotherServiceToken_after": False,
                    },
                )
                self._emit_recovery_stage(
                    stage="login_exchange",
                    result="failed",
                    reason=self._short_reason(f"{reason} {e}"),
                    auth_mode_before=mode_before,
                    auth_mode_after=self._auth_mode,
                    error_code=self._classify_auth_result(e),
                    error_message=str(e),
                    extra={
                        "provider": "micoapi",
                        "login_http_status": "",
                        "login_error_code": self._classify_auth_result(e),
                        "login_error_message": str(e)[:120],
                        "token_changed_serviceToken": False,
                        "token_changed_yetAnotherServiceToken": False,
                        "token_changed_ssecurity": False,
                    },
                )
                self._emit_recovery_stage(
                    stage="runtime_rebind",
                    result="failed",
                    reason=self._short_reason(f"{reason} {e}"),
                    auth_mode_before=mode_before,
                    auth_mode_after=self._auth_mode,
                    error_code=self._classify_auth_result(e),
                    error_message=str(e),
                    extra={
                        "auth_session_id_before": self._auth_session_id(),
                        "auth_session_id_after": self._auth_session_id(),
                        "runtime_instance_rebuilt": False,
                        "mina_service_rebound": False,
                        "token_source": "unknown",
                    },
                )
            self._emit_mi_login_trace(
                stage="post_login_runtime_seed",
                sid="micoapi",
                result="failed",
                reason=reason or "login",
                auth_mode_before=mode_before,
                auth_mode_after=self._auth_mode,
                extra={
                    "runtime_seed_has_serviceToken": False,
                    "runtime_seed_has_yetAnotherServiceToken": False,
                    "runtime_seed_has_ssecurity": False,
                    "runtime_seed_source": "unknown",
                },
            )
            raise

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
            self._emit_mi_login_trace(
                stage="token_writeback",
                sid="micoapi",
                result="skipped",
                reason=reason or "login",
                extra={
                    "wrote_serviceToken": False,
                    "wrote_yetAnotherServiceToken": False,
                    "wrote_target": "none",
                    "token_store_flush": "no",
                    "auth_json_has_serviceToken_after": False,
                    "auth_json_has_yetAnotherServiceToken_after": False,
                },
            )
            return
        merged = deepcopy(auth_data or {})
        merged["saveTime"] = int(time.time() * 1000)
        wrote_service = False
        wrote_yast = False
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
                    wrote_service = True
                    wrote_yast = True
            if acct.get("serviceToken"):
                merged["serviceToken"] = acct.get("serviceToken")
                wrote_service = True
            if acct.get("yetAnotherServiceToken"):
                merged["yetAnotherServiceToken"] = acct.get("yetAnotherServiceToken")
                wrote_yast = True
        except Exception as e:
            self.log.warning("persist token merge failed: %s", e)

        self.token_store.update(merged, reason=reason or "login")
        self.token_store.flush()
        after_data = self._get_oauth2_auth_data()
        self._emit_mi_login_trace(
            stage="token_writeback",
            sid="micoapi",
            result="ok" if (wrote_service or wrote_yast) else "skipped",
            reason=reason or "login",
            extra={
                "wrote_serviceToken": wrote_service,
                "wrote_yetAnotherServiceToken": wrote_yast,
                "wrote_target": "both",
                "token_store_flush": "yes",
                "auth_json_has_serviceToken_after": bool(after_data.get("serviceToken")),
                "auth_json_has_yetAnotherServiceToken_after": bool(after_data.get("yetAnotherServiceToken")),
            },
        )

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
