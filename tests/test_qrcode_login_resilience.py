import json

import pytest
import requests

pytest.importorskip("qrcode")

from xiaomusic.qrcode_login import MiJiaAPI


def test_mijia_retry_log_redacts_secret_query_and_keeps_structured_error(monkeypatch, caplog):
    api = MiJiaAPI(auth_data_path=".")
    api.request_retries = 2
    api.retry_backoff_seconds = 0

    def _raise_timeout(*args, **kwargs):  # noqa: ARG001
        raise requests.exceptions.Timeout("exception-secret")

    monkeypatch.setattr(requests, "get", _raise_timeout)

    with pytest.raises(ValueError) as exc:
        api._http_request(
            "get", "https://example.test/api?ticket=secret-ticket&clientSign=secret-sign#frag-secret"
        )

    payload = json.loads(str(exc.value))
    assert payload["ok"] is False
    assert payload["error"]["code"] == "E_EXTERNAL_SERVICE_UNAVAILABLE"
    assert payload["error"]["error_id"]
    assert "secret-ticket" not in caplog.text
    assert "secret-sign" not in caplog.text
    assert "frag-secret" not in caplog.text
    assert "exception-secret" not in caplog.text
    retry_records = [
        record for record in caplog.records
        if getattr(record, "event", "") == "mijia_http_retry"
    ]
    assert retry_records
    assert all(record.host == "example.test" and record.path == "/api" for record in retry_records)
    assert all(record.error == "Timeout" for record in retry_records)
    assert all(not hasattr(record, "url") for record in retry_records)


def test_mijia_malformed_service_response_does_not_expose_body():
    from types import SimpleNamespace

    api = MiJiaAPI(auth_data_path=".")
    secret = "response-body-secret"
    response = SimpleNamespace(
        status_code=200,
        text=f'&&&START&&&{{"broken":"{secret}"',
    )

    with pytest.raises(ValueError, match="invalid_auth_service_response") as exc:
        api._handle_ret(response)

    assert secret not in str(exc.value)
