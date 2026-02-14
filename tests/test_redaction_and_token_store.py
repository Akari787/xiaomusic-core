import io
import json
import logging
import os

import pytest

from xiaomusic.security.logging import RedactingLogFormatter
from xiaomusic.security.token_store import TokenStore


class DummyConfig:
    def __init__(self, token_path, persist_token=True):
        self._token_path = token_path
        self.persist_token = persist_token

    @property
    def oauth2_token_path(self):
        return self._token_path


def test_log_redaction():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(RedactingLogFormatter("%(message)s"))
    logger = logging.getLogger("redact-test")
    logger.handlers.clear()
    logger.propagate = False
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    secret = "abc123"
    logger.info("Authorization: Bearer %s", secret)
    logger.info("api_key=%s", secret)
    logger.info("cookie: %s", secret)

    out = stream.getvalue()
    assert "***REDACTED***" in out
    assert secret not in out


def test_token_precedence_env_over_file(tmp_path, monkeypatch):
    token_file = tmp_path / "auth.json"
    token_file.write_text(json.dumps({"userId": "u", "passToken": "old", "serviceToken": "old"}))

    cfg = DummyConfig(str(token_file))
    store = TokenStore(cfg, logging.getLogger("t"))

    monkeypatch.setenv("OAUTH2_ACCESS_TOKEN", "new_access")
    monkeypatch.setenv("OAUTH2_REFRESH_TOKEN", "new_refresh")

    data = store.load().data
    assert data["serviceToken"] == "new_access"
    assert data["passToken"] == "new_refresh"


def test_persist_token_false_no_write(tmp_path):
    token_file = tmp_path / "auth.json"
    cfg = DummyConfig(str(token_file), persist_token=False)
    store = TokenStore(cfg, logging.getLogger("t"))

    store.save({"userId": "u", "passToken": "p"})
    assert not token_file.exists()
