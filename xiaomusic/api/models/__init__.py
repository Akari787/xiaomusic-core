"""Pydantic 数据模型定义"""

from pydantic import BaseModel

from xiaomusic.api.models.play_request import (
    ControlRequest,
    FavoritesRequest,
    LibraryRefreshRequest,
    PlayModeRequest,
    PlayRequest,
    ResolveRequest,
    ShutdownTimerRequest,
    TtsRequest,
    VolumeRequest,
)
from xiaomusic.api.models.response import ApiResponse


class Did(BaseModel):
    did: str


class DidVolume(BaseModel):
    did: str
    volume: int = 0


class DidCmd(BaseModel):
    did: str
    cmd: str


class MusicInfoObj(BaseModel):
    musicname: str
    title: str = ""
    artist: str = ""
    album: str = ""
    year: str = ""
    genre: str = ""
    lyrics: str = ""
    picture: str = ""  # base64


class MusicItem(BaseModel):
    name: str


class UrlInfo(BaseModel):
    url: str


class DidUrl(BaseModel):
    did: str
    url: str


class SidObj(BaseModel):
    sid: str


class DownloadPlayList(BaseModel):
    dirname: str
    url: str


class DownloadOneMusic(BaseModel):
    name: str = ""
    url: str
    dirname: str = ""
    playlist_name: str = ""


class PlayListObj(BaseModel):
    name: str = ""  # 歌单名


class PlayListUpdateObj(BaseModel):
    oldname: str  # 旧歌单名字
    newname: str  # 新歌单名字


class PlayListMusicObj(BaseModel):
    name: str = ""  # 歌单名
    music_list: list[str]  # 歌曲名列表


class ApiResponseBase(BaseModel):
    ok: bool
    error_code: str | None = None
    message: str | None = None
    # Backward compatibility for older callers that read `success`.
    success: bool | None = None


class ApiPlaybackResponse(ApiResponseBase):
    sid: str = ""
    speaker_id: str = ""
    state: str = "unknown"
    title: str | None = None
    stream_url: str = ""
    is_live: bool | None = None
    uptime: int | None = None
    reconnect_count: int | None = None
    stage: str | None = None
    last_transition_at: int | None = None
    last_error_code: str | None = None
    cache_hit: bool | None = None
    resolve_ms: int | None = None
    source_plugin: str | None = None
    transport: str | None = None
    deprecated: bool | None = None


__all__ = [
    "ApiPlaybackResponse",
    "ApiResponse",
    "ApiResponseBase",
    "ControlRequest",
    "Did",
    "DidCmd",
    "DidUrl",
    "DidVolume",
    "DownloadOneMusic",
    "DownloadPlayList",
    "FavoritesRequest",
    "LibraryRefreshRequest",
    "MusicInfoObj",
    "MusicItem",
    "PlayModeRequest",
    "PlayListMusicObj",
    "PlayListObj",
    "PlayListUpdateObj",
    "PlayRequest",
    "ResolveRequest",
    "ShutdownTimerRequest",
    "SidObj",
    "TtsRequest",
    "UrlInfo",
    "VolumeRequest",
]
