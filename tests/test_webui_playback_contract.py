from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import ValidationError

from xiaomusic.api.app import _handle_validation_exception
from xiaomusic.api.models.play_request import PlayRequest
from xiaomusic.api.routers import v1

_FIXTURE = Path(__file__).parents[1] / "xiaomusic" / "webui" / "src" / "services" / "playback_contract_fixtures.json"


def _fixtures() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _client() -> TestClient:
    app = FastAPI()
    app.add_exception_handler(RequestValidationError, _handle_validation_exception)
    app.include_router(v1.router)
    return TestClient(app)


def test_real_frontend_fixture_payloads_are_accepted_by_real_router(monkeypatch) -> None:
    class _Facade:
        async def play(self, **kwargs):
            return {"status": "accepted", "device_id": kwargs["device_id"]}

        async def build_player_state_snapshot(self, device_id: str):
            return None

    monkeypatch.setattr(v1, "_get_facade", lambda: _Facade())
    for payload in _fixtures().values():
        response = _client().post("/api/v1/play", json=payload)
        assert response.status_code != 422, (payload, response.text)


def test_unknown_options_remain_strictly_rejected() -> None:
    try:
        PlayRequest.model_validate(
            {
                "device_id": "did-1",
                "query": "https://example.com/a.mp3",
                "options": {"prefer_codec": "auto"},
            }
        )
    except ValidationError as exc:
        assert "prefer_codec" in str(exc)
    else:  # pragma: no cover - regression guard
        raise AssertionError("unknown play option was accepted")


def test_full_fastapi_validation_returns_structured_contract_error(monkeypatch) -> None:
    monkeypatch.setattr(v1, "_get_facade", lambda: None)
    response = _client().post(
        "/api/v1/play",
        json={
            "device_id": "did-1",
            "query": "https://example.com/a.mp3",
            "source_hint": "auto",
            "options": {"prefer_codec": "auto"},
        },
    )
    assert response.status_code == 422
    body = response.json()
    assert body["data"]["error_code"] == "E_INVALID_REQUEST"
    assert body["data"]["stage"] == "request"
    assert body["request_id"]


def test_search_response_never_trusts_or_exposes_plugin_fields(monkeypatch) -> None:
    raw = {
        "name": "Song A",
        "title": "Song A",
        "artist": "Artist A",
        "platform": "qq",
        "id": "media-a",
        "url": "https://cdn.example.invalid/media.mp3",
        "cookie": "SECRET_COOKIE",
        "token": "SECRET_TOKEN",
        "source_payload": {"secret": "value"},
        "context_hint": {"secret": "value"},
        "play_reference": {"query": "https://signed.invalid/x", "source_hint": "direct_url"},
    }

    class _PluginManager:
        @staticmethod
        def get_enabled_plugins():
            return ["qq"]

    class _XM:
        js_plugin_manager = _PluginManager()

        @staticmethod
        async def get_music_list_online(keyword: str, plugin: str, page: int, limit: int):
            return {"success": True, "data": [raw], "total": 1}

    monkeypatch.setattr(v1, "_get_xiaomusic", lambda: _XM())
    body = _client().get("/api/v1/search/online", params={"keyword": "love"}).json()
    serialized = json.dumps(body, ensure_ascii=False)
    item = body["data"]["items"][0]
    assert item["play_reference"]["source_hint"] == "online_plugin"
    assert item["play_reference"]["media_id"].startswith("opm_")
    assert "media-a" not in serialized
    assert item["play_reference"]["title"] == "Song A"
    assert item["play_reference"]["query"].startswith("opq_")
    assert raw["url"] not in serialized
    for secret in ("SECRET_COOKIE", "SECRET_TOKEN", "source_payload", "context_hint", "signed.invalid"):
        assert secret not in serialized
