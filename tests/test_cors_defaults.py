from fastapi import FastAPI
from fastapi.testclient import TestClient

from xiaomusic.api.app import _configure_cors


def test_cors_default_is_not_wildcard():
    app = FastAPI()

    @app.get("/ping")
    def ping():
        return {"ok": True}

    _configure_cors(app, ["http://localhost", "http://127.0.0.1"])
    client = TestClient(app)

    # Allowed origin gets CORS allow header.
    r = client.options(
        "/ping",
        headers={
            "Origin": "http://localhost",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r.headers.get("access-control-allow-origin") == "http://localhost"

    # Disallowed origin should not be allowed.
    r2 = client.options(
        "/ping",
        headers={
            "Origin": "http://evil.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r2.headers.get("access-control-allow-origin") is None
