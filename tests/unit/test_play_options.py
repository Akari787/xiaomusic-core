from __future__ import annotations

from xiaomusic.core.models.media import MediaRequest, PlayOptions
from xiaomusic.core.models.payload_keys import (
    OPT_CONFIRM_START,
    OPT_CONFIRM_START_DELAY_MS,
    OPT_CONFIRM_START_INTERVAL_MS,
    OPT_CONFIRM_START_RETRIES,
    OPT_LOOP,
    OPT_NO_CACHE,
    OPT_PREFER_PROXY,
    OPT_RESOLVE_TIMEOUT_SECONDS,
    OPT_SHUFFLE,
    OPT_SOURCE_PAYLOAD,
    OPT_START_POSITION,
    OPT_TIMEOUT,
    OPT_TITLE,
    OPT_VOLUME,
    PAYLOAD_ID,
    PAYLOAD_SOURCE,
    PAYLOAD_TITLE,
    PAYLOAD_URL,
)


def test_play_options_defaults_from_empty_payload() -> None:
    opts = PlayOptions.from_payload(None)
    assert opts.start_position == 0
    assert opts.shuffle is False
    assert opts.loop is False
    assert opts.volume is None
    assert opts.timeout is None
    assert opts.no_cache is False
    assert opts.prefer_proxy is False
    assert opts.confirm_start is True
    assert opts.confirm_start_delay_ms == 1200
    assert opts.confirm_start_retries == 2
    assert opts.confirm_start_interval_ms == 600
    assert opts.resolve_timeout_seconds is None


def test_play_options_from_legacy_payload_normalizes_values() -> None:
    opts = PlayOptions.from_payload(
        {
            "no_cache": "true",
            "prefer_proxy": "1",
            "confirm_start": "false",
            "confirm_start_delay_ms": "0",
            "confirm_start_retries": "-3",
            "confirm_start_interval_ms": "50",
            "resolve_timeout_seconds": "15",
            "start_position": "30",
            "shuffle": "true",
            "loop": 1,
            "volume": "120",
            "timeout": "20",
            "id": "legacy-id",
            "title": "Legacy Title",
        }
    )
    assert opts.start_position == 30
    assert opts.shuffle is True
    assert opts.loop is True
    assert opts.volume == 100
    assert opts.timeout == 20
    assert opts.no_cache is True
    assert opts.prefer_proxy is True
    assert opts.confirm_start is False
    assert opts.confirm_start_delay_ms == 0
    assert opts.confirm_start_retries == 0
    assert opts.confirm_start_interval_ms == 100
    assert opts.resolve_timeout_seconds == 15
    assert opts.media_id == "legacy-id"
    assert opts.title == "Legacy Title"


def test_media_request_from_payload_builds_jellyfin_context() -> None:
    opts = PlayOptions.from_payload({"title": "My Song", "id": "jf-1"})
    req = MediaRequest.from_payload(
        request_id="rid-1",
        query="http://example.com/stream.mp3",
        source_hint="jellyfin",
        device_id="did-1",
        options=opts,
        include_prefer_proxy=True,
    )
    assert req.context[OPT_RESOLVE_TIMEOUT_SECONDS] == 8
    assert req.context[OPT_START_POSITION] == 0
    assert req.context[OPT_SHUFFLE] is False
    assert req.context[OPT_LOOP] is False
    assert req.context[OPT_NO_CACHE] is False
    assert req.context[OPT_PREFER_PROXY] is False
    assert req.context[OPT_CONFIRM_START] is True
    assert req.context[OPT_CONFIRM_START_DELAY_MS] == 1200
    assert req.context[OPT_CONFIRM_START_RETRIES] == 2
    assert req.context[OPT_CONFIRM_START_INTERVAL_MS] == 600
    payload = req.context[OPT_SOURCE_PAYLOAD]
    assert payload[PAYLOAD_SOURCE] == "jellyfin"
    assert payload[PAYLOAD_URL] == "http://example.com/stream.mp3"
    assert payload[PAYLOAD_ID] == "jf-1"
    assert payload[PAYLOAD_TITLE] == "My Song"
    assert req.context[OPT_TITLE] == "My Song"


def test_media_request_from_payload_keeps_volume_and_timeout_context() -> None:
    opts = PlayOptions.from_payload({"volume": 35, "timeout": 9, "start_position": 12})
    req = MediaRequest.from_payload(
        request_id="rid-2",
        query="https://example.com/a.mp3",
        source_hint="direct_url",
        device_id="did-2",
        options=opts,
        include_prefer_proxy=True,
    )
    assert req.context[OPT_VOLUME] == 35
    assert req.context[OPT_TIMEOUT] == 9
    assert req.context[OPT_START_POSITION] == 12


def test_media_request_from_payload_keeps_title_for_auto_url_route() -> None:
    opts = PlayOptions.from_payload({"title": "慢慢懂-汪苏泷"})
    req = MediaRequest.from_payload(
        request_id="rid-3",
        query="http://192.168.7.4:30013/Audio/id/stream.mp3?api_key=demo",
        source_hint="auto",
        device_id="did-3",
        options=opts,
        include_prefer_proxy=True,
    )

    assert req.context[OPT_TITLE] == "慢慢懂-汪苏泷"
