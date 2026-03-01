from __future__ import annotations

import ipaddress
import os
import socket
from typing import Optional
from urllib.parse import urlparse


def _normalize_base_url(raw: str) -> Optional[str]:
    if not raw:
        return None
    value = str(raw).strip()
    if not value:
        return None
    if not value.startswith(("http://", "https://")):
        value = f"http://{value}"
    parsed = urlparse(value)
    if not parsed.hostname:
        return None
    if parsed.port is None:
        return f"{parsed.scheme}://{parsed.hostname}"
    return f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"


def _is_container_env() -> bool:
    if os.path.exists("/.dockerenv"):
        return True
    for p in ("/proc/1/cgroup", "/proc/self/cgroup"):
        try:
            with open(p, encoding="utf-8") as f:
                txt = f.read().lower()
            if "docker" in txt or "kubepods" in txt:
                return True
        except Exception:
            continue
    return False


def _is_local_host(host: str) -> bool:
    h = (host or "").strip().lower()
    if h in {"localhost", "0.0.0.0"}:
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_unspecified


def _is_recommended_private_ipv4(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if addr.version != 4:
        return False

    if not (
        addr in ipaddress.ip_network("10.0.0.0/8")
        or addr in ipaddress.ip_network("172.16.0.0/12")
        or addr in ipaddress.ip_network("192.168.0.0/16")
    ):
        return False

    blocked = (
        "172.17.0.0/16",
        "172.18.0.0/16",
        "172.19.0.0/16",
        "169.254.0.0/16",
        "100.64.0.0/10",
        "198.18.0.0/15",
    )
    for net in blocked:
        if addr in ipaddress.ip_network(net):
            return False
    return True


def _first_recommended_private_ipv4() -> Optional[str]:
    candidates: list[str] = []
    try:
        hostname = socket.gethostname()
        for _, _, _, _, sockaddr in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = sockaddr[0]
            if ip and _is_recommended_private_ipv4(ip):
                candidates.append(ip)
    except Exception:
        pass

    # UDP socket trick: asks kernel selected source ip without real traffic.
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and _is_recommended_private_ipv4(ip):
            candidates.insert(0, ip)
    except Exception:
        pass

    for ip in candidates:
        if ip and _is_recommended_private_ipv4(ip):
            return ip
    return None


def detect_base_url(request, config) -> Optional[str]:
    # 1) explicit override in config
    explicit = _normalize_base_url(getattr(config, "public_base_url", ""))
    if explicit:
        return explicit

    # 2) request host + scheme (prefer reverse-proxy forwarded headers)
    forwarded_proto = (request.headers.get("x-forwarded-proto", "") or "").split(",", 1)[0].strip()
    forwarded_host = (request.headers.get("x-forwarded-host", "") or "").split(",", 1)[0].strip()
    scheme = forwarded_proto or getattr(getattr(request, "url", None), "scheme", None) or "http"
    host_hdr = forwarded_host or request.headers.get("host", "")
    if host_hdr:
        cand = _normalize_base_url(f"{scheme}://{host_hdr}")
        if cand:
            p = urlparse(cand)
            host = (p.hostname or "").lower()
            if not _is_local_host(host):
                return cand

            # 3) localhost/loopback is ambiguous for speaker reachability.
            # In container env, never guess from localhost.
            if host in {"localhost", "127.0.0.1", "::1"}:
                if _is_container_env():
                    return None
                return None

            # 0.0.0.0 -> best effort private IPv4 only
            ip = _first_recommended_private_ipv4()
            if ip:
                if p.port is not None:
                    return f"{scheme}://{ip}:{p.port}"
                return f"{scheme}://{ip}"

    # 4) no deterministic answer
    return None
