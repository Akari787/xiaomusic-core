import socket

import pytest

from xiaomusic.playback.link_strategy import LinkPlaybackStrategy


class _Cfg:
    def __init__(self, allowlist=None):
        self.outbound_allowlist_domains = allowlist or []


class _Lib:
    def __init__(self, allowlist=None):
        self.config = _Cfg(allowlist=allowlist)

    def get_proxy_url(self, url, name=""):
        return f"/proxy?url={url}"

    def is_jellyfin_url(self, url):
        return False


def _s(allowlist=None):
    return LinkPlaybackStrategy(music_library=_Lib(allowlist=allowlist), log=None)


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("192.168.7.4", True),
        ("10.0.0.5", True),
        ("172.16.0.10", True),
        ("172.31.255.254", True),
        ("172.17.0.10", False),
        ("172.18.0.10", False),
        ("172.19.0.10", False),
        ("172.0.0.1", False),
        ("127.0.0.1", False),
        ("127.1.2.3", False),
        ("localhost", False),
        ("0.0.0.0", False),
        ("169.254.1.1", False),
        ("100.64.0.1", False),
        ("198.18.0.1", False),
        ("::1", False),
        ("fe80::1", False),
    ],
)
def test_proxy_allowlist_ip_matrix(host, expected):
    out = _s()._host_allowed_for_proxy(f"http://{host}/a.mp3")
    assert out is expected


def test_proxy_allowlist_domain_requires_allowlist():
    assert _s()._host_allowed_for_proxy("https://example.com/a.mp3") is False


def test_proxy_allowlist_domain_suffix_hit(monkeypatch):
    def _fake_getaddrinfo(host, port, type=0):  # noqa: ARG001
        assert host == "a.example.com"
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 0)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)
    assert _s(["example.com"])._host_allowed_for_proxy("https://a.example.com/x") is True


def test_proxy_allowlist_domain_dns_private_blocked(monkeypatch):
    def _fake_getaddrinfo(host, port, type=0):  # noqa: ARG001
        assert host == "example.com"
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.2", 0)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)
    assert _s(["example.com"])._host_allowed_for_proxy("https://example.com/live") is False


def test_proxy_allowlist_domain_dns_public_allowed(monkeypatch):
    def _fake_getaddrinfo(host, port, type=0):  # noqa: ARG001
        assert host == "example.com"
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 0)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)
    assert _s(["example.com"])._host_allowed_for_proxy("https://example.com/live") is True
