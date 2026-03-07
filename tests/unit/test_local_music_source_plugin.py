from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from xiaomusic.adapters.sources.local_music_source_plugin import LocalMusicSourcePlugin
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
async def test_local_music_source_plugin_resolve_success_by_name_and_path():
    with TemporaryDirectory() as tmp_dir:
        p = Path(tmp_dir) / "song.mp3"
        p.write_bytes(b"demo")
        plugin = LocalMusicSourcePlugin(_LocalLibraryStub("song", str(p)))

        by_name = await plugin.resolve(MediaRequest(request_id="r1", source_hint="local_music", query="song"))
        by_path = await plugin.resolve(MediaRequest(request_id="r2", source_hint="local_music", query=str(p)))

        assert by_name.source == "local_music"
        assert by_name.stream_url.endswith("/music/song.mp3")
        assert by_path.stream_url.endswith("/music/song.mp3")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_local_music_source_plugin_resolve_not_found_raises():
    plugin = LocalMusicSourcePlugin(_LocalLibraryStub("song", "/music/song.mp3"))

    with pytest.raises(SourceResolveError):
        await plugin.resolve(MediaRequest(request_id="r3", source_hint="local_music", query="not-found"))
