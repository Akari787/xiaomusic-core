#!/usr/bin/env python3
"""系统操作和环境相关工具函数"""

import asyncio
import copy
import logging
import os
import platform
import random
import string
import urllib.parse
from http.cookies import SimpleCookie
from urllib.parse import urlparse

import aiohttp
from requests.utils import cookiejar_from_dict

from xiaomusic.security.errors import SelfUpdateDisabledError
from xiaomusic.security.outbound import OutboundBlockedError, OutboundPolicy, fetch_bytes
from xiaomusic.security.tar_safe import safe_extract_tar_gz

log = logging.getLogger(__package__)


def parse_cookie_string_to_dict(cookie_string: str):
    """
    解析 Cookie 字符串
    Args:
        cookie_string: Cookie 字符串
    Returns:
        CookieJar 对象
    """
    cookie = SimpleCookie()
    cookie.load(cookie_string)
    cookies_dict = {k: m.value for k, m in cookie.items()}
    return cookies_dict


def parse_cookie_string(cookie_string: str):
    """
    解析 Cookie 字符串

    Args:
        cookie_string: Cookie 字符串

    Returns:
        CookieJar 对象
    """
    cookies_dict = parse_cookie_string_to_dict(cookie_string)
    return cookiejar_from_dict(cookies_dict, cookiejar=None, overwrite=True)


def validate_proxy(proxy_str: str) -> bool:
    """
    验证代理字符串格式

    Args:
        proxy_str: 代理字符串

    Returns:
        True 如果格式正确

    Raises:
        ValueError: 如果格式不正确
    """
    parsed = urlparse(proxy_str)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Proxy scheme must be http or https")
    if not (parsed.hostname and parsed.port):
        raise ValueError("Proxy hostname and port must be set")

    return True


def get_random(length: int) -> str:
    """
    生成随机字符串

    Args:
        length: 字符串长度

    Returns:
        随机字符串
    """
    return "".join(random.sample(string.ascii_letters + string.digits, length))


def deepcopy_data_no_sensitive_info(data, fields_to_anonymize: list = None):
    """
    深拷贝数据并脱敏

    Args:
        data: 要拷贝的数据（字典或对象）
        fields_to_anonymize: 需要脱敏的字段列表

    Returns:
        脱敏后的深拷贝数据
    """
    if fields_to_anonymize is None:
        fields_to_anonymize = [
            "jellyfin_api_key",
            "httpauth_username",
            "httpauth_password",
        ]

    copy_data = copy.deepcopy(data)

    # 检查copy_data是否是字典或具有属性的对象
    if isinstance(copy_data, dict):
        # 对字典进行处理
        for field in fields_to_anonymize:
            if field in copy_data:
                copy_data[field] = "******"
    else:
        # 对对象进行处理
        for field in fields_to_anonymize:
            if hasattr(copy_data, field):
                setattr(copy_data, field, "******")

    return copy_data


def try_add_access_control_param(config, url: str) -> str:
    """
    兼容保留：旧版链接鉴权参数注入（已移除）

    Args:
        config: 配置对象
        url: 原始 URL

    Returns:
        原始 URL（不再追加任何参数）
    """
    # 旧版 key/code 链接鉴权已移除，保留该函数避免历史调用链崩溃。
    return url


def is_docker() -> bool:
    """判断是否在 Docker 容器中运行"""
    return os.path.exists("/app/.dockerenv")


def get_os_architecture() -> str:
    """
    获取操作系统架构类型：amd64、arm64、arm-v7

    Returns:
        str: 架构类型
    """
    arch = platform.machine().lower()

    if arch in ("x86_64", "amd64"):
        return "amd64"
    elif arch in ("aarch64", "arm64"):
        return "arm64"
    elif "arm" in arch or "armv7" in arch:
        return "arm-v7"
    else:
        return f"unknown architecture: {arch}"


async def get_latest_version(package_name: str) -> str:
    """
    从 PyPI 获取包的最新版本

    Args:
        package_name: 包名

    Returns:
        最新版本号，失败返回 None
    """
    url = f"https://pypi.org/pypi/{package_name}/json"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status == 200:
                data = await response.json()
                return data["info"]["version"]
            else:
                return None


async def restart_xiaomusic() -> int:
    """
    重启 xiaomusic 程序

    Returns:
        退出码
    """
    # 重启 xiaomusic 程序
    sbp_args = (
        "supervisorctl",
        "restart",
        "xiaomusic",
    )

    cmd = " ".join(sbp_args)
    log.info(f"restart_xiaomusic: {cmd}")
    await asyncio.sleep(2)
    proc = await asyncio.create_subprocess_exec(*sbp_args)
    exit_code = await proc.wait()  # 等待子进程完成
    log.info(f"restart_xiaomusic completed with exit code {exit_code}")
    return exit_code


async def update_version(config, version: str, lite: bool = True) -> str:
    """
    更新 xiaomusic 版本

    Args:
        version: 版本号
        lite: 是否使用 lite 版本

    Returns:
        结果消息
    """
    if not getattr(config, "enable_self_update", False):
        raise SelfUpdateDisabledError("self update disabled")

    if not is_docker():
        ret = "xiaomusic 更新只能在 docker 中进行"
        log.info(ret)
        return ret
    lite_tag = ""
    if lite:
        lite_tag = "-lite"
    arch = get_os_architecture()
    if "unknown" in arch:
        log.warning(f"update_version failed: {arch}")
        return arch
    # https://github.com/hanxi/xiaomusic/releases/download/main/app-amd64-lite.tar.gz
    url = f"https://gproxy.hanxi.cc/proxy/hanxi/xiaomusic/releases/download/{version}/app-{arch}{lite_tag}.tar.gz"
    target_directory = "/app"
    return await download_and_extract(config, url, target_directory)


async def download_and_extract(config, url: str, target_directory: str) -> str:
    """
    下载并解压文件

    Args:
        url: 下载 URL
        target_directory: 目标目录

    Returns:
        结果消息
    """
    ret = "OK"
    # 创建目标目录
    os.makedirs(target_directory, exist_ok=True)

    # Outbound allowlist is required for downloads.
    domains = getattr(config, "outbound_allowlist_domains", None) or getattr(
        config, "allowlist_domains", []
    )
    policy = OutboundPolicy(tuple(domains))
    try:
        policy.validate_url(url)
    except OutboundBlockedError as e:
        ret = f"Outbound blocked: {e}"
        log.warning(ret)
        return ret

    try:
        data = await fetch_bytes(
            url,
            policy=policy,
            timeout_s=10.0,
            max_bytes=200 * 1024 * 1024,
            max_redirects=3,
            user_agent="XiaoMusic/self-update",
        )
    except OutboundBlockedError as e:
        ret = f"下载失败: {e}"
        log.warning(ret)
        return ret

    file_name = os.path.join(target_directory, url.split("/")[-1])
    file_name = os.path.normpath(file_name)
    if not file_name.startswith(target_directory):
        log.warning(f"Invalid file path: {file_name}")
        return "Invalid file path"
    with open(file_name, "wb") as f:
        f.write(data)
    log.info(f"文件下载完成: {file_name}")

    # 解压下载的文件
    if file_name.endswith(".tar.gz"):
        safe_extract_tar_gz(file_name, target_directory)
    else:
        ret = f"下载失败, 包有问题: {file_name}"
        log.warning(ret)
    return ret


async def extract_tar_gz(file_name: str, target_directory: str) -> None:
    # Backward compatible shim: keep name but do safe extraction.
    safe_extract_tar_gz(file_name, target_directory)
