"""Parser for yt-dlp structured output."""

from __future__ import annotations

from typing import Any

from xiaomusic.m1.contracts import ResolveResult


def parse_ytdlp_output(payload: dict[str, Any]) -> ResolveResult:
    url = str(payload.get("url") or "").strip()
    title = str(payload.get("title") or "").strip()
    ext = str(payload.get("ext") or "").strip() or "unknown"
    is_live = bool(payload.get("is_live", False))

    if not url:
        return ResolveResult(
            ok=False,
            source_url="",
            title=title,
            is_live=is_live,
            container_hint=ext,
            error_code="E_RESOLVE_NONZERO_EXIT",
            error_message="missing source url in yt-dlp output",
            meta={"raw_id": payload.get("id")},
        )

    return ResolveResult(
        ok=True,
        source_url=url,
        title=title or "untitled",
        is_live=is_live,
        container_hint=ext,
        error_code=None,
        error_message=None,
        meta={
            "raw_id": payload.get("id"),
            "webpage_url": payload.get("webpage_url"),
        },
    )
