from __future__ import annotations

import argparse
from pathlib import Path

from xiaomusic.config import Config


def test_env_example_quotes_bcrypt_hash_to_survive_compose_interpolation():
    env_example = Path(__file__).parents[1] / ".env.example"
    lines = env_example.read_text(encoding="utf-8").splitlines()
    auth_example_lines = [
        line for line in lines
        if "HTTP_AUTH_PASSWORD=" in line or "HTTP_AUTH_HASH=" in line
    ]
    assert auth_example_lines == [
        "# HTTP_AUTH_PASSWORD='replace_with_a_strong_password'",
        "# HTTP_AUTH_HASH='$2b$12$replace_with_bcrypt_hash'",
    ]
    assert all(line.startswith("# ") and line.count("'") == 2 for line in auth_example_lines)
    assert not any(
        line.strip().startswith(("HTTP_AUTH_PASSWORD=", "HTTP_AUTH_HASH="))
        for line in lines
    )


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


def test_auth_refresh_config_values_are_safe(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_REFRESH_INTERVAL_HOURS", "invalid")
    monkeypatch.setenv("AUTH_REFRESH_THRESHOLD", "2")
    cfg = Config(conf_path=str(tmp_path))
    assert cfg.auth_refresh_interval_hours == 12
    assert cfg.auth_refresh_threshold == 0.99

    monkeypatch.setenv("AUTH_REFRESH_INTERVAL_HOURS", "0")
    monkeypatch.setenv("AUTH_REFRESH_THRESHOLD", "-1")
    cfg = Config(conf_path=str(tmp_path))
    assert cfg.auth_refresh_interval_hours == 0.01
    assert cfg.auth_refresh_threshold == 0.01
