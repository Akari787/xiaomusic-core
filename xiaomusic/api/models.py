"""Pydantic 数据模型定义"""

from pydantic import BaseModel


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
