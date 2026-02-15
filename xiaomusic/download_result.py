from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DownloadResult:
    success: bool
    reason: str = ""
    filepath: str = ""
    stderr_tail: str = ""
    provider: str = ""
    elapsed_ms: int = 0
