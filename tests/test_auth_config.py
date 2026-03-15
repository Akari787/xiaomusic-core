from __future__ import annotations

import argparse
from pathlib import Path

from xiaomusic.config import Config


def test_auth_token_file_uses_auth_env(monkeypatch, tmp_path):
    monkeypatch.setenv("XIAOMUSIC_AUTH_TOKEN_FILE", "auth-new.json")
    cfg = Config(conf_path=str(tmp_path))
    assert cfg.auth_token_file == "auth-new.json"
    assert Path(cfg.auth_token_path).name == "auth-new.json"


def test_auth_token_file_uses_auth_cli_option(tmp_path):
    options = argparse.Namespace(config=None, auth_token_file="custom-auth.json")
    cfg = Config.from_options(options)
    cfg.conf_path = str(tmp_path)
    cfg.init()
    assert cfg.auth_token_file == "custom-auth.json"
    assert Path(cfg.auth_token_path).name == "custom-auth.json"


def test_auth_refresh_intervals_use_new_env_names(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_REFRESH_INTERVAL_HOURS", "6")
    monkeypatch.setenv("AUTH_REFRESH_MIN_INTERVAL_MINUTES", "15")
    cfg = Config(conf_path=str(tmp_path))
    assert cfg.auth_refresh_interval_hours == 6
    assert cfg.auth_refresh_min_interval_minutes == 15
