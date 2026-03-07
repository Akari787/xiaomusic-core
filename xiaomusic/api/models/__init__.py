"""Pydantic 数据模型定义"""

from pydantic import BaseModel, Field

from xiaomusic.api.models.play_request import (
    ControlRequest,
    PlayRequest,
    ResolveRequest,
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


class DidPlayMusic(BaseModel):
    did: str
    musicname: str = ""
    searchkey: str = ""


class DidPlayMusicList(BaseModel):
    did: str
    listname: str = ""
    musicname: str = ""


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


class PlayUrlOptions(BaseModel):
    volume: int | None = None
    prefer_codec: str | None = None
    no_cache: bool = False


class ApiV1PlayUrlRequest(BaseModel):
    url: str
    speaker_id: str
    options: PlayUrlOptions | None = None


class ApiV1StopRequest(BaseModel):
    speaker_id: str | None = None
    sid: str | None = None


class ApiV1ReachabilityRequest(BaseModel):
    speaker_id: str
    base_url: str | None = None


class ApiV1PlayMusicRequest(BaseModel):
    speaker_id: str
    music_name: str
    search_key: str = ""


class ApiV1PlayMusicListRequest(BaseModel):
    speaker_id: str
    list_name: str
    music_name: str = ""


class ApiV1SetPlayModeRequest(BaseModel):
    speaker_id: str
    mode_index: int


class ApiV1PauseRequest(BaseModel):
    speaker_id: str


class ApiV1TtsRequest(BaseModel):
    speaker_id: str
    text: str


class ApiV1SetVolumeRequest(BaseModel):
    speaker_id: str
    volume: int


class ApiV1ProbeRequest(BaseModel):
    speaker_id: str


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


class ApiSessionsResponse(ApiResponseBase):
    sessions: list[dict] = Field(default_factory=list)
    removed: int | None = None
    remaining: int | None = None


class ApiSessionsCleanupRequest(BaseModel):
    max_sessions: int = 100
    ttl_seconds: int | None = None


__all__ = [
    "ApiPlaybackResponse",
    "ApiResponse",
    "ApiResponseBase",
    "ApiSessionsCleanupRequest",
    "ApiSessionsResponse",
    "ApiV1PauseRequest",
    "ApiV1PlayMusicListRequest",
    "ApiV1PlayMusicRequest",
    "ApiV1PlayUrlRequest",
    "ApiV1ProbeRequest",
    "ApiV1ReachabilityRequest",
    "ApiV1SetPlayModeRequest",
    "ApiV1SetVolumeRequest",
    "ApiV1StopRequest",
    "ApiV1TtsRequest",
    "ControlRequest",
    "Did",
    "DidCmd",
    "DidPlayMusic",
    "DidPlayMusicList",
    "DidUrl",
    "DidVolume",
    "DownloadOneMusic",
    "DownloadPlayList",
    "MusicInfoObj",
    "MusicItem",
    "PlayListMusicObj",
    "PlayListObj",
    "PlayListUpdateObj",
    "PlayRequest",
    "PlayUrlOptions",
    "ResolveRequest",
    "SidObj",
    "TtsRequest",
    "UrlInfo",
    "VolumeRequest",
]
