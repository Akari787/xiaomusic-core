from types import SimpleNamespace

import bcrypt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPBasicCredentials

from xiaomusic.api import dependencies


def test_httpauth_verification_uses_bcrypt_hash(monkeypatch):
    password = "s3cret-pass"
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    monkeypatch.delenv("API_SECRET", raising=False)
    monkeypatch.setattr(dependencies, "config", SimpleNamespace(httpauth_username="admin"))
    monkeypatch.setattr(
        dependencies,
        "get_auth_settings",
        lambda: SimpleNamespace(HTTP_AUTH_HASH=hashed),
    )

    creds = HTTPBasicCredentials(username="admin", password=password)
    assert dependencies.verification(creds) is True


def test_httpauth_verification_rejects_plaintext_or_wrong_password(monkeypatch):
    password = "right-pass"
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    monkeypatch.delenv("API_SECRET", raising=False)
    monkeypatch.setattr(dependencies, "config", SimpleNamespace(httpauth_username="admin"))
    monkeypatch.setattr(
        dependencies,
        "get_auth_settings",
        lambda: SimpleNamespace(HTTP_AUTH_HASH=hashed),
    )

    wrong = HTTPBasicCredentials(username="admin", password="bad-pass")
    with pytest.raises(HTTPException):
        dependencies.verification(wrong)
