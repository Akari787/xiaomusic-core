from __future__ import annotations

import argparse
import base64
import json
import math
import os
import warnings
from dataclasses import dataclass, field
from typing import get_args, get_origin, get_type_hints
from urllib.parse import urlparse

from xiaomusic.config_model import try_validate_config_model
from xiaomusic.const import (
    PLAY_TYPE_ALL,
    PLAY_TYPE_ONE,
    PLAY_TYPE_RND,
    PLAY_TYPE_SEQ,
    PLAY_TYPE_SIN,
)
from xiaomusic.utils.system_utils import validate_proxy


# 默认口令
def default_key_word_dict():
    return {
        "下一首": "play_next",
        "上一首": "play_prev",
        "单曲循环": "set_play_type_one",
        "全部循环": "set_play_type_all",
        "随机播放": "set_play_type_rnd",
        "单曲播放": "set_play_type_sin",
        "顺序播放": "set_play_type_seq",
        "分钟后关机": "stop_after_minute",
        "刷新列表": "gen_music_list",
        "加入收藏": "add_to_favorites",
        "收藏歌曲": "add_to_favorites",
        "取消收藏": "del_from_favorites",
        "播放列表第": "play_music_list_index",
        "删除歌曲": "cmd_del_music",
    }


def default_auth_token_file() -> str:
    return os.getenv("XIAOMUSIC_AUTH_TOKEN_FILE", "auth.json")


AUTH_REFRESH_INTERVAL_DEFAULT_HOURS = 12.0
AUTH_REFRESH_INTERVAL_MIN_HOURS = 0.01
AUTH_REFRESH_THRESHOLD_DEFAULT = 0.3
AUTH_REFRESH_THRESHOLD_MIN = 0.01
AUTH_REFRESH_THRESHOLD_MAX = 0.99


