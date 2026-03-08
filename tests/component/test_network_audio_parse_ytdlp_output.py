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
    from xiaomusic.network_audio.ytdlp_parser import parse_ytdlp_output  # noqa: PLC0415

    p = Path("tests/fixtures/network_audio") / fixture_name
    payload = json.loads(p.read_text(encoding="utf-8"))

    result = parse_ytdlp_output(payload)

    assert result.ok is True
    assert result.source_url.startswith("https://")
    assert result.title
    assert result.is_live is expected_live
    assert result.container_hint == expected_ext
    assert result.error_code is None


@pytest.mark.component
def test_ct1_0_parse_ytdlp_output_fallback_to_requested_formats_audio_url():
    from xiaomusic.network_audio.ytdlp_parser import parse_ytdlp_output  # noqa: PLC0415

    payload = {
        "id": "iPnaF8Ngk3Q",
        "title": "YT VOD Missing top-level url",
        "is_live": False,
        "ext": "webm",
        "requested_formats": [
            {
                "format_id": "137",
                "vcodec": "avc1",
                "acodec": "none",
                "url": "https://cdn.example.local/video-only.m4v",
                "ext": "mp4",
            },
            {
                "format_id": "251",
                "vcodec": "none",
                "acodec": "opus",
                "url": "https://cdn.example.local/audio-only.webm",
                "ext": "webm",
            },
        ],
    }
    result = parse_ytdlp_output(payload)

    assert result.ok is True
    assert result.source_url == "https://cdn.example.local/audio-only.webm"
    assert result.container_hint == "webm"


@pytest.mark.component
def test_ct1_0_parse_ytdlp_output_live_youtube_prefers_non_manifest_audio_url():
    from xiaomusic.network_audio.ytdlp_parser import parse_ytdlp_output  # noqa: PLC0415

    payload = {
        "id": "7CQSzyi8UJE",
        "title": "YT LIVE",
        "is_live": True,
        "url": "https://manifest.googlevideo.com/api/manifest/hls_playlist/.../playlist/index.m3u8",
        "webpage_url": "https://www.youtube.com/watch?v=7CQSzyi8UJE",
        "formats": [
            {
                "format_id": "251",
                "vcodec": "none",
                "acodec": "opus",
                "url": "https://rr5---sn-aj3pm5-5p.googlevideo.com/videoplayback?itag=251",
                "ext": "webm",
            }
        ],
    }

    result = parse_ytdlp_output(payload)

    assert result.ok is True
    assert result.source_url.startswith("https://rr5---sn-aj3pm5-5p.googlevideo.com/videoplayback")
