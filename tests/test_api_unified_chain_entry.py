from __future__ import annotations

import pytest

pytest.importorskip("aiofiles")

from xiaomusic.api.models import ApiV1PlayUrlRequest
from xiaomusic.api.routers import music, v1
from xiaomusic.playback.facade import PlaybackFacade


class _CoordinatorStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str | None, str]] = []

    async def play(self, request, device_id=None):  # noqa: ANN001
        self.calls.append((request.source_hint, request.query))
        return {
            "ok": True,
            "request_id": request.request_id,
            "transport": "mina",
            "prepared_stream": type("P", (), {"final_url": request.query, "source": request.source_hint or "unknown"})(),
            "resolved_media": type("R", (), {"title": "ok"})(),
            "dispatch": type("D", (), {"transport": "mina", "data": {"ret": "OK"}})(),
        }


class _FakeRequest:
    def __init__(self, payload: dict):
        self._payload = payload

    async def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_api_v1_play_url_enters_playback_coordinator_for_http_and_network_audio(monkeypatch):
    coordinator = _CoordinatorStub()
    xiaomusic = type("X", (), {"link_playback_strategy": None})()
    facade = PlaybackFacade(xiaomusic)
    facade._core_coordinator = coordinator

    class _Strategy:
        def should_use_network_audio(self, url: str) -> bool:
            return "youtube.com" in url

    facade.xiaomusic.link_playback_strategy = _Strategy()
    monkeypatch.setattr(v1, "_get_facade", lambda: facade)

    await v1.api_v1_play_url(ApiV1PlayUrlRequest(url="http://example.com/a.mp3", speaker_id="d1"))
    await v1.api_v1_play_url(
        ApiV1PlayUrlRequest(url="https://www.youtube.com/watch?v=iPnaF8Ngk3Q", speaker_id="d1")
    )

    assert coordinator.calls[0][0] == "http_url"
    assert coordinator.calls[1][0] == "network_audio"


@pytest.mark.asyncio
async def test_api_pushurl_enters_playback_coordinator_for_jellyfin(monkeypatch):
    coordinator = _CoordinatorStub()
    xiaomusic = type("X", (), {"link_playback_strategy": None})()
    facade = PlaybackFacade(xiaomusic)
    facade._core_coordinator = coordinator
    monkeypatch.setattr(music, "_get_facade", lambda: facade)

    req = _FakeRequest(
        {
            "did": "d1",
            "source": "jellyfin",
            "title": "jf",
            "url": "http://192.168.7.4:30013/Audio/id/stream.mp3",
        }
    )
    out = await music.device_push_url(req)

    assert out["ok"] is True
    assert coordinator.calls[0][0] == "jellyfin"