def _safe_float_env(
    name: str,
    default: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    if not math.isfinite(value):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def default_auth_refresh_interval_hours() -> float:
    return _safe_float_env(
        "AUTH_REFRESH_INTERVAL_HOURS",
        AUTH_REFRESH_INTERVAL_DEFAULT_HOURS,
        minimum=AUTH_REFRESH_INTERVAL_MIN_HOURS,
    )


def default_auth_refresh_min_interval_minutes() -> int:
    try:
        value = int(os.getenv("AUTH_REFRESH_MIN_INTERVAL_MINUTES", "30"))
    except (TypeError, ValueError):
        value = 30
    return max(0, value)


def default_auth_refresh_threshold() -> float:
    return _safe_float_env(
        "AUTH_REFRESH_THRESHOLD",
        AUTH_REFRESH_THRESHOLD_DEFAULT,
        minimum=AUTH_REFRESH_THRESHOLD_MIN,
        maximum=AUTH_REFRESH_THRESHOLD_MAX,
    )


def default_user_key_word_dict():
    # Unsafe features (exec#) are disabled by default; keep this empty.
    return {}


# 命令参数在前面
KEY_WORD_ARG_BEFORE_DICT = {
    "分钟后关机": True,
}


# 口令匹配优先级
def default_key_match_order():
    return [
        "分钟后关机",
        "下一首",
        "上一首",
        "单曲循环",
        "全部循环",
        "随机播放",
        "单曲播放",
        "顺序播放",
        "关机",
        "刷新列表",
        "播放列表第",
        "播放列表",
        "加入收藏",
        "收藏歌曲",
        "取消收藏",
        "删除歌曲",
    ]


@dataclass
class Device:
    did: str = ""
    device_id: str = ""
    hardware: str = ""
    name: str = ""
    play_type: int = PLAY_TYPE_RND
    cur_music: str = ""
    cur_playlist: str = ""
    playlist2music: dict[str, str] = field(default_factory=dict)
    current_entity_id: str = ""
    current_playlist_item_id: str = ""
    current_display_name: str = ""


@dataclass
class Config:
    auth_token_file: str = field(default_factory=default_auth_token_file)
    mi_did: str = os.getenv("MI_DID", "")  # 逗号分割支持多设备
    verbose: bool = os.getenv("XIAOMUSIC_VERBOSE", "").lower() == "true"
    music_path: str = os.getenv("XIAOMUSIC_MUSIC_PATH", "music")
    temp_path: str = os.getenv("XIAOMUSIC_TEMP_PATH", "music/tmp")
    download_path: str = os.getenv("XIAOMUSIC_DOWNLOAD_PATH", "music/download")
    conf_path: str = os.getenv("XIAOMUSIC_CONF_PATH", "conf")
    cache_dir: str = os.getenv("XIAOMUSIC_CACHE_DIR", "music/cache")
    public_base_url: str = os.getenv("XIAOMUSIC_PUBLIC_BASE_URL", "")
    port: int = int(os.getenv("XIAOMUSIC_PORT", "8090"))  # 监听端口
    proxy: str | None = os.getenv("XIAOMUSIC_PROXY", None)
    loudnorm: str | None = os.getenv("XIAOMUSIC_LOUDNORM", None)  # 均衡音量参数
    search_prefix: str = os.getenv(
        "XIAOMUSIC_SEARCH", "bilisearch:"
    )  # "bilisearch:" or "ytsearch:"
    ffmpeg_location: str = os.getenv("XIAOMUSIC_FFMPEG_LOCATION", "./ffmpeg/bin")
    active_cmd: str = os.getenv(
        "XIAOMUSIC_ACTIVE_CMD",
        "play,set_play_type_rnd,playlocal,play_music_list,play_music_list_index,stop_after_minute,stop,play_next,play_prev,set_play_type_one,set_play_type_all,set_play_type_sin,set_play_type_seq,gen_music_list,add_to_favorites,del_from_favorites,cmd_del_music,online_play,singer_play",
    )
    exclude_dirs: str = os.getenv("XIAOMUSIC_EXCLUDE_DIRS", "@eaDir,tmp")
    disable_httpauth: bool = (
        os.getenv("XIAOMUSIC_DISABLE_HTTPAUTH", "true").strip().lower() == "true"
    )
    httpauth_username: str = os.getenv("XIAOMUSIC_HTTPAUTH_USERNAME", "")
    httpauth_password: str = os.getenv("XIAOMUSIC_HTTPAUTH_PASSWORD", "")
    music_list_json: str = os.getenv("XIAOMUSIC_MUSIC_LIST_JSON", "")
    custom_play_list_json: str = os.getenv("XIAOMUSIC_CUSTOM_PLAY_LIST_JSON", "")
    disable_download: bool = (
        os.getenv("XIAOMUSIC_DISABLE_DOWNLOAD", "false").lower() == "true"
    )
    key_word_dict: dict[str, str] = field(default_factory=default_key_word_dict)
    key_match_order: list[str] = field(default_factory=default_key_match_order)
    log_file: str = os.getenv("XIAOMUSIC_LOG_FILE", "xiaomusic.log.txt")
    stop_tts_msg: str = os.getenv("XIAOMUSIC_STOP_TTS_MSG", "收到,再见")

    keywords_playlocal: str = os.getenv(
        "XIAOMUSIC_KEYWORDS_PLAYLOCAL", "播放本地歌曲,本地播放歌曲"
    )
    keywords_play: str = os.getenv("XIAOMUSIC_KEYWORDS_PLAY", "播放歌曲,放歌曲")
    keywords_online_play: str = os.getenv("XIAOMUSIC_KEYWORDS_ONLINE_PLAY", "在线播放")
    keywords_singer_play: str = os.getenv("XIAOMUSIC_KEYWORDS_SINGER_PLAY", "播放歌手")
    keywords_stop: str = os.getenv("XIAOMUSIC_KEYWORDS_STOP", "关机,暂停,停止,停止播放")
    keywords_playlist: str = os.getenv(
        "XIAOMUSIC_KEYWORDS_PLAYLIST", "播放列表,播放歌单"
    )
    user_key_word_dict: dict[str, str] = field(
        default_factory=default_user_key_word_dict
    )
    devices: dict[str, Device] = field(default_factory=dict)
    group_list: str = os.getenv(
        "XIAOMUSIC_GROUP_LIST", ""
    )  # did1:group_name,did2:group_name
    remove_id3tag: bool = (
        os.getenv("XIAOMUSIC_REMOVE_ID3TAG", "false").lower() == "true"
    )
    convert_to_mp3: bool = os.getenv("CONVERT_TO_MP3", "false").lower() == "true"
    delay_sec: int = int(os.getenv("XIAOMUSIC_DELAY_SEC", 0))  # 下一首歌延迟播放秒数
    auto_next_confirm_delay_ms: int = int(
        os.getenv("XIAOMUSIC_AUTO_NEXT_CONFIRM_DELAY_MS", "1000")
    )
    auto_next_confirm_retries: int = int(
        os.getenv("XIAOMUSIC_AUTO_NEXT_CONFIRM_RETRIES", "2")
    )
    auto_next_confirm_interval_ms: int = int(
        os.getenv("XIAOMUSIC_AUTO_NEXT_CONFIRM_INTERVAL_MS", "300")
    )
    # Send player_stop before dispatching the next track on timer-driven
    # auto-advance. Off by default: play_by_music_url already replaces the
    # speaker queue, and the stop is a cloud round trip measured at 1-5 s and
    # up to 31 s on the OH2P, during which the speaker audibly restarts the
    # finished track. Manual commands and retries always stop first. Turn this
    # back on only for hardware that will not switch without a stop.
    auto_next_preplay_stop: bool = (
        os.getenv("XIAOMUSIC_AUTO_NEXT_PREPLAY_STOP", "false").lower() == "true"
    )
    # EXPERIMENT (default off): enables /api/experiment/speaker for probing
    # speaker-side queue behavior on the test server (see ADR-0007).
    enable_playback_experiments: bool = (
        os.getenv("XIAOMUSIC_ENABLE_PLAYBACK_EXPERIMENTS", "false").lower() == "true"
    )
    # 目录监控配置
    enable_file_watch: bool = (
        os.getenv("XIAOMUSIC_ENABLE_FILE_WATCH", "false").lower() == "true"
    )
    # Multi-platform channel cycle/context controls.
    channel_cycle_interval: int = int(os.getenv("XIAOMUSIC_CHANNEL_CYCLE_INTERVAL", "60"))
    channel_archive_log_max_entries: int = int(os.getenv("XIAOMUSIC_CHANNEL_ARCHIVE_LOG_MAX_ENTRIES", "500"))
    channel_archive_log_enabled: bool = (
        os.getenv("XIAOMUSIC_CHANNEL_ARCHIVE_LOG_ENABLED", "false").lower() == "true"
    )
    channel_context_window_size: int = int(os.getenv("XIAOMUSIC_CHANNEL_CONTEXT_WINDOW_SIZE", "20"))
    enable_pull_ask: bool = (
        os.getenv("XIAOMUSIC_ENABLE_PULL_ASK", "false").lower() == "true"
    )
    enable_yt_dlp_cookies: bool = (
        os.getenv("XIAOMUSIC_ENABLE_YT_DLP_COOKIES", "false").lower() == "true"
    )
    enable_save_tag: bool = (
        os.getenv("XIAOMUSIC_ENABLE_SAVE_TAG", "false").lower() == "true"
    )

    # ---- Security defaults (safe by default) ----
    log_redact: bool = os.getenv("XIAOMUSIC_LOG_REDACT", "true").lower() == "true"

    # Persist auth token data to conf/auth.json.
    persist_token: bool = (
        os.getenv("XIAOMUSIC_PERSIST_TOKEN", "true").lower() == "true"
    )

    # Exec plugin (exec#...) is dangerous. Default disabled.
    enable_exec_plugin: bool = (
        os.getenv("XIAOMUSIC_ENABLE_EXEC_PLUGIN", "false").lower() == "true"
    )
    allowed_exec_commands: list[str] = field(
        default_factory=lambda: [
            x.strip()
            for x in os.getenv("XIAOMUSIC_ALLOWED_EXEC_COMMANDS", "").split(",")
            if x.strip()
        ]
    )
    allowlist_domains: list[str] = field(
        default_factory=lambda: [
            x.strip().lower()
            for x in os.getenv("XIAOMUSIC_ALLOWLIST_DOMAINS", "").split(",")
            if x.strip()
        ]
    )

    # Unified outbound allowlist (exec http_get, web playlist fetch, self-update downloads, etc.)
    # Default empty -> deny outbound unless explicitly configured.
    outbound_allowlist_domains: list[str] = field(
        default_factory=lambda: [
            x.strip().lower()
            for x in os.getenv("XIAOMUSIC_OUTBOUND_ALLOWLIST_DOMAINS", "").split(",")
            if x.strip()
        ]
    )

    # Self-update is dangerous. Default disabled.
    enable_self_update: bool = (
        os.getenv("XIAOMUSIC_ENABLE_SELF_UPDATE", "false").lower() == "true"
    )

    # CORS allowlist (default: localhost only)
    cors_allow_origins: list[str] = field(
        default_factory=lambda: [
            x.strip()
            for x in os.getenv(
                "XIAOMUSIC_CORS_ALLOW_ORIGINS",
                "http://localhost,http://127.0.0.1",
            ).split(",")
            if x.strip()
        ]
    )

    # Keyword merging behavior: override(default) or append
    keyword_override_mode: str = os.getenv(
        "XIAOMUSIC_KEYWORD_OVERRIDE_MODE", "override"
    )
    get_ask_by_mina: bool = (
        os.getenv("XIAOMUSIC_GET_ASK_BY_MINA", "false").lower() == "true"
    )
    play_type_one_tts_msg: str = os.getenv(
        "XIAOMUSIC_PLAY_TYPE_ONE_TTS_MSG", "已经设置为单曲循环"
    )
    play_type_all_tts_msg: str = os.getenv(
        "XIAOMUSIC_PLAY_TYPE_ALL_TTS_MSG", "已经设置为全部循环"
    )
    play_type_rnd_tts_msg: str = os.getenv(
        "XIAOMUSIC_PLAY_TYPE_RND_TTS_MSG", "已经设置为随机播放"
    )
    play_type_sin_tts_msg: str = os.getenv(
        "XIAOMUSIC_PLAY_TYPE_SIN_TTS_MSG", "已经设置为单曲播放"
    )
    play_type_seq_tts_msg: str = os.getenv(
        "XIAOMUSIC_PLAY_TYPE_SEQ_TTS_MSG", "已经设置为顺序播放"
    )
    # 开启语音删除歌曲
    enable_cmd_del_music: bool = (
        os.getenv("XIAOMUSIC_ENABLE_CMD_DEL_MUSIC", "false").lower() == "true"
    )


    # edge-tts 语音角色
    edge_tts_voice: str = os.getenv("XIAOMUSIC_EDGE_TTS_VOICE", "zh-CN-XiaoyiNeural")
    qrcode_timeout: int = int(os.getenv("QRCODE_TIMEOUT", "120"))
    auth_refresh_interval_hours: float = field(default_factory=default_auth_refresh_interval_hours)
    auth_refresh_min_interval_minutes: int = field(default_factory=default_auth_refresh_min_interval_minutes)
    auth_refresh_threshold: float = field(default_factory=default_auth_refresh_threshold)
    jellyfin_enabled: bool = (
        os.getenv("XIAOMUSIC_JELLYFIN_ENABLED", "false").lower() == "true"
    )
    jellyfin_base_url: str = os.getenv("XIAOMUSIC_JELLYFIN_BASE_URL", "")
    # Jellyfin uses username/password login; the session token lives in
    # conf/jellyfin_auth.json and the password is never stored.
    jellyfin_sync_interval_minutes: int = int(
        os.getenv("XIAOMUSIC_JELLYFIN_SYNC_INTERVAL_MINUTES", "30")
    )

    # Computed / diagnostic fields (not user-configurable)
    keyword_conflicts: list[str] = field(default_factory=list, init=False, repr=False)
    def append_keyword(self, keys, action):
        for key in keys.split(","):
            if key:
                self.key_word_dict[key] = action
                if key not in self.key_match_order:
                    self.key_match_order.append(key)

    def append_user_keyword(self):
        mode = (self.keyword_override_mode or "override").strip().lower()
        if mode not in ("override", "append"):
            mode = "override"

        self.keyword_conflicts = []
        for k, v in (self.user_key_word_dict or {}).items():
            if k in self.key_word_dict:
                self.keyword_conflicts.append(k)
                if mode == "append":
                    continue
            self.key_word_dict[k] = v
            if k not in self.key_match_order:
                self.key_match_order.append(k)

    def init(self):
        if not self.auth_token_file:
            self.auth_token_file = "auth.json"

        self.key_match_order = default_key_match_order()
        self.key_word_dict = default_key_word_dict()
        self.append_keyword(self.keywords_playlocal, "playlocal")
        self.append_keyword(self.keywords_play, "play")
        self.append_keyword(self.keywords_online_play, "online_play")
        self.append_keyword(self.keywords_singer_play, "singer_play")
        self.append_keyword(self.keywords_stop, "stop")
        self.append_keyword(self.keywords_playlist, "play_music_list")
        self.append_user_keyword()
        self.key_match_order = [
            x for x in self.key_match_order if x in self.key_word_dict
        ]

        # 转换数据
        self._active_cmd_arr = self.active_cmd.split(",") if self.active_cmd else []
        self._exclude_dirs_set = set(self.exclude_dirs.split(","))

        # Normalize and validate security/network fields with typed model.
        with warnings.catch_warnings(record=True) as ws:
            warnings.simplefilter("always")
            typed = try_validate_config_model(
                {
                    "enable_exec_plugin": self.enable_exec_plugin,
                    "allowed_exec_commands": self.allowed_exec_commands,
                    "outbound_allowlist_domains": getattr(self, "outbound_allowlist_domains", []) or [],
                    "allowlist_domains": self.allowlist_domains,
                    "enable_self_update": getattr(self, "enable_self_update", False),
                    "cors_allow_origins": self.cors_allow_origins,
                    "log_redact": self.log_redact,
                    "jellyfin_base_url": self.jellyfin_base_url,
                    "port": self.port,
                }
            )
        for w in ws:
            print(f"Config warning: {w.message}")
        if typed is not None:
            self._typed = typed
            self.enable_exec_plugin = typed.enable_exec_plugin
            self.allowed_exec_commands = list(typed.allowed_exec_commands)
            self.allowlist_domains = list(typed.allowlist_domains)
            self.outbound_allowlist_domains = list(typed.outbound_allowlist_domains)
            self.enable_self_update = typed.enable_self_update
            self.cors_allow_origins = list(typed.cors_allow_origins)
            self.log_redact = typed.log_redact
            self.jellyfin_base_url = typed.jellyfin_base_url
        else:
            self.allowed_exec_commands = [x.strip() for x in self.allowed_exec_commands if x.strip()]
            self.allowlist_domains = [x.strip().lower() for x in self.allowlist_domains if x.strip()]
            self.outbound_allowlist_domains = [
                x.strip().lower()
                for x in (getattr(self, "outbound_allowlist_domains", []) or [])
                if x.strip()
            ]
            if not self.outbound_allowlist_domains:
                self.outbound_allowlist_domains = list(self.allowlist_domains)
            self.cors_allow_origins = [x.strip() for x in (self.cors_allow_origins or []) if x.strip()]

        mode = (self.keyword_override_mode or "override").strip().lower()
        if mode not in ("override", "append"):
            mode = "override"
        self.keyword_override_mode = mode

        if not isinstance(self.log_redact, bool):
            self.log_redact = True
        if not isinstance(self.persist_token, bool):
            self.persist_token = True
        if not isinstance(self.enable_exec_plugin, bool):
            self.enable_exec_plugin = False

        if not isinstance(getattr(self, "enable_self_update", False), bool):
            self.enable_self_update = False

    def __post_init__(self) -> None:
        if self.proxy:
            validate_proxy(self.proxy)
        self.init()

    @classmethod
    def from_options(cls, options: argparse.Namespace) -> Config:
        config = {}
        if options.config:
            config = cls.read_from_file(options.config)
        for key, value in vars(options).items():
            if value is not None and key in cls.__dataclass_fields__:
                config[key] = value
        return cls(**config)

    @classmethod
    def convert_value(cls, k, v, type_hints):
        if v is not None and k in type_hints:
            expected_type = type_hints[k]
            try:
                if expected_type is bool:
                    converted_value = False
                    if str(v).lower() == "true":
                        converted_value = True
                elif expected_type == dict[str, Device]:
                    converted_value = {}
                    for kk, vv in v.items():
                        converted_value[kk] = Device(**vv)
                else:
                    origin = get_origin(expected_type)
                    args = get_args(expected_type)
                    if origin is list and isinstance(v, list):
                        inner = args[0] if args else None
                        if inner is str:
                            converted_value = [str(x) for x in v]
                        else:
                            converted_value = list(v)
                    elif origin is dict and isinstance(v, dict):
                        converted_value = dict(v)
                    else:
                        converted_value = expected_type(v)
                return converted_value
            except (ValueError, TypeError) as e:
                print(f"Error converting {k}:{v} to {expected_type}: {e}")
        return None

    @classmethod
    def read_from_file(cls, config_path: str) -> dict:
        result = {}
        with open(config_path, "rb") as f:
            data = json.load(f)
            type_hints = get_type_hints(cls)

            for k, v in data.items():
                if k not in cls.__dataclass_fields__:
                    continue  # removed setting left in an old setting.json
                converted_value = cls.convert_value(k, v, type_hints)
                if converted_value is not None:
                    result[k] = converted_value
        return result

    def update_config(self, data):
        type_hints = get_type_hints(self, globals(), locals())

        for k, v in data.items():
            if k not in self.__dataclass_fields__:
                continue  # removed setting left in an old setting.json
            converted_value = self.convert_value(k, v, type_hints)
            if converted_value is not None:
                setattr(self, k, converted_value)
        self.init()

    def get_active_cmd_arr(self):
        return self._active_cmd_arr

    def get_exclude_dirs_set(self):
        return self._exclude_dirs_set

    # 获取设置文件
    def getsettingfile(self):
        # 兼容旧配置空的情况
        if not self.conf_path:
            self.conf_path = "conf"
        if not os.path.exists(self.conf_path):
            os.makedirs(self.conf_path)
        filename = os.path.join(self.conf_path, "setting.json")
        return filename

    @property
    def auth_token_path(self):
        token_file = self.auth_token_file or "auth.json"
        if os.path.isabs(token_file):
            return token_file
        conf_path = self.conf_path or "conf"
        if not os.path.exists(conf_path):
            os.makedirs(conf_path)
        return os.path.join(conf_path, token_file)

    @property
    def tag_cache_path(self):
        if (len(self.cache_dir) > 0) and (not os.path.exists(self.cache_dir)):
            os.makedirs(self.cache_dir)
        filename = os.path.join(self.cache_dir, "tag_cache.json")
        return filename

    @property
    def picture_cache_path(self):
        cache_path = os.path.join(self.cache_dir, "picture_cache")
        if not os.path.exists(cache_path):
            os.makedirs(cache_path)
        return cache_path

    @property
    def yt_dlp_cookies_path(self):
        if not os.path.exists(self.conf_path):
            os.makedirs(self.conf_path)
        cookies_path = os.path.join(self.conf_path, "yt-dlp-cookie.txt")
        return cookies_path

    @property
    def temp_dir(self):
        if not os.path.exists(self.temp_path):
            os.makedirs(self.temp_path)
        return self.temp_path

    def get_play_type_tts(self, play_type):
        if play_type == PLAY_TYPE_ONE:
            return self.play_type_one_tts_msg
        if play_type == PLAY_TYPE_ALL:
            return self.play_type_all_tts_msg
        if play_type == PLAY_TYPE_RND:
            return self.play_type_rnd_tts_msg
        if play_type == PLAY_TYPE_SIN:
            return self.play_type_sin_tts_msg
        if play_type == PLAY_TYPE_SEQ:
            return self.play_type_seq_tts_msg
        return ""

    def get_one_device_id(self):
        """获取一个设备ID

        Returns:
            str: 第一个设备的device_id，如果没有设备则返回空字符串
        """
        device = next(iter(self.devices.values()), None)
        return device.device_id if device else ""

    def is_http_server_config(self, key: str) -> bool:
        """判断配置键是否影响HTTP服务器

        Args:
            key: 配置键名

        Returns:
            bool: True表示该配置会影响HTTP服务器，False表示不影响
        """
        http_server_keys = {
            "disable_httpauth",
            "httpauth_username",
            "httpauth_password",
            "port",
        }
        return key in http_server_keys

    def get_basic_auth(self):
        credentials = f"{self.httpauth_username}:{self.httpauth_password}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()
        return f"Basic {encoded_credentials}"

    def get_public_base_url(self):
        base = str(self.public_base_url or "").strip()
        if not base:
            return ""
        if not base.startswith(("http://", "https://")):
            base = f"http://{base}"
        parsed = urlparse(base)
        if not parsed.hostname:
            return ""
        if parsed.port is None:
            return f"{parsed.scheme}://{parsed.hostname}"
        return f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"

    def get_self_netloc(self):
        """获取网络地址"""
        parsed = urlparse(self.get_public_base_url())
        host = parsed.hostname or ""
        if not host:
            return ""
        if parsed.port is None:
            return host
        return f"{host}:{parsed.port}"
