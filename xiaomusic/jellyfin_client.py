import json
import os
import time
import uuid
from urllib.parse import urlencode

import aiohttp

AUTH_FILE_NAME = "jellyfin_auth.json"
CLIENT_NAME = "xiaomusic"
DEVICE_NAME = "xiaomusic-core"


class JellyfinAuthError(Exception):
    """Jellyfin login missing, rejected, or expired."""


class JellyfinAuthStore:
    """Persists the Jellyfin session (never the password) under conf/.

    File content: username, user_id, access_token, device_id, base_url,
    logged_in_at. Written with mode 0600.
    """

    def __init__(self, config):
        self.config = config

    def path(self):
        conf_path = getattr(self.config, "conf_path", "") or "conf"
        return os.path.join(conf_path, AUTH_FILE_NAME)

    def load(self) -> dict:
        try:
            with open(self.path(), encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save(self, data: dict) -> None:
        path = self.path()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = f"{path}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)

    def clear_session(self) -> None:
        """Drop the token but keep device_id so re-login reuses the device."""
        data = self.load()
        device_id = data.get("device_id")
        if device_id:
            self.save({"device_id": device_id})
        else:
            try:
                os.remove(self.path())
            except OSError:
                pass


class JellyfinClient:
    def __init__(self, config, log):
        self.config = config
        self.log = log
        self.auth_store = JellyfinAuthStore(config)

    def _session(self) -> dict:
        data = self.auth_store.load()
        if not data.get("access_token") or not data.get("user_id"):
            return {}
        # A session belongs to the server it was issued by.
        if data.get("base_url") and data.get("base_url") != self._base():
            return {}
        return data

    def enabled(self):
        return bool(
            self.config.jellyfin_enabled
            and self.config.jellyfin_base_url
            and self._session()
        )

    def login_status(self) -> dict:
        session = self._session()
        return {
            "logged_in": bool(session),
            "username": str(session.get("username", "")) if session else "",
            "logged_in_at": session.get("logged_in_at") if session else None,
        }

    def _device_id(self) -> str:
        data = self.auth_store.load()
        device_id = str(data.get("device_id") or "").strip()
        return device_id or uuid.uuid4().hex

    @staticmethod
    def _auth_header(device_id: str, token: str = "") -> str:
        from xiaomusic import __version__

        parts = [
            f'Client="{CLIENT_NAME}"',
            f'Device="{DEVICE_NAME}"',
            f'DeviceId="{device_id}"',
            f'Version="{__version__}"',
        ]
        if token:
            safe_token = token.replace('"', "")
            parts.append(f'Token="{safe_token}"')
        return "MediaBrowser " + ", ".join(parts)

    def _headers(self):
        session = self._session()
        if not session:
            raise JellyfinAuthError("Jellyfin 未登录，请在设置页登录")
        return {
            "Authorization": self._auth_header(
                str(session.get("device_id") or ""), str(session["access_token"])
            ),
            "Accept": "application/json",
        }

    def _base(self):
        return (self.config.jellyfin_base_url or "").rstrip("/")

    async def login(self, username: str, password: str) -> dict:
        """Log in with username/password. The password is used once and
        never stored or logged; only the issued access token is persisted."""
        username = (username or "").strip()
        if not self._base():
            raise JellyfinAuthError("请先填写 Jellyfin 地址")
        if not username:
            raise JellyfinAuthError("请填写 Jellyfin 用户名")
        device_id = self._device_id()
        url = f"{self._base()}/Users/AuthenticateByName"
        headers = {
            "Authorization": self._auth_header(device_id),
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=12)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                url, json={"Username": username, "Pw": password or ""}, headers=headers
            ) as resp:
                if resp.status in (401, 403):
                    raise JellyfinAuthError("Jellyfin 用户名或密码错误")
                if resp.status != 200:
                    raise JellyfinAuthError(f"Jellyfin 登录失败: HTTP {resp.status}")
                payload = await resp.json()
        token = str(payload.get("AccessToken") or "")
        user = payload.get("User") or {}
        user_id = str(user.get("Id") or "")
        if not token or not user_id:
            raise JellyfinAuthError("Jellyfin 登录响应缺少令牌")
        self.auth_store.save(
            {
                "username": str(user.get("Name") or username),
                "user_id": user_id,
                "access_token": token,
                "device_id": device_id,
                "base_url": self._base(),
                "logged_in_at": int(time.time()),
            }
        )
        return self.login_status()

    async def logout(self) -> None:
        session = self._session()
        if session:
            try:
                timeout = aiohttp.ClientTimeout(total=8)
                async with aiohttp.ClientSession(timeout=timeout) as http:
                    async with http.post(
                        f"{self._base()}/Sessions/Logout", headers=self._headers()
                    ):
                        pass
            except Exception as e:  # best effort; local session is dropped anyway
                if self.log:
                    self.log.info("jellyfin_logout_remote_failed: %s", type(e).__name__)
        self.auth_store.clear_session()

    def _raise_if_unauthorized(self, status: int) -> None:
        if status == 401:
            raise JellyfinAuthError("Jellyfin 登录已失效，请在设置页重新登录")

    async def _resolve_user_id(self):
        session = self._session()
        if not session:
            raise JellyfinAuthError("Jellyfin 未登录，请在设置页登录")
        return str(session["user_id"])

    def _need_transcode_mp3(self, item):
        container = str(item.get("Container", "")).lower()
        if "m4a" in container:
            return True
        for media_source in item.get("MediaSources", []):
            ms_container = str(media_source.get("Container", "")).lower()
            if "m4a" in ms_container:
                return True
        return False

    @staticmethod
    def build_stream_url(base_url: str, item_id: str, api_key: str) -> str:
        base = (base_url or "").rstrip("/")
        path = f"{base}/Audio/{item_id}/stream.mp3"
        # NOTE:
        # Some XiaoAi firmware variants rewrite '&' in query strings and may
        # send "static=true+api_key=..." to Jellyfin, which becomes invalid.
        # Keep direct-stream URL to a single query param to avoid this class of
        # transport mutation.
        params = [
            ("api_key", api_key),
        ]
        return f"{path}?{urlencode(params)}"

    @staticmethod
    def build_universal_url(base_url: str, item_id: str, user_id: str, api_key: str) -> str:
        base = (base_url or "").rstrip("/")
        path = f"{base}/Audio/{item_id}/universal"
        params = [
            ("UserId", user_id),
            ("Container", "mp3"),
            ("TranscodingContainer", "mp3"),
            ("AudioCodec", "mp3"),
            ("TranscodingProtocol", "http"),
            ("api_key", api_key),
        ]
        return f"{path}?{urlencode(params)}"

    def _stream_url(self, item_id, user_id="", force_transcode=False):
        base = self._base()
        # The speaker fetches the stream itself and cannot send headers, so the
        # session access token travels as the api_key query parameter.
        api_key = str(self._session().get("access_token") or "")
        # Use single-param direct URL for XiaoAi compatibility (see note in
        # build_stream_url). Jellyfin can still transcode stream.mp3 when needed.
        return self.build_stream_url(base, item_id, api_key)

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
                self._raise_if_unauthorized(response.status)
                if response.status != 200:
                    raise Exception(f"Jellyfin 搜索失败: HTTP {response.status}")
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

    def _build_music_name(self, item, item_id_to_name, name_to_item_id):
        title = item.get("Name") or "Unknown"
        artists = item.get("Artists") or item.get("AlbumArtists") or []
        artist = artists[0] if artists else "Unknown"
        base_name = f"{title}-{artist}"
        item_id = str(item.get("Id", "")).strip()

        # 同一个 Jellyfin 媒体项出现在多个歌单时，应复用第一次分配的名称，
        # 而不是在后续歌单里追加后缀制造“同媒体不同名字”的别名。
        if item_id and item_id in item_id_to_name:
            return item_id_to_name[item_id]

        owner = name_to_item_id.get(base_name)
        if owner in (None, item_id):
            name = base_name
        elif item_id:
            short_name = f"{base_name}-[{item_id[:6]}]"
            short_owner = name_to_item_id.get(short_name)
            if short_owner in (None, item_id):
                name = short_name
            else:
                name = f"{base_name}-[{item_id}]"
        else:
            name = base_name

        if item_id:
            item_id_to_name[item_id] = name
            name_to_item_id[name] = item_id
        return name

    def _convert_audio_items_to_musics(self, items, item_id_to_name, name_to_item_id, user_id):
        musics = []
        for item in items:
            item_id = item.get("Id")
            if not item_id:
                continue
            name = self._build_music_name(item, item_id_to_name, name_to_item_id)
            duration = 0
            runtime_ticks = item.get("RunTimeTicks")
            if runtime_ticks:
                try:
                    duration = float(runtime_ticks) / 10000000.0
                except Exception:
                    duration = 0
            stream_url = self._stream_url(
                item_id,
                user_id=user_id,
                force_transcode=self._need_transcode_mp3(item),
            )
            musics.append(
                {
                    "entity_id": f"jellyfin:{item_id}",
                    "name": name,
                    "canonical_name": name,
                    "url": stream_url,
                    "type": "music",
                    "source": "jellyfin",
                    "source_item_id": str(item_id),
                    "media_id": str(item_id),
                    "id": str(item_id),
                    "duration": duration,
                }
            )
        return musics

    async def export_music_lists(self, max_items=3000, max_playlist_items=500):
        user_id = await self._resolve_user_id()
        timeout = aiohttp.ClientTimeout(total=20)
        headers = self._headers()
        base = self._base()

        item_id_to_name = {}
        name_to_item_id = {}
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
                self._raise_if_unauthorized(resp.status)
                if resp.status != 200:
                    raise Exception(f"Jellyfin 获取歌单失败: HTTP {resp.status}")
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
                    # Abort the whole sync rather than silently dropping a
                    # playlist: a partial export would overwrite the old copy.
                    self._raise_if_unauthorized(items_resp.status)
                    if items_resp.status != 200:
                        raise Exception(
                            f"Jellyfin 获取歌单内容失败: {playlist_name} HTTP {items_resp.status}"
                        )
                    items_payload = await items_resp.json()
                playlist_items = (
                    items_payload.get("Items", [])
                    if isinstance(items_payload, dict)
                    else []
                )
                musics = self._convert_audio_items_to_musics(
                    playlist_items, item_id_to_name, name_to_item_id, user_id
                )
                exported_lists.append(
                    {
                        "name": playlist_name,
                        "playlist_id": f"jellyfin:{playlist_id}",
                        "source_playlist_id": str(playlist_id),
                        "musics": musics,
                        "source": "jellyfin",
                    }
                )

        return exported_lists
