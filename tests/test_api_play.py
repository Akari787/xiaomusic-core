from __future__ import annotations

import pytest

from xiaomusic.api.models import PlayRequest
from xiaomusic.api.routers import v1
from xiaomusic.core.errors import (
    DeliveryPrepareError,
    DeviceNotFoundError,
    InvalidRequestError,
    SourceResolveError,
    TransportError,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source_hint",
    ["auto", "direct_url", "site_media", "jellyfin", "local_library"],
)
async def test_api_v1_play_success(monkeypatch, source_hint: str):
    calls: list[dict] = []

    class _Facade:
        async def play(self, *, device_id, query, source_hint="auto", options=None, request_id=None):  # noqa: ANN001
            calls.append(
                {
                    "device_id": device_id,
                    "query": query,
                    "source_hint": source_hint,
                    "options": options,
                    "request_id": request_id,
                }
            )
            return {
                "status": "playing",
                "device_id": device_id,
                "source_plugin": source_hint,
                "transport": "mina",
                "request_id": request_id,
                "media": {"title": "song", "stream_url": "http://a/b.mp3", "is_live": False},
                "extra": {},
            }

        async def build_player_state_snapshot(self, device_id):  # noqa: ANN001
            return {
                "device_id": device_id,
                "revision": 3,
                "play_session_id": "sess-3",
                "transport_state": "playing",
                "track": {"id": "track-1", "title": "song", "source": source_hint},
                "context": {"id": "中文", "name": "中文", "current_index": 0},
                "position_ms": 0,
                "duration_ms": 123000,
                "volume": 48,
                "snapshot_at_ms": 1710000000000,
            }

    pushed: list[str] = []

    async def _push(device_id: str):
        pushed.append(device_id)

    monkeypatch.setattr(v1, "_get_facade", lambda: _Facade())
    monkeypatch.setattr(v1, "_push_player_state_event", _push)
    out = await v1.api_v1_play(
        PlayRequest(
            device_id="did-1",
            query="http://a/b.mp3",
            source_hint=source_hint,
            options={"prefer_proxy": False},
        )
    )
    assert out["code"] == 0
    assert set(out.keys()) == {"code", "message", "data", "request_id"}
    assert out["request_id"]
    assert out["message"] == "ok"
    assert out["data"]["device_id"] == "did-1"
    assert out["data"]["source_plugin"] == source_hint
    assert out["data"]["state"]["play_session_id"] == "sess-3"
    assert out["data"]["state"]["track"]["id"] == "track-1"
    assert calls and calls[0]["source_hint"] == source_hint
    assert pushed == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc", "expected_code"),
    [
        (SourceResolveError("resolve failed"), 20002),
        (DeliveryPrepareError("prepare failed"), 30001),
        (TransportError("transport failed"), 40002),
        (DeviceNotFoundError("missing device"), 40004),
        (InvalidRequestError("invalid payload"), 40001),
    ],
)
async def test_api_v1_play_error_mapping(monkeypatch, exc: Exception, expected_code: int):
    class _Facade:
        async def play(self, *, device_id, query, source_hint="auto", options=None, request_id=None):  # noqa: ANN001
            _ = (device_id, query, source_hint, options, request_id)
            raise exc

    monkeypatch.setattr(v1, "_get_facade", lambda: _Facade())
    out = await v1.api_v1_play(PlayRequest(device_id="did-1", query="http://a/b.mp3"))
    assert out["code"] == expected_code
    assert out["request_id"]
    assert out["message"]


@pytest.mark.asyncio
async def test_api_v1_play_unknown_error_has_structured_dispatch_fallback(monkeypatch):
    class _Facade:
        async def play(self, *, device_id, query, source_hint="auto", options=None, request_id=None):  # noqa: ANN001
            _ = (device_id, query, source_hint, options, request_id)
            raise RuntimeError("boom")

    monkeypatch.setattr(v1, "_get_facade", lambda: _Facade())
    out = await v1.api_v1_play(PlayRequest(device_id="did-1", query="http://a/b.mp3"))
    assert out["code"] == 10000
    assert out["message"] == "play operation failed"
    assert out["data"]["error_code"] == "E_PLAY_OPERATION_FAILED"
    assert out["data"]["stage"] == "dispatch"


@pytest.mark.asyncio
async def test_api_v1_play_does_not_push_player_state_event(monkeypatch):
    class _Facade:
        async def play(self, *, device_id, query, source_hint="auto", options=None, request_id=None):  # noqa: ANN001
            _ = (query, source_hint, options, request_id)
            return {
                "status": "playing",
                "device_id": device_id,
                "source_plugin": "jellyfin",
                "transport": "mina",
                "request_id": request_id,
                "media": {"title": "song", "stream_url": "http://a/b.mp3", "is_live": False},
                "extra": {},
            }

        async def build_player_state_snapshot(self, device_id):  # noqa: ANN001
            return {
                "device_id": device_id,
                "revision": 2,
                "play_session_id": "sess-2",
                "transport_state": "playing",
                "track": {"id": "track-1", "title": "song", "source": "jellyfin"},
                "context": {"id": "中文", "name": "中文", "current_index": 0},
                "position_ms": 0,
                "duration_ms": 1000,
                "volume": 48,
                "snapshot_at_ms": 1710000000000,
            }

    pushed: list[str] = []

    async def _push(device_id: str):
        pushed.append(device_id)

    monkeypatch.setattr(v1, "_get_facade", lambda: _Facade())
    monkeypatch.setattr(v1, "_push_player_state_event", _push)

    out = await v1.api_v1_play(PlayRequest(device_id="did-1", query="http://a/b.mp3"))

    assert out["code"] == 0
    assert out["data"]["status"] == "playing"
    assert out["data"]["state"]["play_session_id"] == "sess-2"
    assert pushed == []
