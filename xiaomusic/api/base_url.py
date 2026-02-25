from __future__ import annotations

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


def _first_non_loopback_ipv4() -> Optional[str]:
    candidates: list[str] = []
    try:
        hostname = socket.gethostname()
        for _, _, _, _, sockaddr in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = sockaddr[0]
            if ip and not ip.startswith("127."):
                candidates.append(ip)
    except Exception:
        pass

    # UDP socket trick: asks kernel selected source ip without real traffic.
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            candidates.insert(0, ip)
    except Exception:
        pass

    for ip in candidates:
        if ip and not ip.startswith("127."):
            return ip
    return None


def detect_base_url(request, config) -> Optional[str]:
    # 1) explicit override in config
    explicit = _normalize_base_url(getattr(config, "public_base_url", ""))
    if explicit:
        return explicit

    # 2) request host + scheme
    scheme = getattr(getattr(request, "url", None), "scheme", None) or "http"
    host_hdr = request.headers.get("host", "")
    if host_hdr:
        cand = _normalize_base_url(f"{scheme}://{host_hdr}")
        if cand:
            p = urlparse(cand)
            host = (p.hostname or "").lower()
            if host not in {"0.0.0.0", "localhost"}:
                return cand

            # 3) replace localhost/0.0.0.0 with first non-loopback IPv4
            ip = _first_non_loopback_ipv4()
            if ip:
                if p.port is not None:
                    return f"{scheme}://{ip}:{p.port}"
                return f"{scheme}://{ip}"

    # 4) no deterministic answer
    return None
