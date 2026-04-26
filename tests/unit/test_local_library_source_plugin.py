from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from xiaomusic.adapters.sources.local_library_source_plugin import LocalLibrarySourcePlugin
from xiaomusic.core.errors.source_errors import SourceResolveError
from xiaomusic.core.models.media import MediaRequest


class _LocalLibraryStub:
    def __init__(self, track_name: str, file_path: str) -> None:
        self.all_music = {track_name: file_path}
        self._track_name = track_name

    def is_web_music(self, name: str) -> bool:
        _ = name
        return False

    def get_filename(self, name: str) -> str:
        return self.all_music.get(name, "")

    def _get_file_url(self, filepath: str) -> str:
        return f"http://127.0.0.1:58090/music/{Path(filepath).name}"

    def searchmusic(self, query: str):
        if query in self.all_music:
            return [query]
        return [self._track_name] if query in self._track_name else []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_local_library_source_plugin_resolve_success_by_name_and_path():
    with TemporaryDirectory() as tmp_dir:
        p = Path(tmp_dir) / "song.mp3"
        p.write_bytes(b"demo")
        plugin = LocalLibrarySourcePlugin(_LocalLibraryStub("song", str(p)))

        by_name = await plugin.resolve(MediaRequest(request_id="r1", source_hint="local_library", query="song"))
        by_path = await plugin.resolve(MediaRequest(request_id="r2", source_hint="local_library", query=str(p)))

        assert by_name.source == "local_library"
        assert by_name.stream_url.endswith("/music/song.mp3")
        assert by_path.stream_url.endswith("/music/song.mp3")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_local_library_source_plugin_resolve_playlist_payload():
    with TemporaryDirectory() as tmp_dir:
        p = Path(tmp_dir) / "song.mp3"
        p.write_bytes(b"demo")
        plugin = LocalLibrarySourcePlugin(_LocalLibraryStub("song", str(p)))

        out = await plugin.resolve(
            MediaRequest(
                request_id="r-playlist",
                source_hint="local_library",
                query="Song From Playlist",
                context={
                    "title": "Song From Playlist",
                    "context_hint": {
                        "context_type": "playlist",
                        "context_name": "All Songs",
                        "context_id": "All Songs",
                    },
                    "source_payload": {
                        "source": "local_library",
                        "playlist_name": "All Songs",
                        "context_name": "All Songs",
                        "music_name": "song",
                        "track_name": "song",
                        "track_id": "track-song-1",
                        "context_type": "playlist",
                    },
                },
            )
        )

        assert out.media_id == "track-song-1"
        assert out.source == "local_library"
        assert out.title == "Song From Playlist"
        assert out.stream_url.endswith("/music/song.mp3")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_local_library_source_plugin_prefers_playlist_item_id_and_entity_id_over_title():
    with TemporaryDirectory() as tmp_dir:
        pa = Path(tmp_dir) / "song-a.mp3"
        pb = Path(tmp_dir) / "song-b.mp3"
        pa.write_bytes(b"a")
        pb.write_bytes(b"b")

        class _PlaylistLibrary(_LocalLibraryStub):
            def __init__(self):
                super().__init__("song-a", str(pa))
                self.config = type(
                    "Cfg",
                    (),
                    {
                        "music_list_json": '{"bad": true}',
                    },
                )()
                self.all_music = {"song-a": str(pa), "song-b": str(pb)}

            def searchmusic(self, query: str):
                return [query] if query in self.all_music else []

        plugin = LocalLibrarySourcePlugin(_PlaylistLibrary())
        out = await plugin.resolve(
            MediaRequest(
                request_id="r-priority",
                source_hint="local_library",
                query="song-a",
                context={
                    "title": "Same Song",
                    "context_hint": {"context_type": "playlist", "context_name": "中文", "context_id": "中文"},
                    "source_payload": {
                        "source": "local_library",
                        "playlist_name": "中文",
                        "music_name": "Same Song",
                        "track_name": "Same Song",
                        "playlist_item_id": "playlist-item-b",
                        "entity_id": "entity-b",
                        "track_id": "track-b",
                        "path": str(pb),
                    },
                },
            )
        )

        assert out.media_id == "playlist-item-b"
        assert out.stream_url.endswith("/music/song-b.mp3")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_local_library_source_plugin_resolve_not_found_raises():
    plugin = LocalLibrarySourcePlugin(_LocalLibraryStub("song", "/music/song.mp3"))

    with pytest.raises(SourceResolveError):
        await plugin.resolve(MediaRequest(request_id="r3", source_hint="local_library", query="not-found"))
