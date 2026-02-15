import asyncio

import pytest


@pytest.mark.asyncio
async def test_play_failure_backoff_and_degrade(monkeypatch):
    from xiaomusic.device_player import XiaoMusicDevice

    class Dummy:
        def __init__(self):
            self._play_fail_first_ts = 0.0
            self._play_fail_last_reason = ""
            self._play_failed_cnt = 0
            self._degraded = False
            self._degraded_notified = False
            self._play_session_id = 1
            self.is_playing = True
            self._last_cmd = "play"
            self.next_calls = 0
            self.tts_calls = 0

        async def _play_next(self):
            self.next_calls += 1

        async def do_tts(self, msg):
            self.tts_calls += 1

    d = Dummy()

    sleeps: list[float] = []

    async def fake_sleep(sec):
        sleeps.append(float(sec))
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    # First 4 failures schedule retries with exponential backoff.
    for i in range(4):
        await XiaoMusicDevice._handle_play_failure(d, name="x", sid=1, reason="r")
    # Allow scheduled tasks to run.
    await asyncio.sleep(0)
    # 5th failure should degrade and stop auto-next.
    await XiaoMusicDevice._handle_play_failure(d, name="x", sid=1, reason="r")

    assert d._degraded is True
    assert d.tts_calls == 1
    # Backoff sequence should start at 1s and grow.
    assert sleeps[0] == 1.0
    assert sleeps[1] == 2.0
    assert sleeps[2] == 4.0
    assert sleeps[3] == 8.0
