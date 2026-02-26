from types import SimpleNamespace

from xiaomusic.api.base_url import detect_base_url


def _request(host: str = "", scheme: str = "http"):
    return SimpleNamespace(headers={"host": host}, url=SimpleNamespace(scheme=scheme))


def _config(public_base_url: str = ""):
    return SimpleNamespace(public_base_url=public_base_url)


def test_detect_base_url_prefers_public_base_url():
    req = _request(host="example.local:8090", scheme="https")
    cfg = _config(public_base_url="https://public.example.com:9443")
    assert detect_base_url(req, cfg) == "https://public.example.com:9443"


def test_detect_base_url_uses_request_host_when_not_loopback():
    req = _request(host="192.168.7.178:58090", scheme="http")
    cfg = _config(public_base_url="")
    assert detect_base_url(req, cfg) == "http://192.168.7.178:58090"


def test_detect_base_url_returns_none_for_localhost_in_container(monkeypatch):
    req = _request(host="localhost:8090", scheme="http")
    cfg = _config(public_base_url="")
    monkeypatch.setattr("xiaomusic.api.base_url._is_container_env", lambda: True)
    assert detect_base_url(req, cfg) is None


def test_detect_base_url_returns_none_for_invalid_host_colon():
    req = _request(host=":58090", scheme="http")
    cfg = _config(public_base_url="")
    assert detect_base_url(req, cfg) is None


def test_detect_base_url_returns_none_for_loopback_ipv4():
    req = _request(host="127.0.0.1:58090", scheme="http")
    cfg = _config(public_base_url="")
    assert detect_base_url(req, cfg) is None


def test_detect_base_url_returns_none_when_no_signal():
    req = _request(host="", scheme="http")
    cfg = _config(public_base_url="")
    assert detect_base_url(req, cfg) is None
