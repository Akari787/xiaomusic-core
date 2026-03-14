from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from xiaomusic.config import Config


def test_auth_token_file_prefers_new_env(monkeypatch, tmp_path):
    monkeypatch.setenv("XIAOMUSIC_AUTH_TOKEN_FILE", "auth-new.json")
    monkeypatch.setenv("XIAOMUSIC_OAUTH2_TOKEN_FILE", "auth-old.json")
    cfg = Config(conf_path=str(tmp_path))
    assert cfg.auth_token_file == "auth-new.json"
    assert cfg.oauth2_token_file == "auth-old.json"
    assert Path(cfg.auth_token_path).name == "auth-new.json"
    assert cfg.oauth2_token_path == cfg.auth_token_path


def test_auth_token_file_falls_back_to_legacy_env(monkeypatch, tmp_path):
    monkeypatch.delenv("XIAOMUSIC_AUTH_TOKEN_FILE", raising=False)
    monkeypatch.setenv("XIAOMUSIC_OAUTH2_TOKEN_FILE", "legacy-auth.json")
    cfg = Config(conf_path=str(tmp_path))
    assert cfg.auth_token_file == "legacy-auth.json"
    assert cfg.oauth2_token_path == cfg.auth_token_path


def test_cli_option_new_name_overrides_legacy(tmp_path):
    options = argparse.Namespace(config=None, auth_token_file="new-auth.json", oauth2_token_file="old-auth.json")

    cfg = Config.from_options(options)
    cfg.conf_path = str(tmp_path)
    cfg.init()
    assert cfg.auth_token_file == "new-auth.json"
    assert cfg.oauth2_token_file == "old-auth.json"
    assert Path(cfg.auth_token_path).name == "new-auth.json"


@pytest.mark.asyncio
async def test_auth_and_oauth2_refresh_aliases_match(monkeypatch):
    pytest.importorskip("qrcode")
    from xiaomusic.api.routers import system

    class _AuthManager:
        async def manual_reload_runtime(self, reason="manual_refresh_runtime"):
            return {
                "refreshed": True,
                "runtime_auth_ready": True,
                "token_store_reloaded": True,
                "verify_result": "ok",
                "last_error": None,
                "error_code": "",
                "timestamps": {"saveTime": 1, "last_ok_ts": 2, "last_refresh_ts": 3},
            }

    monkeypatch.setattr(system, "xiaomusic", type("_XM", (), {"auth_manager": _AuthManager()})())
    auth_out = await system.auth_refresh()
    legacy_out = await system.oauth2_refresh()
    assert auth_out == legacy_out


@pytest.mark.asyncio
async def test_auth_and_oauth2_status_aliases_match(monkeypatch, tmp_path):
    pytest.importorskip("qrcode")
    from xiaomusic.api.routers import system

    token_file = tmp_path / "auth.json"
    token_file.write_text('{"userId":"u","passToken":"p","ssecurity":"s","serviceToken":"t"}', encoding="utf-8")

    class _TokenStore:
        path = token_file

        @staticmethod
        def get():
            return {"userId": "u", "passToken": "p", "ssecurity": "s", "serviceToken": "t"}

    class _AuthManager:
        @staticmethod
        async def need_login():
            return False

        @staticmethod
        def auth_status_snapshot():
            return {"mode": "healthy", "locked": False, "locked_until_ts": None, "lock_reason": ""}

    monkeypatch.setattr(system, "config", type("_Cfg", (), {"auth_token_path": str(token_file), "qrcode_timeout": 120})())
    monkeypatch.setattr(system, "xiaomusic", type("_XM", (), {"auth_manager": _AuthManager(), "token_store": _TokenStore()})())
    monkeypatch.setattr(system, "qrcode_login_task", None)
    monkeypatch.setattr(system, "qrcode_login_started_at", 0.0)
    monkeypatch.setattr(system, "qrcode_login_error", "")

    auth_out = await system.auth_status()
    legacy_out = await system.oauth2_status()
    assert auth_out == legacy_out
