import pytest

from xiaomusic.relay.url_classifier import UrlClassifier


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw_url", "site", "kind_hint", "normalized"),
    [
        (
            "https://www.youtube.com/watch?v=iPnaF8Ngk3Q&t=10",
            "youtube",
            "vod",
            "https://www.youtube.com/watch?v=iPnaF8Ngk3Q",
        ),
        (
            "https://youtu.be/iPnaF8Ngk3Q?si=abc",
            "youtube",
            "vod",
            "https://www.youtube.com/watch?v=iPnaF8Ngk3Q",
        ),
        (
            "https://m.youtube.com/watch?v=iPnaF8Ngk3Q&feature=share",
            "youtube",
            "vod",
            "https://www.youtube.com/watch?v=iPnaF8Ngk3Q",
        ),
        (
            "https://www.youtube.com/watch?v=vNG3-GRjrAo",
            "youtube",
            "live",
            "https://www.youtube.com/watch?v=vNG3-GRjrAo",
        ),
        (
            "https://live.bilibili.com/12345?broadcast_type=0",
            "bilibili",
            "live",
            "https://live.bilibili.com/12345",
        ),
        (
            "https://www.bilibili.com/video/BV14EcazWEna/?p=2",
            "bilibili",
            "vod",
            "https://www.bilibili.com/video/BV14EcazWEna",
        ),
        (
            "https://example.com/video?id=1",
            "unknown",
            "unknown",
            "https://example.com/video?id=1",
        ),
    ],
)
def test_url_classifier_cases(raw_url, site, kind_hint, normalized):
    info = UrlClassifier().classify(raw_url)

    assert info.site == site
    assert info.kind_hint == kind_hint
    assert info.normalized_url == normalized
    assert info.original_url == raw_url


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw_url",
    [
        "http://192.168.7.4:30013/Audio/aa05e8ae29761e44e505f2a9b1816eb8/stream.mp3?api_key=demo",
        "http://192.168.7.4:30013/Audio/aa05e8ae29761e44e505f2a9b1816eb8/stream?static=true",
    ],
)
def test_url_classifier_recognizes_jellyfin_audio_stream_urls(raw_url):
    info = UrlClassifier(jellyfin_base_url="http://192.168.7.4:30013").classify(raw_url)

    assert info.site == "jellyfin"
    assert info.kind_hint == "audio_stream"
    assert info.normalized_url == raw_url


@pytest.mark.unit
def test_url_classifier_does_not_mark_non_matching_host_as_jellyfin():
    info = UrlClassifier(jellyfin_base_url="http://192.168.7.4:30013").classify(
        "http://192.168.7.99:30013/Audio/aa05e8ae29761e44e505f2a9b1816eb8/stream.mp3?api_key=demo"
    )

    assert info.site == "unknown"
