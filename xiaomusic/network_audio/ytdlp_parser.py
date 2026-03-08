"""Parser for yt-dlp structured output."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

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
    is_live = bool(payload.get("is_live", False))
    top_url = str(payload.get("url") or "").strip()
    if top_url and not _should_avoid_top_url(payload, top_url, is_live=is_live):
        return top_url

    live_candidate = _pick_source_url_from_formats(payload, prefer_non_manifest=is_live)
    if live_candidate:
        return live_candidate

    if top_url:
        return top_url

    return ""


def _should_avoid_top_url(payload: dict[str, Any], top_url: str, *, is_live: bool) -> bool:
    if not is_live:
        return False
    host = (urlparse(top_url).hostname or "").lower()
    if "manifest.googlevideo.com" in host or "/playlist/index.m3u8" in top_url:
        return True
    page_url = str(payload.get("webpage_url") or "").lower()
    return "youtube.com" in page_url and host.endswith(".googlevideo.com") and "/manifest/" in top_url


def _pick_source_url_from_formats(payload: dict[str, Any], *, prefer_non_manifest: bool) -> str:
    for key in ("requested_formats", "formats"):
        items = payload.get(key) or []
        if not isinstance(items, list):
            continue

        audio_only = [f for f in items if _is_audio_only_format(f)]
        for fmt in audio_only:
            fmt_url = str((fmt or {}).get("url") or "").strip()
            if fmt_url and (not prefer_non_manifest or not _is_manifest_url(fmt_url)):
                return fmt_url

        audio_capable = [f for f in items if _is_audio_capable_format(f)]
        for fmt in audio_capable:
            fmt_url = str((fmt or {}).get("url") or "").strip()
            if fmt_url and (not prefer_non_manifest or not _is_manifest_url(fmt_url)):
                return fmt_url

    if prefer_non_manifest:
        for key in ("requested_formats", "formats"):
            items = payload.get(key) or []
            if not isinstance(items, list):
                continue
            for fmt in items:
                fmt_url = str((fmt or {}).get("url") or "").strip()
                if fmt_url:
                    return fmt_url

    return ""


def _is_manifest_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return "manifest.googlevideo.com" in host or url.endswith(".m3u8")


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
