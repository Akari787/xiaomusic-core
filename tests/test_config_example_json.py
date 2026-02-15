import json
from pathlib import Path


def test_config_example_json_is_valid_and_has_required_fields():
    repo_root = Path(__file__).resolve().parents[1]
    path = repo_root / "config-example.json"
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)

    # Required keys must exist.
    for k in (
        "enable_exec_plugin",
        "allowed_exec_commands",
        "outbound_allowlist_domains",
        "enable_self_update",
        "cors_allow_origins",
        "log_redact",
        "persist_token",
    ):
        assert k in data

    # Any __comment_* field must be a single-line string.
    for k, v in data.items():
        if not k.startswith("__comment_"):
            continue
        assert isinstance(v, str)
        assert "\n" not in v
