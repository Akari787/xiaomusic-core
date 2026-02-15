from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field


@dataclass
class PathCheck:
    path: str
    exists: bool
    is_dir: bool
    readable: bool
    writable: bool
    error: str = ""


@dataclass
class ToolCheck:
    name: str
    found: bool
    version: str = ""
    error: str = ""


@dataclass
class StartupDiagnostics:
    ok: bool = True
    checked_at: float = field(default_factory=lambda: time.time())
    paths: list[PathCheck] = field(default_factory=list)
    tools: list[ToolCheck] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _check_path(path: str) -> PathCheck:
    path = path or ""
    exists = os.path.exists(path)
    is_dir = os.path.isdir(path)
    readable = False
    writable = False
    err = ""
    try:
        if exists:
            readable = os.access(path, os.R_OK)
            writable = os.access(path, os.W_OK)
            # If dir is writable, do a small write test.
            if is_dir and writable:
                test_file = os.path.join(path, ".xiaomusic_write_test")
                with open(test_file, "w", encoding="utf-8") as f:
                    f.write("ok")
                os.remove(test_file)
    except Exception as e:
        err = str(e)
    return PathCheck(
        path=path,
        exists=exists,
        is_dir=is_dir,
        readable=readable,
        writable=writable,
        error=err,
    )


def _run_version(cmd: list[str]) -> tuple[bool, str, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        out = (p.stdout or "") + (p.stderr or "")
        out = out.strip().splitlines()
        return True, (out[0] if out else ""), ""
    except Exception as e:
        return False, "", str(e)


def build_startup_diagnostics(config) -> StartupDiagnostics:
    d = StartupDiagnostics()
    # Paths
    for p in [config.music_path, config.download_path, config.temp_path, config.cache_dir, config.conf_path]:
        ck = _check_path(p)
        d.paths.append(ck)
        if not (ck.exists and ck.is_dir and ck.readable):
            d.ok = False

    # Tools
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if config.ffmpeg_location:
        # Best-effort: if ffmpeg_location points to a dir, check inside.
        if os.path.isdir(config.ffmpeg_location):
            for n in ("ffmpeg", "ffprobe"):
                cand = os.path.join(config.ffmpeg_location, n)
                if os.path.exists(cand):
                    if n == "ffmpeg":
                        ffmpeg = cand
                    else:
                        ffprobe = cand

    for name, path in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)):
        if not path:
            d.tools.append(ToolCheck(name=name, found=False, error="not found"))
            d.ok = False
            continue
        ok, ver, err = _run_version([path, "-version"])
        d.tools.append(ToolCheck(name=name, found=ok, version=ver, error=err))
        if not ok:
            d.ok = False

    if not d.ok:
        d.notes.append(
            "If running in Docker, ensure /app/music and /app/conf are mounted and writable."
        )
        d.notes.append(
            "If ffmpeg is missing, install ffmpeg or set XIAOMUSIC_FFMPEG_LOCATION to a valid path."
        )

    return d
