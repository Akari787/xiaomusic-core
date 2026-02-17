import pytest


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
    from xiaomusic.m1.url_classifier import UrlClassifier  # noqa: PLC0415

    info = UrlClassifier().classify(raw_url)

    assert info.site == site
    assert info.kind_hint == kind_hint
    assert info.normalized_url == normalized
    assert info.original_url == raw_url
