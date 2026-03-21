"""Unified URL strategy shared by playback workflows."""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

from xiaomusic.relay.url_classifier import UrlClassifier


@dataclass
class NormalizedLink:
    source_type: str
    direct_url: str
    proxy_url: Optional[str] = None
    headers: dict[str, str] = field(default_factory=dict)
    ttl_sec: Optional[int] = None


class LinkPlaybackStrategy:
    def __init__(self, music_library, log) -> None:
        self.music_library = music_library
        self.log = log
        self.classifier = UrlClassifier()

    def classify(self, raw_url: str):
        return self.classifier.classify(raw_url)

    def should_use_relay(self, raw_url: str) -> bool:
        info = self.classify(raw_url)
        return info.site in {"youtube", "bilibili"}

    def should_use_network_audio(self, raw_url: str) -> bool:
        return self.should_use_relay(raw_url)

    def normalize_input_url(self, raw_url: str) -> str:
        info = self.classify(raw_url)
        return info.normalized_url

    def build_proxy_url(self, raw_url: str, name: str = "") -> str:
        return self.music_library.get_proxy_url(raw_url, name=name)

    @staticmethod
    def _is_private_rfc1918_ipv4(ip: ipaddress.IPv4Address) -> bool:
        return (
            ip in ipaddress.ip_network("10.0.0.0/8")
            or ip in ipaddress.ip_network("172.16.0.0/12")
            or ip in ipaddress.ip_network("192.168.0.0/16")
        )

    @staticmethod
    def _is_explicitly_blocked_ipv4(ip: ipaddress.IPv4Address) -> bool:
        blocked_nets = (
            "172.17.0.0/16",
            "172.18.0.0/16",
            "172.19.0.0/16",
            "127.0.0.0/8",
            "0.0.0.0/8",
            "169.254.0.0/16",
            "100.64.0.0/10",
            "198.18.0.0/15",
        )
        return any(ip in ipaddress.ip_network(net) for net in blocked_nets)

    @classmethod
    def _is_allowed_ip_literal(cls, ip: ipaddress._BaseAddress) -> bool:
        # IPv6 literals are blocked for proxy target by default.
        if isinstance(ip, ipaddress.IPv6Address):
            return False
        if cls._is_explicitly_blocked_ipv4(ip):
            return False
        if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            return False
        return cls._is_private_rfc1918_ipv4(ip)

    @classmethod
    def _resolved_ips_safe_for_domain(cls, host: str) -> bool:
        try:
            infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except Exception:
            return False

        resolved = []
        for family, _type, _proto, _canon, sockaddr in infos:
            if family == socket.AF_INET:
                resolved.append(sockaddr[0])
            elif family == socket.AF_INET6:
                resolved.append(sockaddr[0])
        if not resolved:
            return False

        for ip_s in resolved:
            try:
                ip_obj = ipaddress.ip_address(ip_s)
            except ValueError:
                return False
            # Domain targets must resolve to public routable IPs only.
            if (
                ip_obj.is_private
                or ip_obj.is_loopback
                or ip_obj.is_link_local
                or ip_obj.is_multicast
                or ip_obj.is_reserved
                or ip_obj.is_unspecified
            ):
                return False
            if isinstance(ip_obj, ipaddress.IPv4Address) and cls._is_explicitly_blocked_ipv4(ip_obj):
                return False
        return True

    def _host_allowed_for_proxy(self, raw_url: str) -> bool:
        host = (urlparse(raw_url).hostname or "").strip().lower()
        if not host:
            return False

        if host == "localhost":
            return False

        # IP literal path: allow only strict RFC1918 subset.
        try:
            ip_obj = ipaddress.ip_address(host)
            return self._is_allowed_ip_literal(ip_obj)
        except ValueError:
            pass

        cfg = getattr(self.music_library, "config", None)
        allowlist = []
        if cfg is not None:
            allowlist = list(getattr(cfg, "outbound_allowlist_domains", []) or [])
        if not allowlist:
            return False
        normalized_allowlist = [str(d).strip().lower().rstrip(".") for d in allowlist if str(d).strip()]
        allow_ok = any(host == d or host.endswith("." + d) for d in normalized_allowlist)
        if not allow_ok:
            return False
        return self._resolved_ips_safe_for_domain(host)

    def normalize(self, raw_url: str, *, name: str = "") -> NormalizedLink:
        direct = self.normalize_input_url(raw_url)
        if self.music_library.is_jellyfin_url(direct):
            source_type = "jellyfin"
        else:
            source_type = self.classify(direct).site
        proxy_url = None
        if self._host_allowed_for_proxy(direct):
            proxy_url = self.build_proxy_url(direct, name=name)
        return NormalizedLink(
            source_type=source_type,
            direct_url=direct,
            proxy_url=proxy_url,
        )

    def should_fallback(
        self,
        *,
        startup_ok: bool,
        fail_count: int,
        reason: str = "",
    ) -> bool:
        if not startup_ok:
            return True
        if fail_count >= 2:
            return True
        if reason and reason in {
            "not_playing",
            "player_play_failed",
            "connection_reset",
        }:
            return True
        return False

    def select_url(
        self,
        normalized: NormalizedLink,
        *,
        prefer: str = "direct",
        startup_ok: bool = True,
        fail_count: int = 0,
        failure_reason: str = "",
    ) -> str:
        if prefer == "proxy" and normalized.proxy_url:
            return normalized.proxy_url
        if not self.should_fallback(
            startup_ok=startup_ok,
            fail_count=fail_count,
            reason=failure_reason,
        ):
            return normalized.direct_url
        if normalized.proxy_url:
            return normalized.proxy_url
        return normalized.direct_url

    def should_jellyfin_auto_fallback(
        self,
        jellyfin_mode: str,
        origin_url: str,
        current_url: str,
    ) -> bool:
        mode = (jellyfin_mode or "auto").lower()
        return bool(
            mode == "auto"
            and origin_url
            and origin_url == current_url
            and self.music_library.is_jellyfin_url(current_url)
        )
