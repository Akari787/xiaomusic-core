from __future__ import annotations

from xiaomusic.api.models.play_request import PlayRequest, ResolveRequest
from xiaomusic.constants.api_fields import DEVICE_ID, OPTIONS, QUERY, REQUEST_ID, SOURCE_HINT
from xiaomusic.core.models.payload_keys import (
    KEY_DEVICE_ID,
    KEY_OPTIONS,
    KEY_QUERY,
    KEY_REQUEST_ID,
    KEY_SOURCE_HINT,
)


def test_api_field_constants_values_are_stable() -> None:
    assert DEVICE_ID == "device_id"
    assert QUERY == "query"
    assert SOURCE_HINT == "source_hint"
    assert OPTIONS == "options"
    assert REQUEST_ID == "request_id"


def test_payload_keys_reuse_shared_api_field_constants() -> None:
    assert KEY_DEVICE_ID == DEVICE_ID
    assert KEY_QUERY == QUERY
    assert KEY_SOURCE_HINT == SOURCE_HINT
    assert KEY_OPTIONS == OPTIONS
    assert KEY_REQUEST_ID == REQUEST_ID


def test_request_models_accept_payload_with_shared_field_names() -> None:
    play = PlayRequest.model_validate(
        {
            DEVICE_ID: "did-1",
            QUERY: "https://example.com/song.mp3",
            SOURCE_HINT: "auto",
            OPTIONS: {"timeout": 5},
            REQUEST_ID: "rid-1",
        }
    )
    assert play.device_id == "did-1"
    assert play.query.endswith("song.mp3")
    assert play.options == {"timeout": 5}
    assert play.request_id == "rid-1"

    resolve = ResolveRequest.model_validate(
        {
            QUERY: "keyword",
            SOURCE_HINT: "site_media",
            OPTIONS: {"no_cache": True},
        }
    )
    assert resolve.query == "keyword"
    assert resolve.source_hint == "site_media"
