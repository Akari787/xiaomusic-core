import aiohttp


class JellyfinClient:
    def __init__(self, config, log):
        self.config = config
        self.log = log

    def enabled(self):
        return bool(
            self.config.jellyfin_enabled
            and self.config.jellyfin_base_url
            and self.config.jellyfin_api_key
        )

    def _headers(self):
        return {
            "X-Emby-Token": self.config.jellyfin_api_key,
            "Accept": "application/json",
        }

    def _base(self):
        return self.config.jellyfin_base_url.rstrip("/")

    async def _resolve_user_id(self):
        if self.config.jellyfin_user_id:
            return self.config.jellyfin_user_id

        url = f"{self._base()}/Users"
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=self._headers()) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise Exception(f"Jellyfin 获取用户失败: {resp.status} {text}")
                users = await resp.json()
        if not users:
            raise Exception("Jellyfin 无可用用户")
        return users[0].get("Id", "")

    def _stream_url(self, item_id):
        base = self._base()
        api_key = self.config.jellyfin_api_key
        return (
            f"{base}/Audio/{item_id}/stream"
            f"?static=true&api_key={api_key}&audioCodec=mp3,aac,flac,opus"
        )

    async def search_music(self, keyword, limit=20):
        user_id = await self._resolve_user_id()
        url = f"{self._base()}/Users/{user_id}/Items"
        params = {
            "Recursive": "true",
            "IncludeItemTypes": "Audio",
            "SearchTerm": keyword,
            "Fields": "AlbumArtists,Artists,MediaSources",
            "Limit": str(limit),
            "SortBy": "SortName",
            "SortOrder": "Ascending",
        }

        timeout = aiohttp.ClientTimeout(total=12)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                url, params=params, headers=self._headers()
            ) as response:
                if response.status != 200:
                    text = await response.text()
                    raise Exception(
                        f"Jellyfin 搜索失败: HTTP {response.status}, {text}"
                    )
                payload = await response.json()

        items = payload.get("Items", []) if isinstance(payload, dict) else []
        data = []
        for item in items:
            item_id = item.get("Id")
            if not item_id:
                continue
            artists = item.get("Artists") or item.get("AlbumArtists") or []
            artist = artists[0] if artists else "Unknown"
            title = item.get("Name") or "Unknown"
            data.append(
                {
                    "title": title,
                    "artist": artist,
                    "album": item.get("Album", ""),
                    "type": "music",
                    "platform": "Jellyfin",
                    "id": item_id,
                    "url": self._stream_url(item_id),
                    "source": "jellyfin",
                }
            )

        return {
            "success": True,
            "data": data,
            "total": len(data),
            "sources": {"Jellyfin": len(data)},
        }
