from __future__ import annotations

from pathlib import Path


def test_primary_entry_docs_and_compose_use_core_naming() -> None:
    repo = Path(__file__).resolve().parents[1]
    targets = [
        repo / "README.md",
        repo / "ARCHITECTURE.md",
        repo / "docker-compose.yml",
        repo / "docker-compose.hardened.yml",
        repo / "docs" / "index.md",
        repo / "docs" / "architecture" / "README.md",
    ]
    for path in targets:
        assert path.is_file(), f"missing current entry document: {path}"
        assert "xiaomusic-core" in path.read_text(encoding="utf-8")


def test_primary_entry_docs_recommend_current_auth_paths() -> None:
    repo = Path(__file__).resolve().parents[1]
    auth_spec = (repo / "docs" / "spec" / "auth" / "auth_runtime_recovery.md").read_text(
        encoding="utf-8"
    )
    assert "/api/admin/v1/auth/status" in auth_spec
    assert "/api/internal/diagnostics/auth_state" in auth_spec
    assert "TokenStore" in auth_spec
    assert "auth.json" in auth_spec
