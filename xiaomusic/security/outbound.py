from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urljoin, urlparse

import aiohttp


class OutboundBlockedError(RuntimeError):
    pass


def _is_ip_literal(host: str) -> bool:
    # urlparse strips brackets for IPv6? It returns '::1' for [::1]
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _is_private_or_special(ip: ipaddress._BaseAddress) -> bool:
    # Block anything that can hit local network or non-routable spaces.
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _normalize_host(host: str) -> str:
    return host.strip().lower().rstrip(".")


def _host_in_allowlist(host: str, allowlist: Iterable[str]) -> bool:
    host = _normalize_host(host)
    for item in allowlist:
        item = _normalize_host(item)
        if not item:
            continue
        if host == item:
            return True
        if host.endswith("." + item):
            return True
    return False


async def _resolve_host(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    ips: list[str] = []
    for family, _type, _proto, _canon, sockaddr in infos:
        if family == socket.AF_INET:
            ip = sockaddr[0]
        elif family == socket.AF_INET6:
            ip = sockaddr[0]
        else:
            continue
        if ip not in ips:
            ips.append(ip)
    return ips


class _FixedResolver(aiohttp.abc.AbstractResolver):
    def __init__(self, mapping: dict[str, list[str]]):
        self._mapping = {k: list(v) for k, v in mapping.items()}

    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_INET):
        host_n = _normalize_host(host)
        ips = self._mapping.get(host_n)
        if not ips:
            raise OSError(f"No fixed resolution for host: {host}")
        out = []
        for ip in ips:
            fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
            if family not in (socket.AF_UNSPEC, fam):
                continue
            out.append(
                {
                    "hostname": host,
                    "host": ip,
                    "port": port,
                    "family": fam,
                    "proto": 0,
                    "flags": socket.AI_NUMERICHOST,
                }
            )
        if not out:
            raise OSError(f"No fixed resolution for host: {host}")
        return out

    async def close(self):
        return None


@dataclass(frozen=True)
class OutboundPolicy:
    allowlist_domains: tuple[str, ...] = ()
    deny_private_networks: bool = True

    def validate_url(self, url: str) -> None:
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            raise OutboundBlockedError("only http/https allowed")
        if not p.hostname:
            raise OutboundBlockedError("missing hostname")

        host = _normalize_host(p.hostname)
        if not self.allowlist_domains:
            raise OutboundBlockedError("outbound allowlist not configured")
        if not _host_in_allowlist(host, self.allowlist_domains):
            raise OutboundBlockedError("domain not allowlisted")
        if _is_ip_literal(host):
            raise OutboundBlockedError("ip literal not allowed")

    async def resolve_and_validate(self, host: str, port: int) -> list[str]:
        host_n = _normalize_host(host)
        if _is_ip_literal(host_n):
            raise OutboundBlockedError("ip literal not allowed")

        ips = await _resolve_host(host_n, port)
        if not ips:
            raise OutboundBlockedError("dns resolve failed")

        if self.deny_private_networks:
            for ip_s in ips:
                ip = ipaddress.ip_address(ip_s)
                if _is_private_or_special(ip):
                    raise OutboundBlockedError("resolved to private/special ip")

        return ips


async def fetch_bytes(
    url: str,
    *,
    policy: OutboundPolicy,
    timeout_s: float = 5.0,
    max_bytes: int = 1024 * 1024,
    max_redirects: int = 3,
    user_agent: str | None = None,
) -> bytes:
    """Fetch URL with SSRF protections.

    - Allowlist-by-domain required
    - Block IP literals
    - DNS rebinding: pin connection to validated IP(s) via fixed resolver
    - Manual redirects with per-hop revalidation
    - Bounded timeout and payload size
    """
    cur = url
    redirects = 0
    start = time.time()

    while True:
        policy.validate_url(cur)
        p = urlparse(cur)
        assert p.hostname is not None
        port = p.port or (443 if p.scheme == "https" else 80)
        ips = await policy.resolve_and_validate(p.hostname, port)

        resolver = _FixedResolver({_normalize_host(p.hostname): ips})
        connector = aiohttp.TCPConnector(
            resolver=resolver,
            ssl=True if p.scheme == "https" else False,
            limit=10,
        )
        headers = {}
        if user_agent:
            headers["User-Agent"] = user_agent

        timeout = aiohttp.ClientTimeout(total=timeout_s)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            async with session.get(cur, allow_redirects=False, headers=headers) as resp:
                if resp.status in (301, 302, 303, 307, 308):
                    loc = resp.headers.get("Location")
                    if not loc:
                        raise OutboundBlockedError("redirect without location")
                    redirects += 1
                    if redirects > max_redirects:
                        raise OutboundBlockedError("too many redirects")
                    cur = urljoin(cur, loc)
                    continue

                if resp.status >= 400:
                    raise OutboundBlockedError(f"upstream http {resp.status}")

                buf = bytearray()
                async for chunk in resp.content.iter_chunked(16 * 1024):
                    buf.extend(chunk)
                    if len(buf) > max_bytes:
                        raise OutboundBlockedError("response too large")

                # Guard against endless loops consuming time budget.
                if time.time() - start > timeout_s:
                    raise OutboundBlockedError("timeout")

                return bytes(buf)


async def fetch_text(
    url: str,
    *,
    policy: OutboundPolicy,
    timeout_s: float = 5.0,
    max_bytes: int = 1024 * 1024,
    max_redirects: int = 3,
    user_agent: str | None = None,
) -> str:
    data = await fetch_bytes(
        url,
        policy=policy,
        timeout_s=timeout_s,
        max_bytes=max_bytes,
        max_redirects=max_redirects,
        user_agent=user_agent,
    )
    return data.decode("utf-8", errors="replace")
