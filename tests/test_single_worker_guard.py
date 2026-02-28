import logging

import pytest

from xiaomusic import cli


def test_single_worker_guard_allows_default(monkeypatch):
    for key in ("XIAOMUSIC_WORKERS", "UVICORN_WORKERS", "WEB_CONCURRENCY", "GUNICORN_WORKERS"):
        monkeypatch.delenv(key, raising=False)
    cli._enforce_single_worker()


def test_single_worker_guard_rejects_multi_worker(monkeypatch):
    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    with pytest.raises(RuntimeError, match="workers=1"):
        cli._enforce_single_worker()


def test_httpauth_unsafe_warning(monkeypatch, caplog):
    cfg = type("_Cfg", (), {})()
    cfg.disable_httpauth = True
    cfg.cors_allow_origins = ["https://example.com"]
    caplog.set_level(logging.WARNING)
    logger = logging.getLogger("test-httpauth")
    cli._warn_if_httpauth_unsafe(cfg, "0.0.0.0", logger)
    assert "HTTP auth disabled; if exposed beyond LAN this is unsafe" in caplog.text
