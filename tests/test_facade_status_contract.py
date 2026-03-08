from __future__ import annotations

import pytest

from xiaomusic.core.errors import InvalidRequestError
from xiaomusic.playback.facade import PlaybackFacade


@pytest.mark.asyncio
async def test_facade_status_requires_device_id_string_contract() -> None:
    class _XM:
        async def get_player_status(self, did: str):
            assert did == "did-1"
            return {"status": 1}

    facade = PlaybackFacade(_XM())
    out = await facade.status("did-1")
    assert out["speaker_id"] == "did-1"


@pytest.mark.asyncio
async def test_facade_status_rejects_empty_device_id() -> None:
    class _XM:
        async def get_player_status(self, did: str):
            _ = did
            return {}

    facade = PlaybackFacade(_XM())
    with pytest.raises(InvalidRequestError):
        await facade.status("")
