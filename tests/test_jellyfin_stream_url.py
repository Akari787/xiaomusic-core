from xiaomusic.jellyfin_client import JellyfinClient


def test_jellyfin_stream_url_query_format():
    url = JellyfinClient.build_stream_url("http://server", "abc123", "key123")
    assert "api_key=key123" in url
    assert "+api_key" not in url
    assert "&" not in url.split("?", 1)[1]
