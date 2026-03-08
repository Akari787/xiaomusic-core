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
def test_delivery_adapter_prepare_plan_direct_then_proxy_for_site_media():
    adapter = DeliveryAdapter(expiry_skew_seconds=5, proxy_url_builder=lambda url, name: f"http://127.0.0.1:58090/proxy?u={name}")
    media = ResolvedMedia(
        media_id="m3",
        source="site_media",
        title="yt",
        stream_url="https://googlevideo.example/videoplayback",
        headers={},
        expires_at=None,
        is_live=False,
    )

    plan = adapter.prepare_plan(media, context={"prefer_proxy": False})

    assert plan.strategy == "direct_then_proxy"
    assert plan.primary.is_proxy is False
    assert plan.fallback is not None
    assert plan.fallback.is_proxy is True


@pytest.mark.unit
def test_delivery_adapter_prepare_plan_proxy_first_when_prefer_proxy_enabled():
    adapter = DeliveryAdapter(expiry_skew_seconds=5, proxy_url_builder=lambda url, name: f"http://127.0.0.1:58090/proxy?name={name}")
    media = ResolvedMedia(
        media_id="m4",
        source="jellyfin",
        title="movie",
        stream_url="http://192.168.7.4:30013/Audio/id/stream.mp3",
        headers={},
        expires_at=None,
        is_live=False,
    )

    plan = adapter.prepare_plan(media, context={"prefer_proxy": True})

    assert plan.strategy == "proxy_first"
    assert plan.primary.is_proxy is True
    assert plan.fallback is not None
    assert plan.fallback.is_proxy is False


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
