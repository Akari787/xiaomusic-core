from __future__ import annotations

import time

import pytest

from xiaomusic.core.delivery.delivery_adapter import DeliveryAdapter
from xiaomusic.core.errors.stream_errors import ExpiredStreamError
from xiaomusic.core.models.media import ResolvedMedia


@pytest.mark.unit
def test_delivery_adapter_prepare_direct_http_stream():
    adapter = DeliveryAdapter(expiry_skew_seconds=5)
    media = ResolvedMedia(
        media_id="m1",
        source="direct_url",
        title="demo",
        stream_url="https://example.com/a.mp3",
        headers={"Auth": "x"},
        expires_at=None,
        is_live=False,
    )

    prepared = adapter.prepare(media)

    assert prepared.final_url == media.stream_url
    assert prepared.headers == {"Auth": "x"}
    assert prepared.source == "direct_url"


@pytest.mark.unit
def test_delivery_adapter_prepare_raises_expired_stream_error():
    adapter = DeliveryAdapter(expiry_skew_seconds=5)
    media = ResolvedMedia(
        media_id="m2",
        source="direct_url",
        title="expired",
        stream_url="https://example.com/e.mp3",
        expires_at=int(time.time()),
        is_live=False,
    )

    with pytest.raises(ExpiredStreamError):
        adapter.prepare(media)
