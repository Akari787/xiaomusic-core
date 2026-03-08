from __future__ import annotations

import pytest

from xiaomusic.playback.facade import PlaybackFacade


@pytest.mark.asyncio
async def test_facade_status_uses_explicit_device_id() -> None:
    class _XM:
        async def get_player_status(self, did: str):
            assert did == "did-1"
            return {"status": 1, "play_song_detail": {"title": "hello"}}

    facade = PlaybackFacade(_XM())
    out = await facade.status("did-1")
    assert out["speaker_id"] == "did-1"
    assert out["ok"] is True
    assert out["raw"]["status"] == 1


@pytest.mark.asyncio
async def test_stop_legacy_bridges_to_new_stop_path(monkeypatch) -> None:
    class _XM:
        pass

    facade = PlaybackFacade(_XM())
    called = {"did": ""}

    async def _fake_stop(device_id: str, request_id: str | None = None):
        _ = request_id
        called["did"] = device_id
        return {
            "status": "stopped",
            "device_id": device_id,
            "transport": "mina",
            "extra": {"dispatch": {}},
        }

    monkeypatch.setattr(facade, "stop", _fake_stop)
    out = await facade.stop_legacy({"speaker_id": "did-2"})
    assert called["did"] == "did-2"
    assert out["ok"] is True
    assert out["raw"]["ret"] == "OK"
