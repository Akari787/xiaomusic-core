import pytest


class _MockMiAccepted:
    async def play_by_music_url(self, device_id, url, audio_id=None):  # noqa: ARG002
        return {"code": 0, "message": "ok"}


class _MockMiRejected:
    async def play_by_music_url(self, device_id, url, audio_id=None):  # noqa: ARG002
        return {"code": 1, "message": "failed"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ut6_0_xiaomi_adapter_contract_accepts():
    from xiaomusic.network_audio.xiaomi_adapter import XiaomiPlaybackAdapter  # noqa: PLC0415

    adapter = XiaomiPlaybackAdapter(mina_service=_MockMiAccepted())
    out = await adapter.play(speaker_id="981257654", stream_url="http://127.0.0.1:18090/stream/s_1")
    assert out["accepted"] is True
    assert out["error_code"] is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ut6_0_xiaomi_adapter_contract_rejects_with_error_code():
    from xiaomusic.network_audio.xiaomi_adapter import XiaomiPlaybackAdapter  # noqa: PLC0415

    adapter = XiaomiPlaybackAdapter(mina_service=_MockMiRejected())
    out = await adapter.play(speaker_id="981257654", stream_url="http://127.0.0.1:18090/stream/s_1")
    assert out["accepted"] is False
    assert out["error_code"] == "E_XIAOMI_PLAY_FAILED"
