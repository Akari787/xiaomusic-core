import io
import os
import tarfile

import pytest

from xiaomusic.security.tar_safe import UnsafeTarError, safe_extract_tar_gz


def _make_tar_gz(tmp_path, members: list[tuple[str, bytes | None, dict | None]]):
    """members: (name, data_bytes or None for dir, tarinfo_overrides)"""
    tar_path = tmp_path / "x.tar.gz"
    with tarfile.open(tar_path, mode="w:gz") as tf:
        for name, data, overrides in members:
            ti = tarfile.TarInfo(name=name)
            if overrides:
                for k, v in overrides.items():
                    setattr(ti, k, v)
            if data is None:
                ti.type = tarfile.DIRTYPE
                ti.size = 0
                tf.addfile(ti)
            else:
                ti.size = len(data)
                tf.addfile(ti, io.BytesIO(data))
    return tar_path


def test_safe_extract_blocks_traversal(tmp_path):
    tar_path = _make_tar_gz(tmp_path, [("../../pwn.txt", b"pwn", None)])
    out_dir = tmp_path / "out"
    with pytest.raises(UnsafeTarError):
        safe_extract_tar_gz(str(tar_path), str(out_dir))
    assert not (tmp_path / "pwn.txt").exists()


def test_safe_extract_blocks_absolute(tmp_path):
    tar_path = _make_tar_gz(tmp_path, [("/abs.txt", b"abs", None)])
    out_dir = tmp_path / "out"
    with pytest.raises(UnsafeTarError):
        safe_extract_tar_gz(str(tar_path), str(out_dir))


def test_safe_extract_blocks_symlink(tmp_path):
    tar_path = _make_tar_gz(
        tmp_path,
        [
            (
                "link",
                b"",
                {"type": tarfile.SYMTYPE, "linkname": "../../escape"},
            )
        ],
    )
    out_dir = tmp_path / "out"
    with pytest.raises(UnsafeTarError):
        safe_extract_tar_gz(str(tar_path), str(out_dir))


def test_safe_extract_allows_normal(tmp_path):
    tar_path = _make_tar_gz(tmp_path, [("dir/file.txt", b"ok", None)])
    out_dir = tmp_path / "out"
    safe_extract_tar_gz(str(tar_path), str(out_dir))
    assert (out_dir / "dir" / "file.txt").read_text(encoding="utf-8") == "ok"
