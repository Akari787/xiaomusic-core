"""Lightweight API package initializer.

Importing ``xiaomusic.api`` must not initialize FastAPI app state.
Use explicit import ``xiaomusic.api.app`` when app initialization is needed.
"""

from __future__ import annotations

from typing import Any

__all__ = ["app", "HttpInit"]


def __getattr__(name: str) -> Any:
    if name in {"app", "HttpInit"}:
        from xiaomusic.api.app import HttpInit, app

        return {"app": app, "HttpInit": HttpInit}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
