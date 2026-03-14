from __future__ import annotations

from pathlib import Path


def test_primary_entry_docs_and_compose_use_core_naming() -> None:
    repo = Path(__file__).resolve().parents[1]
    targets = [
        repo / "README.md",
        repo / "docker-compose.yml",
        repo / "docker-compose.hardened.yml",
        repo / "docs" / "index.md",
        repo / "docs" / "issues" / "index.md",
    ]
    for path in targets:
        text = path.read_text(encoding="utf-8")
        assert "xiaomusic-core" in text

    compose_text = (repo / "docker-compose.yml").read_text(encoding="utf-8")
    hardened_text = (repo / "docker-compose.hardened.yml").read_text(encoding="utf-8")
    assert "xiaomusic-oauth2" not in compose_text
    assert "xiaomusic-oauth2" not in hardened_text
    assert "oauth2-only" not in hardened_text


def test_primary_entry_docs_recommend_auth_paths_and_fields() -> None:
    repo = Path(__file__).resolve().parents[1]
    readme = (repo / "README.md").read_text(encoding="utf-8")
    assert "/api/auth/refresh" in readme
    assert "auth_token_file" in readme
