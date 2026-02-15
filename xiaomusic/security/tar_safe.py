from __future__ import annotations

import os
import tarfile


class UnsafeTarError(RuntimeError):
    pass


def _is_within_directory(directory: str, target: str) -> bool:
    directory = os.path.abspath(directory)
    target = os.path.abspath(target)
    try:
        common = os.path.commonpath([directory, target])
    except ValueError:
        return False
    return common == directory


def safe_extract_tar_gz(tar_gz_path: str, target_dir: str) -> None:
    """Safely extract a .tar.gz into target_dir.

    Blocks:
    - absolute paths
    - path traversal via '..'
    - symlink/hardlink extraction (link escape)
    """
    target_dir = os.path.abspath(target_dir)
    os.makedirs(target_dir, exist_ok=True)

    with tarfile.open(tar_gz_path, mode="r:gz") as tf:
        members = tf.getmembers()
        for m in members:
            name = m.name
            if not name or name.startswith("/") or name.startswith("\\"):
                raise UnsafeTarError("absolute path in tar")
            # Normalize and check traversal
            dest = os.path.join(target_dir, name)
            if not _is_within_directory(target_dir, dest):
                raise UnsafeTarError("path traversal in tar")
            # Block links to avoid escape.
            if m.issym() or m.islnk():
                raise UnsafeTarError("links not allowed in tar")

        tf.extractall(path=target_dir, members=members)
