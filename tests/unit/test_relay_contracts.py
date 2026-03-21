import json
from dataclasses import asdict
from pathlib import Path

import pytest


@pytest.mark.unit
def test_contract_examples_match_model_definitions():
    from xiaomusic.relay.contracts import (  # noqa: PLC0415
        ERROR_CODES,
        Event,
        ResolveResult,
        Session,
        UrlInfo,
    )

    examples_path = Path("docs/dev/relay/contracts.examples.json")
    assert examples_path.exists(), "missing docs/dev/relay/contracts.examples.json"

    payload = json.loads(examples_path.read_text(encoding="utf-8"))

    assert payload["models"]["UrlInfo"].keys() == asdict(UrlInfo.sample()).keys()
    assert payload["models"]["ResolveResult"].keys() == asdict(ResolveResult.sample()).keys()
    assert payload["models"]["Session"].keys() == asdict(Session.sample()).keys()
    assert payload["models"]["Event"].keys() == asdict(Event.sample()).keys()

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

    assert must_have.issubset(set(ERROR_CODES.keys()))
    assert must_have.issubset(set(payload["error_codes"].keys()))
