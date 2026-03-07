from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlparse

from xiaomusic.core.errors.source_errors import SourceResolveError
from xiaomusic.core.models.media import MediaRequest, ResolvedMedia
from xiaomusic.core.source.source_plugin import SourcePlugin


class LocalMusicSourcePlugin(SourcePlugin):
    """Official source plugin for local music playback."""

    name = "local_music"

    def __init__(self, music_library) -> None:
        self._music_library = music_library

    def can_resolve(self, request: MediaRequest) -> bool:
        if request.source_hint == self.name:
            return True
        payload = request.context.get("source_payload")
        if isinstance(payload, dict) and str(payload.get("source") or "").lower() == self.name:
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
        payload = request.context.get("source_payload") if isinstance(request.context, dict) else {}
        if not isinstance(payload, dict):
            payload = {}
        raw_query = str(request.query or "").strip()
        candidate = str(
            payload.get("music_name")
            or payload.get("track_id")
            or payload.get("path")
            or payload.get("name")
            or raw_query
            or ""
        )
        if not candidate:
            raise SourceResolveError("local music query is required")

        title = str(request.context.get("title") or payload.get("name") or payload.get("title") or candidate)
        path_candidate = self._candidate_path(candidate)
        if path_candidate:
            final_path = self._validate_path(path_candidate)
            return ResolvedMedia(
                media_id=request.request_id,
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
                raise SourceResolveError(f"local music file missing: {candidate}")
            return ResolvedMedia(
                media_id=request.request_id,
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
                    return ResolvedMedia(
                        media_id=request.request_id,
                        source=self.name,
                        title=str(name),
                        stream_url=self._music_library._get_file_url(filename),
                        headers={},
                        expires_at=None,
                        is_live=False,
                    )

        raise SourceResolveError(f"local music not found: {candidate}")

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
        if LocalMusicSourcePlugin._looks_like_path(candidate):
            return Path(candidate)
        return None

    @staticmethod
    def _validate_path(path: Path) -> Path:
        resolved = path.expanduser().resolve()
        if not resolved.exists() or not resolved.is_file():
            raise SourceResolveError(f"local music path does not exist: {resolved}")
        return resolved
