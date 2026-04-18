from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import unquote, urlparse

from xiaomusic.core.errors.source_errors import SourceResolveError
from xiaomusic.core.models.media import MediaRequest, ResolvedMedia
from xiaomusic.core.source.source_plugin import SourcePlugin


class LocalLibrarySourcePlugin(SourcePlugin):
    """Source plugin for local media library references and file paths."""

    name = "local_library"

    def __init__(self, music_library) -> None:
        self._music_library = music_library

    @staticmethod
    def _playlist_payload(request: MediaRequest) -> tuple[bool, dict, dict]:
        context = request.context if isinstance(request.context, dict) else {}
        payload = context.get("source_payload")
        if not isinstance(payload, dict):
            payload = {}
        context_hint = context.get("context_hint")
        if not isinstance(context_hint, dict):
            context_hint = {}
        context_type = str(
            context_hint.get("context_type") or payload.get("context_type") or ""
        ).strip().lower()
        playlist_name = str(
            payload.get("playlist_name")
            or context_hint.get("context_name")
            or context_hint.get("context_id")
            or payload.get("context_name")
            or ""
        ).strip()
        return context_type == "playlist" or bool(playlist_name), payload, context_hint

    def can_resolve(self, request: MediaRequest) -> bool:
        if request.source_hint == self.name:
            return True
        is_playlist_context, payload, _ = self._playlist_payload(request)
        if isinstance(payload, dict) and str(payload.get("source") or "").lower() == self.name:
            return True
        if is_playlist_context:
            return True
        query = str(request.query or "").strip()
        if not query:
            return False
        if query.startswith("file://"):
            return True
        if self._looks_like_path(query):
            return True
        if query in self._music_library.all_music and not self._music_library.is_web_music(query):
            return True
        return False

    async def resolve(self, request: MediaRequest) -> ResolvedMedia:
        is_playlist_context, payload, context_hint = self._playlist_payload(request)

        raw_query = str(request.query or "").strip()
        candidate = str(
            payload.get("music_name")
            or payload.get("track_name")
            or payload.get("track_id")
            or payload.get("path")
            or payload.get("name")
            or raw_query
            or ""
        ).strip()
        if not candidate:
            raise SourceResolveError("local library query is required")

        playlist_name = str(
            payload.get("playlist_name")
            or context_hint.get("context_name")
            or context_hint.get("context_id")
            or payload.get("context_name")
            or ""
        ).strip()
        title = str(
            request.context.get("title")
            or payload.get("music_name")
            or payload.get("track_name")
            or payload.get("name")
            or payload.get("title")
            or candidate
        ).strip()
        media_id = str(payload.get("track_id") or request.request_id)

        path_candidate = self._candidate_path(candidate)
        if path_candidate:
            final_path = self._validate_path(path_candidate)
            return ResolvedMedia(
                media_id=media_id,
                source=self.name,
                title=title,
                stream_url=self._music_library._get_file_url(str(final_path)),
                headers={},
                expires_at=None,
                is_live=False,
            )

        if candidate in self._music_library.all_music and not self._music_library.is_web_music(candidate):
            filename = self._music_library.get_filename(candidate)
            if not filename:
                raise SourceResolveError(f"local library file missing: {candidate}")
            return ResolvedMedia(
                media_id=media_id,
                source=self.name,
                title=title,
                stream_url=self._music_library._get_file_url(filename),
                headers={},
                expires_at=None,
                is_live=False,
            )

        matches = self._music_library.searchmusic(candidate)
        for name in matches:
            if name in self._music_library.all_music and not self._music_library.is_web_music(name):
                filename = self._music_library.get_filename(name)
                if filename:
                    resolved_title = title if is_playlist_context and title else str(name)
                    return ResolvedMedia(
                        media_id=media_id,
                        source=self.name,
                        title=resolved_title,
                        stream_url=self._music_library._get_file_url(filename),
                        headers={},
                        expires_at=None,
                        is_live=False,
                    )

        # ── Playlist-context branch: read raw music_list_json to find the track ──
        if is_playlist_context and playlist_name:
            try:
                raw = self._music_library.config.music_list_json
                music_lists = json.loads(raw) if raw else []
            except Exception:
                music_lists = []

            if music_lists:
                for playlist in music_lists:
                    pl_name = str(playlist.get("name") or "").strip()
                    if pl_name != playlist_name:
                        continue
                    musics = playlist.get("musics")
                    if not isinstance(musics, list):
                        continue

                    for item in musics:
                        item_name = str(item.get("name") or "").strip()
                        item_track_name = str(item.get("track_name") or "").strip()
                        item_track_id = str(item.get("track_id") or "").strip()

                        if (
                            item_name == candidate
                            or item_track_name == candidate
                            or item_track_id == candidate
                        ):
                            item_url = str(item.get("url") or "").strip()
                            if not item_url:
                                continue
                            parsed_url = urlparse(item_url)
                            is_jellyfin_item = (
                                parsed_url.scheme in ("http", "https")
                                and self._music_library.is_jellyfin_url(item_url)
                            )
                            return ResolvedMedia(
                                media_id=media_id,
                                source="jellyfin" if is_jellyfin_item else self.name,
                                title=title,
                                stream_url=item_url,
                                headers={},
                                expires_at=None,
                                is_live=False,
                            )

            raise SourceResolveError(
                f"local library media not found in playlist {playlist_name}: {candidate}"
            )
        raise SourceResolveError(f"local library media not found: {candidate}")

    @staticmethod
    def _looks_like_path(query: str) -> bool:
        q = query.strip()
        if not q:
            return False
        if q.startswith(("/", "./", "../")):
            return True
        if len(q) > 2 and q[1] == ":" and q[2] in {"/", "\\"}:
            return True
        return q.endswith((".mp3", ".flac", ".m4a", ".wav", ".aac", ".ogg"))

    @staticmethod
    def _candidate_path(candidate: str) -> Path | None:
        if candidate.startswith("file://"):
            parsed = urlparse(candidate)
            return Path(unquote(parsed.path))
        if LocalLibrarySourcePlugin._looks_like_path(candidate):
            return Path(candidate)
        return None

    @staticmethod
    def _validate_path(path: Path) -> Path:
        resolved = path.expanduser().resolve()
        if not resolved.exists() or not resolved.is_file():
            raise SourceResolveError(f"local library path does not exist: {resolved}")
        return resolved
