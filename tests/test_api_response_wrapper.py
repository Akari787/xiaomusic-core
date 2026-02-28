from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError

from xiaomusic.api import response as api_response


def test_ok_standard_contract_shape():
    out = api_response.ok({"foo": 1}, message="ok")
    assert out["ok"] is True
    assert out["success"] is True
    assert out["error_code"] is None
    assert out["message"] == "ok"
    assert out["foo"] == 1


def test_fail_standard_contract_shape():
    out = api_response.fail("E_BAD_REQUEST", "bad")
    assert out["ok"] is False
    assert out["success"] is False
    assert out["error_code"] == "E_BAD_REQUEST"
    assert out["message"] == "bad"


def test_from_exception_http_exception_keeps_detail_shape():
    resp = api_response.from_exception(HTTPException(status_code=403, detail="forbidden"))
    assert resp.status_code == 403
    assert resp.body == b'{"detail":"forbidden"}'


def test_from_exception_validation_keeps_422_detail_shape():
    exc = RequestValidationError(
        [{"loc": ["query", "x"], "msg": "Field required", "type": "missing"}]
    )
    resp = api_response.from_exception(exc)
    assert resp.status_code == 422
    assert b'"detail"' in resp.body
