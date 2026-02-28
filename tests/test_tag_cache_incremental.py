import os
import time

from xiaomusic.music_library import MusicLibrary


def test_need_refresh_tag_by_mtime_and_size(tmp_path):
    fp = tmp_path / "a.mp3"
    fp.write_bytes(b"abc")

    lib = MusicLibrary.__new__(MusicLibrary)
    sig = lib._file_tag_signature(str(fp))
    tag = {"title": "x", **sig}

    assert lib._need_refresh_tag(tag, str(fp)) is False

    time.sleep(1)
    fp.write_bytes(b"abcd")
    assert lib._need_refresh_tag(tag, str(fp)) is True


def test_file_tag_signature_handles_missing_file(tmp_path):
    lib = MusicLibrary.__new__(MusicLibrary)
    missing = os.path.join(tmp_path, "missing.mp3")
    sig = lib._file_tag_signature(missing)
    assert sig["__source_mtime"] == 0
    assert sig["__source_size"] == 0
