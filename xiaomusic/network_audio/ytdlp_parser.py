"""Parser for yt-dlp structured output."""

from __future__ import annotations

from typing import Any

from xiaomusic.network_audio.contracts import ResolveResult


def parse_ytdlp_output(payload: dict[str, Any]) -> ResolveResult:
    url = _pick_source_url(payload)
    title = str(payload.get("title") or "").strip()
    ext = _pick_container_hint(payload)
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


def _pick_source_url(payload: dict[str, Any]) -> str:
    top_url = str(payload.get("url") or "").strip()
    if top_url:
        return top_url

    for key in ("requested_formats", "formats"):
        items = payload.get(key) or []
        if not isinstance(items, list):
            continue

        audio_only = [f for f in items if _is_audio_only_format(f)]
        for fmt in audio_only:
            fmt_url = str((fmt or {}).get("url") or "").strip()
            if fmt_url:
                return fmt_url

        audio_capable = [f for f in items if _is_audio_capable_format(f)]
        for fmt in audio_capable:
            fmt_url = str((fmt or {}).get("url") or "").strip()
            if fmt_url:
                return fmt_url

    return ""


def _pick_container_hint(payload: dict[str, Any]) -> str:
    ext = str(payload.get("ext") or "").strip()
    if ext:
        return ext

    for key in ("requested_formats", "formats"):
        items = payload.get(key) or []
        if not isinstance(items, list):
            continue
        for fmt in items:
            fmt_ext = str((fmt or {}).get("ext") or "").strip()
            if fmt_ext:
                return fmt_ext
    return "unknown"


def _is_audio_only_format(fmt: Any) -> bool:
    if not isinstance(fmt, dict):
        return False
    acodec = str(fmt.get("acodec") or "").lower()
    vcodec = str(fmt.get("vcodec") or "").lower()
    return acodec not in {"", "none"} and vcodec in {"", "none"}


def _is_audio_capable_format(fmt: Any) -> bool:
    if not isinstance(fmt, dict):
        return False
    acodec = str(fmt.get("acodec") or "").lower()
    return acodec not in {"", "none"}
