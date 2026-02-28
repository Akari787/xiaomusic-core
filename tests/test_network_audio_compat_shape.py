import pytest

pytest.importorskip("aiofiles")

from xiaomusic.api.models import DidUrl, SidObj
from xiaomusic.api.routers import network_audio


@pytest.mark.asyncio
async def test_network_audio_play_link_compat_shape(monkeypatch):
    monkeypatch.setattr(network_audio.xiaomusic, "did_exist", lambda did: True)

    class _Facade:
        async def play_url(self, url, speaker_id, options):  # noqa: ARG002
            return {
                "ok": True,
                "sid": "s_2",
                "speaker_id": speaker_id,
                "state": "running",
                "title": "song",
                "stream_url": url,
                "error_code": None,
            }

    monkeypatch.setattr(network_audio, "_get_facade", lambda: _Facade())

    out = await network_audio.network_audio_play_link(DidUrl(did="did-1", url="http://a/b.mp3"))
    assert out["ok"] is True
    assert out["success"] is True
    assert out["deprecated"] is True
    assert out["sid"] == "s_2"
    assert out["speaker_id"] == "did-1"
    assert out["state"] == "running"
    assert "message" in out


@pytest.mark.asyncio
async def test_network_audio_stop_compat_shape(monkeypatch):
    class _Facade:
        async def stop(self, target):  # noqa: ARG002
            return {
                "ok": False,
                "sid": "s_2",
                "speaker_id": "did-1",
                "state": "error",
                "stream_url": "",
                "error_code": "E_STREAM_NOT_FOUND",
            }

    monkeypatch.setattr(network_audio, "_get_facade", lambda: _Facade())
    out = await network_audio.network_audio_stop(SidObj(sid="s_2"))
    assert out["ok"] is False
    assert out["success"] is False
    assert out["error_code"] == "E_STREAM_NOT_FOUND"
    assert out["message"]
    assert out["deprecated"] is True
