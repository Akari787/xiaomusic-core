from dataclasses import asdict

import pytest

from xiaomusic.relay.contracts import (
    ERROR_CODES,
    SESSION_STATES,
    Event,
    ResolveResult,
    Session,
    UrlInfo,
)


@pytest.mark.unit
def test_contract_samples_are_self_consistent():
    url = asdict(UrlInfo.sample())
    resolved = asdict(ResolveResult.sample())
    session = asdict(Session.sample())
    event = asdict(Event.sample())

    assert url["site"] and url["normalized_url"].startswith("http")
    assert resolved["ok"] is True and resolved["source_url"].startswith("http")
    assert session["state"] in SESSION_STATES and session["sid"]
    assert event["type"] and event["level"] in {"debug", "info", "warning", "error"}

    must_have = {
        "E_URL_UNSUPPORTED",
        "E_RESOLVE_TIMEOUT",
        "E_RESOLVE_NONZERO_EXIT",
        "E_STREAM_START_FAILED",
        "E_STREAM_NOT_FOUND",
        "E_STREAM_SINGLE_CLIENT_ONLY",
        "E_XIAOMI_PLAY_FAILED",
        "E_INTERNAL",
    }
    assert must_have.issubset(ERROR_CODES)
