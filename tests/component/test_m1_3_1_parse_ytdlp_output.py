import json
from pathlib import Path

import pytest


@pytest.mark.component
@pytest.mark.parametrize(
    ("fixture_name", "expected_live", "expected_ext"),
    [
        ("ytdlp_youtube_vod.json", False, "m4a"),
        ("ytdlp_youtube_live.json", True, "m3u8"),
        ("ytdlp_bilibili_live.json", True, "flv"),
    ],
)
def test_ct1_0_parse_ytdlp_output_fixture(fixture_name, expected_live, expected_ext):
    from xiaomusic.m1.ytdlp_parser import parse_ytdlp_output  # noqa: PLC0415

    p = Path("tests/fixtures/m1") / fixture_name
    payload = json.loads(p.read_text(encoding="utf-8"))

    result = parse_ytdlp_output(payload)

    assert result.ok is True
    assert result.source_url.startswith("https://")
    assert result.title
    assert result.is_live is expected_live
    assert result.container_hint == expected_ext
    assert result.error_code is None
