from fastapi import Query
from fastapi.testclient import TestClient

from xiaomusic.api.app import app


def _ensure_test_routes():
    paths = {getattr(r, "path", "") for r in app.routes}
    if "/__test/http_403" not in paths:

        @app.get("/__test/http_403")
        async def _http_403():
            from fastapi import HTTPException

            raise HTTPException(status_code=403, detail="forbidden")

    if "/__test/http_401" not in paths:

        @app.get("/__test/http_401")
        async def _http_401():
            from fastapi import HTTPException

            raise HTTPException(status_code=401, detail="unauthorized")

    if "/__test/validation" not in paths:

        @app.get("/__test/validation")
        async def _validation(x: int = Query(...)):  # noqa: ARG001
            return {"ok": True}

    if "/__test/error_500" not in paths:

        @app.get("/__test/error_500")
        async def _error_500():
            raise RuntimeError("secret-value-should-not-leak")


def test_exception_handlers_keep_contract_and_status():
    _ensure_test_routes()
    client = TestClient(app, raise_server_exceptions=False)

    r_403 = client.get("/__test/http_403")
    assert r_403.status_code == 403
    assert r_403.json() == {"detail": "forbidden"}

    r_401 = client.get("/__test/http_401")
    assert r_401.status_code == 401
    assert r_401.json() == {"detail": "unauthorized"}

    r_422 = client.get("/__test/validation")
    assert r_422.status_code == 422
    assert isinstance(r_422.json().get("detail"), list)

    r_500 = client.get("/__test/error_500")
    assert r_500.status_code == 500
    body = r_500.json()
    assert body["ok"] is False
    assert body["error_code"] == "E_INTERNAL"
    assert body["message"] == "Internal server error"
    assert "secret-value-should-not-leak" not in r_500.text
