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
    "servicetoken expired",
    "token expired",
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

        # 当前设备DID（用于设备ID更新）
        self._cur_did = None
        self.device_id = get_random(16).upper()
        self.mi_session = ClientSession()
        self.device_manager = device_manager

    def is_auth_error(self, exc=None, resp=None, body=None) -> bool:
        return is_auth_error(exc=exc, resp=resp, body=body)

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

    async def ensure_logged_in(self, force=False, reason=""):
        async with self._relogin_lock:
            now = time.time()
            self._relogin_inflight_ts = now
            if reason:
                self._last_relogin_reason = reason

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

            self.mark_session_invalid(reason or "ensure_logged_in")
            try:
                await self.init_all_data()
                if await self.need_login():
                    raise RuntimeError("relogin completed but service still unavailable")
                self._last_ok_ts = time.time()
                self._relogin_fail_streak = 0
                self._next_relogin_allowed_ts = 0.0
                self.log.info("ensure_logged_in success")
                return True
            except Exception:
                self._relogin_fail_streak += 1
                delay = min(30 * (2 ** max(self._relogin_fail_streak - 1, 0)), 300)
                self._next_relogin_allowed_ts = time.time() + delay
                self.log.warning(
                    "ensure_logged_in failed. streak=%d next_retry_after=%ss",
                    self._relogin_fail_streak,
                    delay,
                )
                raise

    async def auth_call(self, fn, *, retry=1, ctx=""):
        try:
            return await fn()
        except Exception as e:
            if not self.is_auth_error(exc=e):
                raise

            self.log.warning(f"auth_call detect auth error ctx={ctx} err={e}")
            await self.ensure_logged_in(force=True, reason=ctx or str(e))
            if retry <= 0:
                raise
            return await fn()

    async def mina_call(self, method_name: str, *args, retry=1, ctx="", **kwargs):
        async def _call():
            if self.mina_service is None:
                raise RuntimeError("mina service unavailable")
            method = getattr(self.mina_service, method_name)
            return await method(*args, **kwargs)

        return await self.auth_call(_call, retry=retry, ctx=f"mina:{method_name}:{ctx}")

    async def miio_call(self, fn, *, retry=1, ctx=""):
        return await self.auth_call(fn, retry=retry, ctx=f"miio:{ctx}")

    async def keepalive_loop(self, interval_sec=300):
        while True:
            try:
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
        if os.path.isfile(self.oauth2_token_path):
            oauth2_mtime = int(os.path.getmtime(self.oauth2_token_path))
        return (oauth2_mtime, self.config.mi_did)

    async def login_miboy(self):
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
            oauth_service_token = auth_data.get("serviceToken") or auth_data.get(
                "yetAnotherServiceToken"
            )
            oauth_ssecurity = auth_data.get("ssecurity")
            if oauth_service_token and oauth_ssecurity:
                try:
                    mi_account.token["micoapi"] = (oauth_ssecurity, oauth_service_token)
                except Exception:
                    # keep fallback login path
                    pass

            # OAuth2 扫码场景优先使用 serviceToken，避免触发账号二次风控验证。
            # 但 serviceToken 可能已失效：先走免登录路径，失败后回退到显式 login。
            has_service_token = bool(oauth_service_token and oauth_ssecurity)

            self.mina_service = MiNAService(mi_account)
            self.miio_service = MiIOService(mi_account)

            if has_service_token:
                try:
                    await self.mina_service.device_list()
                except Exception as verify_err:
                    self.log.warning(
                        f"OAuth2 serviceToken 可能失效，回退账号登录流程: {verify_err}"
                    )
                    await mi_account.login("micoapi")
                    self.mina_service = MiNAService(mi_account)
                    self.miio_service = MiIOService(mi_account)
            else:
                await mi_account.login("micoapi")
                self.mina_service = MiNAService(mi_account)
                self.miio_service = MiIOService(mi_account)

            self.login_acount = account_name
            self.login_signature = self._get_login_signature()
            self.log.info(f"登录完成. {self.login_acount}")
        except Exception as e:
            self.mina_service = None
            self.miio_service = None
            self.log.warning(f"可能登录失败. {e}")

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

    def _get_oauth2_auth_data(self):
        if self.token_store is not None:
            user_data = self.token_store.load().data
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
        service_token = auth_data.get("serviceToken") or auth_data.get(
            "yetAnotherServiceToken"
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
        service_token = auth_data.get("serviceToken") or auth_data.get(
            "yetAnotherServiceToken"
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
