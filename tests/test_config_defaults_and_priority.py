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
