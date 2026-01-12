"""Jellyfin 客户端（只读）"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request


class JellyfinClient:
    """Jellyfin 只读客户端"""

    def __init__(self, base_url: str, api_key: str, user_id: str | None, log):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.user_id = user_id or ""
        self.log = log

    def _build_url(self, path: str, params: dict | None = None) -> str:
        url = f"{self.base_url}{path}"
        if params:
            query = urllib.parse.urlencode(params)
            return f"{url}?{query}"
        return url

    def _request_json(self, path: str, params: dict | None = None) -> dict | list:
        url = self._build_url(path, params)
        req = urllib.request.Request(
            url,
            headers={
                "X-Emby-Token": self.api_key,
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            payload = response.read().decode("utf-8")
        return json.loads(payload)

    def get_user_id(self) -> str:
        if self.user_id:
            return self.user_id
        users = self._request_json("/Users")
        if not isinstance(users, list) or not users:
            return ""
        active_user = next(
            (user for user in users if not user.get("Disabled", False)), users[0]
        )
        self.user_id = active_user.get("Id", "")
        return self.user_id

    def list_playlists(self) -> list[dict]:
        user_id = self.get_user_id()
        if not user_id:
            return []
        data = self._request_json(
            f"/Users/{user_id}/Items",
            params={
                "IncludeItemTypes": "Playlist",
                "Recursive": "true",
                "Fields": "Id,Name",
                "SortBy": "SortName",
            },
        )
        if isinstance(data, dict):
            return data.get("Items", [])
        return []

    def list_playlist_tracks(self, playlist_id: str) -> list[dict]:
        user_id = self.get_user_id()
        if not user_id:
            return []
        data = self._request_json(
            f"/Playlists/{playlist_id}/Items",
            params={
                "UserId": user_id,
                "IncludeItemTypes": "Audio",
                "Fields": "RunTimeTicks,Artists",
            },
        )
        if isinstance(data, dict):
            return data.get("Items", [])
        return []

    def build_stream_url(self, item_id: str) -> str:
        base_url = f"{self.base_url}/Audio/{item_id}/stream"
        query = urllib.parse.urlencode({"api_key": self.api_key})
        return f"{base_url}?{query}"
