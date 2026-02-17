"""Subprocess runner for yt-dlp with hard timeout kill."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass(slots=True)
class RunnerResult:
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    error_code: str | None


class YtdlpRunner:
    def run(
        self,
        url: str,
        timeout_seconds: float,
        command_override: list[str] | None = None,
    ) -> RunnerResult:
        command = command_override or ["yt-dlp", "-J", "--no-playlist", url]
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            proc.kill()
            _stdout, _stderr = proc.communicate()
            return RunnerResult(
                exit_code=None,
                stdout=_stdout,
                stderr=_stderr,
                timed_out=True,
                error_code="E_RESOLVE_TIMEOUT",
            )

        if proc.returncode != 0:
            return RunnerResult(
                exit_code=proc.returncode,
                stdout=stdout,
                stderr=stderr,
                timed_out=False,
                error_code="E_RESOLVE_NONZERO_EXIT",
            )

        return RunnerResult(
            exit_code=proc.returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=False,
            error_code=None,
        )
