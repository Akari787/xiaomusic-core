from __future__ import annotations

import pytest
from typing import Any, cast

pytest.importorskip("aiofiles")

from xiaomusic.api.models import ApiV1PlayUrlRequest, ApiV1SetVolumeRequest, ApiV1StopRequest, ApiV1TtsRequest
from xiaomusic.api.routers import music, v1
from xiaomusic.playback.facade import PlaybackFacade


class _CoordinatorStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str | None, str]] = []
        self.control_calls: list[tuple[str, str]] = []

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

    async def stop(self, device_id: str):
        self.control_calls.append(("stop", device_id))
        return {"ok": True, "transport": "miio", "dispatch": type("D", (), {"data": {"ret": "OK"}})()}

    async def tts(self, device_id: str, text: str):
        _ = text
        self.control_calls.append(("tts", device_id))
        return {"ok": True, "transport": "miio", "dispatch": type("D", (), {"data": {"ret": "OK"}})()}

    async def set_volume(self, device_id: str, volume: int):
        _ = volume
        self.control_calls.append(("volume", device_id))
        return {"ok": True, "transport": "miio", "dispatch": type("D", (), {"data": {"ret": "OK"}})()}


class _FakeRequest:
    def __init__(self, payload: dict):
        self._payload = payload

    async def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_api_v1_play_url_enters_playback_coordinator_for_direct_and_site_media(monkeypatch):
    coordinator = _CoordinatorStub()
    xiaomusic = type("X", (), {"link_playback_strategy": None})()
    facade = PlaybackFacade(xiaomusic)
    facade._core_coordinator = cast(Any, coordinator)

    class _Strategy:
        def should_use_network_audio(self, url: str) -> bool:
            return "youtube.com" in url

    facade.xiaomusic.link_playback_strategy = _Strategy()
    monkeypatch.setattr(v1, "_get_facade", lambda: facade)

    await v1.api_v1_play_url(ApiV1PlayUrlRequest(url="http://example.com/a.mp3", speaker_id="d1"))
    await v1.api_v1_play_url(
        ApiV1PlayUrlRequest(url="https://www.youtube.com/watch?v=iPnaF8Ngk3Q", speaker_id="d1")
    )

    assert coordinator.calls[0][0] == "direct_url"
    assert coordinator.calls[1][0] == "site_media"


@pytest.mark.asyncio
async def test_api_pushurl_enters_playback_coordinator_for_jellyfin(monkeypatch):
    coordinator = _CoordinatorStub()
    xiaomusic = type("X", (), {"link_playback_strategy": None})()
    facade = PlaybackFacade(xiaomusic)
    facade._core_coordinator = cast(Any, coordinator)
    monkeypatch.setattr(music, "_get_facade", lambda: facade)

    req = _FakeRequest(
        {
            "did": "d1",
            "source": "jellyfin",
            "title": "jf",
            "url": "http://192.168.7.4:30013/Audio/id/stream.mp3",
        }
    )
    out = cast(Any, await music.device_push_url(cast(Any, req)))

    assert out["code"] == 0
    assert coordinator.calls[0][0] == "jellyfin"


@pytest.mark.asyncio
async def test_api_v1_control_actions_enter_playback_coordinator(monkeypatch):
    coordinator = _CoordinatorStub()
    xiaomusic = type("X", (), {"link_playback_strategy": None})()
    facade = PlaybackFacade(xiaomusic)
    facade._core_coordinator = cast(Any, coordinator)
    monkeypatch.setattr(v1, "_get_facade", lambda: facade)

    out_tts = await v1.api_v1_tts(ApiV1TtsRequest(speaker_id="d1", text="hello"))
    out_stop = await v1.api_v1_stop(ApiV1StopRequest(speaker_id="d1"))
    out_volume = await v1.api_v1_set_volume(ApiV1SetVolumeRequest(speaker_id="d1", volume=20))

    assert out_tts["code"] == 0
    assert out_stop["code"] == 0
    assert out_volume["code"] == 0
    assert ("tts", "d1") in coordinator.control_calls
    assert ("stop", "d1") in coordinator.control_calls
    assert ("volume", "d1") in coordinator.control_calls
