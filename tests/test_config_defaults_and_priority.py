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
    cfg = Config(hostname="http://192.168.1.10", public_port=58090, public_base_url="https://edge.example.com:9443")
    assert cfg.get_public_base_url() == "https://edge.example.com:9443"
    assert cfg.get_self_netloc() == "edge.example.com:9443"


def test_public_base_url_falls_back_to_legacy_host_port():
    cfg = Config(hostname="http://192.168.1.10", public_port=58090, public_base_url="")
    assert cfg.get_public_base_url() == "http://192.168.1.10:58090"
    assert cfg.get_self_netloc() == "192.168.1.10:58090"
