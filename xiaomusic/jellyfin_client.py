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

        configured = (self.config.jellyfin_user_id or "").strip()
        if configured:
            for user in users:
                uid = str(user.get("Id", "")).strip()
                name = str(user.get("Name", "")).strip()
                if configured == uid:
                    return uid
                if configured.lower() == name.lower() and uid:
                    return uid
            raise Exception(
                f"Jellyfin 用户标识无效: {configured}（应为用户ID或用户名）"
            )

        return str(users[0].get("Id", ""))

    def _need_transcode_mp3(self, item):
        container = str(item.get("Container", "")).lower()
        if "m4a" in container:
            return True
        for media_source in item.get("MediaSources", []):
            ms_container = str(media_source.get("Container", "")).lower()
            if "m4a" in ms_container:
                return True
        return False

    def _stream_url(self, item_id, user_id="", force_transcode=False):
        base = self._base()
        api_key = self.config.jellyfin_api_key
        if force_transcode and user_id:
            return (
                f"{base}/Audio/{item_id}/universal"
                f"?UserId={user_id}&Container=mp3&TranscodingContainer=mp3"
                f"&AudioCodec=mp3&TranscodingProtocol=http&api_key={api_key}"
            )
        return f"{base}/Audio/{item_id}/stream.mp3?static=true&api_key={api_key}"

    async def search_music(self, keyword, limit=20):
        user_id = await self._resolve_user_id()
        url = f"{self._base()}/Users/{user_id}/Items"
        params = {
            "Recursive": "true",
            "IncludeItemTypes": "Audio",
            "SearchTerm": keyword,
            "Fields": "AlbumArtists,Artists,MediaSources,Container",
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
                    "url": self._stream_url(
                        item_id,
                        user_id=user_id,
                        force_transcode=self._need_transcode_mp3(item),
                    ),
                    "source": "jellyfin",
                }
            )

        return {
            "success": True,
            "data": data,
            "total": len(data),
            "sources": {"Jellyfin": len(data)},
        }

    def _build_music_name(self, item, used_names):
        title = item.get("Name") or "Unknown"
        artists = item.get("Artists") or item.get("AlbumArtists") or []
        artist = artists[0] if artists else "Unknown"
        base_name = f"{title}-{artist}"
        name = base_name
        item_id = str(item.get("Id", ""))
        if name in used_names and item_id:
            name = f"{base_name}-[{item_id[:6]}]"
        if name in used_names and item_id:
            name = f"{base_name}-[{item_id}]"
        return name

    def _convert_audio_items_to_musics(self, items, used_names, user_id):
        musics = []
        for item in items:
            item_id = item.get("Id")
            if not item_id:
                continue
            name = self._build_music_name(item, used_names)
            used_names.add(name)
            duration = 0
            runtime_ticks = item.get("RunTimeTicks")
            if runtime_ticks:
                try:
                    duration = float(runtime_ticks) / 10000000.0
                except Exception:
                    duration = 0
            musics.append(
                {
                    "name": name,
                    "url": self._stream_url(
                        item_id,
                        user_id=user_id,
                        force_transcode=self._need_transcode_mp3(item),
                    ),
                    "type": "music",
                    "duration": duration,
                }
            )
        return musics

    async def export_music_lists(self, max_items=3000, max_playlist_items=500):
        user_id = await self._resolve_user_id()
        timeout = aiohttp.ClientTimeout(total=20)
        headers = self._headers()
        base = self._base()

        used_names = set()
        exported_lists = []

        async with aiohttp.ClientSession(timeout=timeout) as session:
            playlist_url = f"{base}/Users/{user_id}/Items"
            playlist_params = {
                "Recursive": "true",
                "IncludeItemTypes": "Playlist",
                "SortBy": "SortName",
                "SortOrder": "Ascending",
                "Limit": "100",
            }
            async with session.get(
                playlist_url, params=playlist_params, headers=headers
            ) as resp:
                if resp.status != 200:
                    return exported_lists
                playlist_payload = await resp.json()

            playlists = (
                playlist_payload.get("Items", [])
                if isinstance(playlist_payload, dict)
                else []
            )
            for playlist in playlists:
                playlist_id = playlist.get("Id")
                playlist_name = playlist.get("Name") or "未命名歌单"
                if not playlist_id:
                    continue
                items_url = f"{base}/Playlists/{playlist_id}/Items"
                items_params = {
                    "UserId": user_id,
                    "Recursive": "true",
                    "IncludeItemTypes": "Audio",
                    "Fields": "AlbumArtists,Artists,MediaSources,Container,RunTimeTicks",
                    "Limit": str(max_playlist_items),
                }
                async with session.get(
                    items_url, params=items_params, headers=headers
                ) as items_resp:
                    if items_resp.status != 200:
                        continue
                    items_payload = await items_resp.json()
                playlist_items = (
                    items_payload.get("Items", [])
                    if isinstance(items_payload, dict)
                    else []
                )
                musics = self._convert_audio_items_to_musics(
                    playlist_items, used_names, user_id
                )
                exported_lists.append(
                    {"name": playlist_name, "musics": musics, "source": "jellyfin"}
                )

        return exported_lists
