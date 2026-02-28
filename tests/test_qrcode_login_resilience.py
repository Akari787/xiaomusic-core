import json

import pytest
import requests

pytest.importorskip("qrcode")

from xiaomusic.qrcode_login import MiJiaAPI


def test_mijia_http_request_retries_and_returns_structured_error(monkeypatch):
    api = MiJiaAPI(auth_data_path=".")
    api.request_retries = 2
    api.retry_backoff_seconds = 0

    def _raise_timeout(*args, **kwargs):  # noqa: ARG001
        raise requests.exceptions.Timeout("timeout")

    monkeypatch.setattr(requests, "get", _raise_timeout)

    with pytest.raises(ValueError) as exc:
        api._http_request("get", "https://example.test/api")

    payload = json.loads(str(exc.value))
    assert payload["ok"] is False
    assert payload["error"]["code"] == "E_EXTERNAL_SERVICE_UNAVAILABLE"
    assert payload["error"]["error_id"]
