import asyncio

import pytest


@pytest.mark.asyncio
async def test_file_refresh_coalesces(monkeypatch):
    class DummyMusicLib:
        def __init__(self):
            self.calls = 0

        def gen_all_music_list(self):
            self.calls += 1

    class Dummy:
        def __init__(self):
            self.music_library = DummyMusicLib()
            self.update_calls = 0
            self._library_refresh_task = None
            self._library_refresh_pending = False

        def update_all_playlist(self):
            self.update_calls += 1

        def _queue_library_refresh(self, reason: str = ""):
            from xiaomusic.xiaomusic import XiaoMusic

            return XiaoMusic._queue_library_refresh(self, reason)

    d = Dummy()

    async def fake_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)

    # Queue multiple times quickly; should run once.
    d._queue_library_refresh("t")
    d._queue_library_refresh("t")
    d._queue_library_refresh("t")

    # Let task run.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert d.music_library.calls == 1
    assert d.update_calls == 1
