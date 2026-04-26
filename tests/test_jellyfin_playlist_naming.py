from types import SimpleNamespace

from xiaomusic.jellyfin_client import JellyfinClient


def _client() -> JellyfinClient:
    config = SimpleNamespace(
        jellyfin_enabled=True,
        jellyfin_base_url="http://server",
        jellyfin_api_key="key123",
    )
    log = SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None, error=lambda *a, **k: None)
    return JellyfinClient(config, log)


def _item(item_id: str, title: str = "Song A", artist: str = "Artist A") -> dict:
    return {
        "Id": item_id,
        "Name": title,
        "Artists": [artist],
        "RunTimeTicks": 10000000,
        "MediaSources": [],
        "Container": "mp3",
    }


def test_same_jellyfin_item_reuses_same_name_across_playlists():
    client = _client()
    item_id_to_name = {}
    name_to_item_id = {}

    first = client._convert_audio_items_to_musics([_item("abc123")], item_id_to_name, name_to_item_id, "user-1")
    second = client._convert_audio_items_to_musics([_item("abc123")], item_id_to_name, name_to_item_id, "user-1")

    assert first[0]["name"] == "Song A-Artist A"
    assert second[0]["name"] == "Song A-Artist A"
    assert item_id_to_name == {"abc123": "Song A-Artist A"}
    assert name_to_item_id == {"Song A-Artist A": "abc123"}


def test_different_jellyfin_items_with_same_base_name_get_distinct_names():
    client = _client()
    item_id_to_name = {}
    name_to_item_id = {}

    first = client._convert_audio_items_to_musics([_item("abc123")], item_id_to_name, name_to_item_id, "user-1")
    second = client._convert_audio_items_to_musics([_item("def456")], item_id_to_name, name_to_item_id, "user-1")

    assert first[0]["name"] == "Song A-Artist A"
    assert second[0]["name"] == "Song A-Artist A-[def456]"
    assert item_id_to_name == {
        "abc123": "Song A-Artist A",
        "def456": "Song A-Artist A-[def456]",
    }
