from xiaomusic.config import Config


def test_config_defaults_and_priority():
    cfg = Config()

    # Defaults
    assert cfg.enable_self_update is False
    assert cfg.cors_allow_origins == ["http://localhost", "http://127.0.0.1"]

    # Priority: outbound_allowlist_domains > allowlist_domains
    cfg.update_config(
        {
            "allowlist_domains": ["allow.test"],
            "outbound_allowlist_domains": ["out.test"],
        }
    )
    assert cfg.outbound_allowlist_domains == ["out.test"]

    # Backward compatibility: if outbound_allowlist_domains empty, reuse allowlist_domains
    cfg.update_config(
        {
            "allowlist_domains": ["legacy.test"],
            "outbound_allowlist_domains": [],
        }
    )
    assert cfg.outbound_allowlist_domains == ["legacy.test"]


def test_public_base_url_prefers_manual_override():
    cfg = Config(public_base_url="https://edge.example.com:9443")
    assert cfg.get_public_base_url() == "https://edge.example.com:9443"
    assert cfg.get_self_netloc() == "edge.example.com:9443"


def test_public_base_url_empty_when_not_configured():
    cfg = Config(public_base_url="")
    assert cfg.get_public_base_url() == ""
    assert cfg.get_self_netloc() == ""


def test_removed_settings_in_old_setting_json_are_ignored(tmp_path):
    import json

    path = tmp_path / "setting.json"
    path.write_text(
        json.dumps({"hostname": "http://192.168.1.10", "verbose": True, "crontab_json": "[]"}),
        encoding="utf-8",
    )
    data = Config.read_from_file(str(path))
    assert data == {"verbose": True}

    cfg = Config()
    cfg.update_config({"hostname": "http://x", "music_list_url": "http://y"})
    assert not hasattr(cfg, "hostname")
    assert not hasattr(cfg, "music_list_url")
