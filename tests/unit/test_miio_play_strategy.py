from __future__ import annotations

from types import SimpleNamespace

import pytest

from xiaomusic.adapters.miio.miio_transport import MiioTransport
from xiaomusic.core.errors.transport_errors import TransportError
from xiaomusic.core.models.media import PreparedStream


@pytest.mark.unit
@pytest.mark.asyncio
async def test_miio_play_url_is_explicitly_unsupported_in_phase3():
    transport = MiioTransport(SimpleNamespace())

    with pytest.raises(TransportError):
        await transport.play_url(
            "d1",
            PreparedStream(final_url="https://example.com/a.mp3", source="http_url"),
        )
