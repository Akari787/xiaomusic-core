import { useEffect, useMemo, useRef, useState } from "react";

import {
  addFavorite as v1AddFavorite,
  apiErrorInfo,
  getDevices as v1GetDevices,
  getLibraryMusicInfo,
  getLibraryPlaylists,
  getSystemStatus,
  getPlayerState,
  isApiOk,
  next as v1Next,
  play as v1Play,
  previous as v1Previous,
  removeFavorite as v1RemoveFavorite,
  libraryRefresh as v1LibraryRefresh,
  setPlayMode as v1SetPlayMode,
  setShutdownTimer as v1SetShutdownTimer,
  setVolume as v1SetVolume,
  stop as v1Stop,
  tts as v1Tts,
  type ApiEnvelope,
  type PlayMode,
  type PlayerStateData,
} from "../services/v1Api";
import { fetchAuthStatus, logoutAuth as logoutAuthRequest, reloadAuthRuntime } from "../services/auth";
import {
  cleanTempDir as cleanTempDirRequest,
  fetchPlaylistJson,
  fetchQrcode,
  fetchSettingsWithDevices,
  refreshMusicTag as refreshMusicTagRequest,
  saveSettingsPayload,
  searchOnlineMusic,
  updateSystemSetting,
} from "../services/homeApi";

void [v1RemoveFavorite];

import { useTheme } from "../theme/ThemeProvider";
import "../styles/home.css";

type Device = {
  did?: string;
  deviceID?: string;
  miotDID?: string;
  hardware?: string;
  name?: string;
  alias?: string;
};

type PlayingInfo = {
  ret?: string;
  is_playing?: boolean;
  cur_music?: string;
  cur_playlist?: string;
  offset?: number;
  duration?: number;
};

type OnlineSearchItem = {
  name?: string;
  title?: string;
  artist?: string;
};

type AuthStatus = {
  token_valid?: boolean;
  runtime_auth_ready?: boolean;
  login_in_progress?: boolean;
  last_error?: string;
};

type QrcodeResp = {
  success?: boolean;
  already_logged_in?: boolean;
  qrcode_url?: string;
  expire_seconds?: number;
  message?: string;
  error?: string;
};

const PLAY_MODES = [
  { icon: "repeat_one", label: "单曲循环", value: "one" },
  { icon: "repeat", label: "全部循环", value: "all" },
  { icon: "shuffle", label: "随机播放", value: "random" },
  { icon: "filter_1", label: "单曲播放", value: "single" },
  { icon: "playlist_play", label: "顺序播放", value: "sequence" },
] as const;

type SettingField = {
  key: string;
  label: string;
  kind?: "text" | "number" | "bool" | "password" | "textarea" | "select";
  options?: Array<{ value: string; label: string }>;
};

const EDGE_TTS_VOICE_OPTIONS: Array<{ value: string; label: string }> = [
  { value: "", label: "不使用(默认)" },
  { value: "zh-CN-XiaoxiaoNeural", label: "晓晓 (女声,温柔)" },
  { value: "zh-CN-XiaoyiNeural", label: "晓伊 (女声,活泼)" },
  { value: "zh-CN-YunjianNeural", label: "云健 (男声,成熟)" },
  { value: "zh-CN-YunxiNeural", label: "云希 (男声,阳光)" },
  { value: "zh-CN-YunxiaNeural", label: "云夏 (男声,少年)" },
  { value: "zh-CN-YunyangNeural", label: "云扬 (男声,新闻)" },
  { value: "zh-CN-liaoning-XiaobeiNeural", label: "晓北 (女声,东北)" },
  { value: "zh-CN-shaanxi-XiaoniNeural", label: "晓妮 (女声,陕西)" },
  { value: "zh-HK-HiuGaaiNeural", label: "曉佳 (女声,粤语)" },
  { value: "zh-HK-HiuMaanNeural", label: "曉曼 (女声,粤语)" },
  { value: "zh-HK-WanLungNeural", label: "雲龍 (男声,粤语)" },
  { value: "zh-TW-HsiaoChenNeural", label: "曉臻 (女声,台湾)" },
  { value: "zh-TW-YunJheNeural", label: "雲哲 (男声,台湾)" },
  { value: "zh-TW-HsiaoYuNeural", label: "曉雨 (女声,台湾)" },
];

const BUILT_IN_THEME_OPTIONS: Array<{ value: "default" | "dark"; label: string }> = [
  { value: "default", label: "Default" },
  { value: "dark", label: "Dark" },
];

const ADVANCED_TABS: Array<{ key: string; label: string; fields: SettingField[] }> = [
  {
    key: "filepath",
    label: "文件路径",
    fields: [
      { key: "music_path", label: "音乐目录" },
      { key: "download_path", label: "音乐下载目录" },
      { key: "conf_path", label: "配置文件目录" },
      { key: "cache_dir", label: "缓存文件目录" },
      { key: "temp_path", label: "临时文件目录" },
      { key: "ffmpeg_location", label: "ffmpeg 路径" },
    ],
  },
  {
    key: "filewatch",
    label: "文件监控",
    fields: [
      { key: "enable_file_watch", label: "启用目录监控", kind: "bool" },
      { key: "file_watch_debounce", label: "刷新延迟(秒)", kind: "number" },
      { key: "enable_auto_clean_temp", label: "启用定时清理临时文件", kind: "bool" },
      { key: "exclude_dirs", label: "忽略目录" },
      { key: "ignore_tag_dirs", label: "不扫描标签目录" },
      { key: "music_path_depth", label: "目录深度", kind: "number" },
      { key: "recently_added_playlist_len", label: "最近新增歌曲数量", kind: "number" },
    ],
  },
  {
    key: "playback",
    label: "播放控制",
    fields: [
      { key: "delay_sec", label: "下一首延迟秒数", kind: "number" },
      { key: "continue_play", label: "启用继续播放", kind: "bool" },
      { key: "use_music_api", label: "型号兼容模式", kind: "bool" },
      { key: "use_music_audio_id", label: "触屏版歌曲ID" },
      { key: "use_music_id", label: "触屏版分段ID" },
      { key: "active_cmd", label: "允许唤醒命令" },
    ],
  },
  {
    key: "search",
    label: "搜索匹配",
    fields: [
      { key: "fuzzy_match_cutoff", label: "模糊匹配阈值", kind: "number" },
      { key: "enable_fuzzy_match", label: "开启模糊搜索", kind: "bool" },
    ],
  },
  {
    key: "audio",
    label: "音频处理",
    fields: [
      { key: "get_duration_type", label: "获取时长方式" },
      { key: "loudnorm", label: "均衡音量参数" },
      { key: "remove_id3tag", label: "去除 MP3 ID3v2", kind: "bool" },
      { key: "convert_to_mp3", label: "转换为 MP3", kind: "bool" },
      { key: "enable_save_tag", label: "启用 ID3 写入", kind: "bool" },
    ],
  },
  {
    key: "tts",
    label: "语音TTS",
    fields: [
      {
        key: "edge_tts_voice",
        label: "Edge-TTS 语音角色",
        kind: "select",
        options: EDGE_TTS_VOICE_OPTIONS,
      },
      { key: "stop_tts_msg", label: "停止提示音" },
      { key: "play_type_one_tts_msg", label: "单曲循环提示音" },
      { key: "play_type_all_tts_msg", label: "全部循环提示音" },
      { key: "play_type_rnd_tts_msg", label: "随机播放提示音" },
      { key: "play_type_sin_tts_msg", label: "单曲播放提示音" },
      { key: "play_type_seq_tts_msg", label: "顺序播放提示音" },
    ],
  },
  {
    key: "voicecmd",
    label: "语音命令",
    fields: [
      { key: "keywords_playlocal", label: "播放本地歌曲口令" },
      { key: "keywords_play", label: "播放歌曲口令" },
      { key: "keywords_playlist", label: "播放列表口令" },
      { key: "keywords_stop", label: "停止口令" },
      { key: "keywords_online_play", label: "在线播放口令" },
      { key: "keywords_singer_play", label: "播放歌手口令" },
      { key: "enable_cmd_del_music", label: "启用语音删除歌曲", kind: "bool" },
    ],
  },
  {
    key: "network",
    label: "网络下载",
    fields: [
      { key: "search_prefix", label: "歌曲下载方式前缀" },
      { key: "proxy", label: "代理地址" },
      { key: "web_music_proxy", label: "网络歌曲走代理", kind: "bool" },
      { key: "disable_download", label: "关闭下载功能", kind: "bool" },
      { key: "enable_yt_dlp_cookies", label: "启用 yt-dlp-cookies", kind: "bool" },
      { key: "yt_dlp_cookies_path", label: "yt-dlp-cookies 文件路径" },
    ],
  },
  {
    key: "device",
    label: "设备对话",
    fields: [
      { key: "group_list", label: "设备分组配置" },
      { key: "enable_pull_ask", label: "获取对话记录", kind: "bool" },
      { key: "pull_ask_sec", label: "对话轮询间隔(秒)", kind: "number" },
      { key: "get_ask_by_mina", label: "特殊型号对话记录", kind: "bool" },
    ],
  },
  {
    key: "playlist",
    label: "歌单定时",
    fields: [
      { key: "music_list_url", label: "歌单地址" },
      { key: "music_list_json", label: "歌单内容", kind: "textarea" },
      { key: "crontab_json", label: "定时任务", kind: "textarea" },
    ],
  },
  {
    key: "security",
    label: "安全访问",
    fields: [
      { key: "disable_httpauth", label: "关闭控制台密码验证", kind: "bool" },
      { key: "httpauth_username", label: "控制台账户" },
      { key: "httpauth_password", label: "控制台密码", kind: "password" },
    ],
  },
  {
    key: "other",
    label: "其他",
    fields: [
      { key: "verbose", label: "开启调试日志", kind: "bool" },
      { key: "log_file", label: "日志路径" },
      { key: "enable_analytics", label: "开启谷歌数据统计", kind: "bool" },
    ],
  },
];

function resolveDisplayName(d: Device): string {
  return d.alias || d.name || d.did || d.deviceID || d.miotDID || "未知设备";
}

function deviceCandidates(d: Device): string[] {
  const vals = [d.miotDID, d.did, d.deviceID].filter((v): v is string => Boolean(v));
  return Array.from(new Set(vals));
}

function preferredDidFromDevice(d: Device): string {
  return String(d.miotDID || d.did || d.deviceID || "").trim();
}

function formatTime(sec: number): string {
  const s = Math.max(0, Math.floor(sec || 0));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${r.toString().padStart(2, "0")}`;
}

function displayModelName(d: Device): string {
  const raw = String(d.name || d.alias || "").trim();
  if (!raw) {
    return resolveDisplayName(d);
  }
  return raw;
}

function explainPlaybackError(
  errorCode?: string | null,
  message?: string | null,
  stage?: string | null,
): string {
  const code = String(errorCode || "");
  if (code === "E_RESOLVE_NONZERO_EXIT") {
    return "链接解析失败（源站限制/风控/地区限制）。建议换直链或更换来源。";
  }
  if (code === "E_STREAM_NOT_FOUND") {
    return "未找到可用流会话，请重试或切换播放模式。";
  }
  if (code === "E_XIAOMI_PLAY_FAILED") {
    return "小爱端播放失败，请检查设备在线状态与当前播放权限。";
  }
  if (code === "E_DEVICE_NOT_FOUND") {
    return "目标设备不存在或当前不可用，请重新选择设备后重试。";
  }
  if (code === "E_INVALID_REQUEST") {
    return message || "请求参数不合法，请检查输入后重试。";
  }
  if (stage === "request") {
    return `请求参数错误：${message || code || "未知错误"}`;
  }
  if (stage === "resolve") {
    return `解析阶段失败：${message || code || "未知错误"}`;
  }
  if (stage === "prepare") {
    return `预处理阶段失败：${message || code || "未知错误"}`;
  }
  if (stage === "dispatch" || stage === "xiaomi") {
    return `下发播放阶段失败：${message || code || "未知错误"}`;
  }
  if (stage === "transport") {
    return `传输执行阶段失败：${message || code || "未知错误"}`;
  }
  return message || code || "未知错误";
}

function normalizeBaseUrlInput(raw: unknown): string {
  const s = String(raw || "").trim();
  if (!s) {
    return "";
  }
  try {
    const withScheme = s.startsWith("http://") || s.startsWith("https://") ? s : `http://${s}`;
    const u = new URL(withScheme);
    if (!u.hostname) {
      return "";
    }
    return u.port ? `${u.protocol}//${u.hostname}:${u.port}` : `${u.protocol}//${u.hostname}`;
  } catch {
    return "";
  }
}

function browserOriginBaseUrl(): string {
  if (typeof window === "undefined") {
    return "";
  }
  return normalizeBaseUrlInput(window.location.origin);
}

function legacyBaseUrl(hostnameRaw: unknown, portRaw: unknown): string {
  const host = String(hostnameRaw || "").trim();
  if (!host) {
    return "";
  }
  const normalizedHost = normalizeBaseUrlInput(host);
  if (!normalizedHost) {
    return "";
  }
  const u = new URL(normalizedHost);
  const portNum = Number(portRaw || 0);
  if (Number.isFinite(portNum) && portNum > 0) {
    return `${u.protocol}//${u.hostname}:${portNum}`;
  }
  return normalizedHost;
}

function legacyLooksUnconfigured(hostnameRaw: unknown, portRaw: unknown): boolean {
  const host = String(hostnameRaw || "").trim();
  const portNum = Number(portRaw || 0);
  return !host || (host === "http://192.168.2.5" && (!portNum || portNum === 58090));
}

function baseUrlToLegacyHostPort(baseUrl: string): { hostname: string; public_port: number } | null {
  const normalized = normalizeBaseUrlInput(baseUrl);
  if (!normalized) {
    return null;
  }
  const u = new URL(normalized);
  return {
    hostname: `${u.protocol}//${u.hostname}`,
    public_port: Number(u.port || (u.protocol === "https:" ? "443" : "80")),
  };
}

function playbackSongStorageKey(did: string): string {
  return `xm_last_playing_song_${did}`;
}

function loadRememberedPlayingSong(did: string): string {
  if (!did) {
    return "";
  }
  try {
    return String(localStorage.getItem(playbackSongStorageKey(did)) || "").trim();
  } catch {
    return "";
  }
}

function saveRememberedPlayingSong(did: string, song: string): void {
  if (!did) {
    return;
  }
  const value = String(song || "").trim();
  if (!value) {
    return;
  }
  try {
    localStorage.setItem(playbackSongStorageKey(did), value);
  } catch {
    return;
  }
}

function loadLocal(key: string): string {
  try {
    return String(localStorage.getItem(key) || "");
  } catch {
    return "";
  }
}

function saveLocal(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    return;
  }
}

function removeLocal(key: string): void {
  try {
    localStorage.removeItem(key);
  } catch {
    return;
  }
}

function playbackSnapshotKey(did: string): string {
  return `xm_playback_snapshot_${did}`;
}

function volumeStorageKey(did: string): string {
  return `xm_volume_${did}`;
}

function loadRememberedVolume(did: string): number {
  if (!did) {
    return 50;
  }
  const raw = Number(loadLocal(volumeStorageKey(did)));
  return Number.isFinite(raw) && raw >= 0 && raw <= 100 ? raw : 50;
}

function saveRememberedVolume(did: string, volume: number): void {
  if (!did) {
    return;
  }
  const next = Math.max(0, Math.min(100, Math.floor(Number(volume) || 0)));
  saveLocal(volumeStorageKey(did), String(next));
}

export function HomePage() {
  const [devices, setDevices] = useState<Device[]>([]);
  const [activeDid, setActiveDid] = useState<string>(() => loadLocal("xm_ui_active_did"));
  const [playlists, setPlaylists] = useState<Record<string, string[]>>({});
  const [playlist, setPlaylist] = useState<string>(() => loadLocal("xm_ui_playlist"));
  const [music, setMusic] = useState<string>(() => loadLocal("xm_ui_music"));
  const [volume, setVolume] = useState<number>(50);
  const [status, setStatus] = useState<PlayingInfo>({});
  const [message, setMessage] = useState<string>("");
  const [version, setVersion] = useState<string>("");
  const [playModeIndex, setPlayModeIndex] = useState<number>(2);
  const [switchingPlayMode, setSwitchingPlayMode] = useState<boolean>(false);

  const [showSearch, setShowSearch] = useState<boolean>(false);
  const [showTimer, setShowTimer] = useState<boolean>(false);
  const [showPlaylink, setShowPlaylink] = useState<boolean>(false);
  const [showVolume, setShowVolume] = useState<boolean>(false);
  const [showSettings, setShowSettings] = useState<boolean>(false);

  const [linkUrl, setLinkUrl] = useState<string>("https://www.youtube.com/watch?v=iPnaF8Ngk3Q");
  const [ttsText, setTtsText] = useState<string>("播放文字测试");
  const [searchKeyword, setSearchKeyword] = useState<string>("");
  const [soundscapeFilter, setSoundscapeFilter] = useState<string>("");
  const [searchResults, setSearchResults] = useState<OnlineSearchItem[]>([]);
  const [selectedSearchIndex, setSelectedSearchIndex] = useState<number>(-1);

  const [authStatus, setAuthStatus] = useState<AuthStatus>({});
  const [qrcodeUrl, setQrcodeUrl] = useState<string>("");
  const [qrcodeStatus, setQrcodeStatus] = useState<string>("点击上方按钮获取登录二维码");
  const [settingData, setSettingData] = useState<Record<string, unknown>>({});
  const [settingJsonText, setSettingJsonText] = useState<string>("{}");
  const [settingDeviceList, setSettingDeviceList] = useState<Device[]>([]);
  const [selectedSettingDids, setSelectedSettingDids] = useState<string[]>([]);
  const [advancedOpen, setAdvancedOpen] = useState<boolean>(false);
  const [activeAdvancedTab, setActiveAdvancedTab] = useState<string>("filepath");
  const [securityAdvancedOpen, setSecurityAdvancedOpen] = useState<boolean>(false);
  const [operationOpen, setOperationOpen] = useState<boolean>(false);
  const [toolsOpen, setToolsOpen] = useState<boolean>(false);
  const [qrcodeExpireAt, setQrcodeExpireAt] = useState<number>(0);
  const [qrcodeRemain, setQrcodeRemain] = useState<number>(0);
  const [pullAskEnabled, setPullAskEnabled] = useState<boolean>(false);
  const [localPlaybackStartedAt, setLocalPlaybackStartedAt] = useState<number>(0);
  const [localPlaybackDuration, setLocalPlaybackDuration] = useState<number>(0);
  const [localPlaybackSong, setLocalPlaybackSong] = useState<string>("");
  const [rememberedPlayingSong, setRememberedPlayingSong] = useState<string>("");
  const activeDidRef = useRef<string>(activeDid);
  const statusRef = useRef<PlayingInfo>({});
  const localPlaybackStartedAtRef = useRef<number>(0);
  const localPlaybackDurationRef = useRef<number>(0);
  const localPlaybackSongRef = useRef<string>("");
  const rememberedPlayingSongRef = useRef<string>("");
  const publicBaseMigratedRef = useRef<boolean>(false);
  const lastPositivePlaybackAtRef = useRef<number>(0);
  const stopSuppressUntilRef = useRef<number>(0);
  const lastAutoSyncedPlayingSongRef = useRef<string>("");
  const statusRequestSeqRef = useRef<number>(0);
  const activeActionSeqRef = useRef<number>(0);
  const pendingPlayRef = useRef<{ did: string; song: string; expiresAt: number } | null>(null);
  const themeFileInputRef = useRef<HTMLInputElement | null>(null);

  const {
    selectedThemeId,
    activeLayout,
    customThemes,
    setTheme,
    uploadThemePackage,
    validationError,
  } = useTheme();

  const songs = useMemo(() => playlists[playlist] || [], [playlists, playlist]);
  const filteredSongs = useMemo(() => {
    const key = soundscapeFilter.trim().toLowerCase();
    if (!key) {
      return songs;
    }
    return songs.filter((name) => name.toLowerCase().includes(key));
  }, [songs, soundscapeFilter]);
  const authLoggedIn = Boolean(authStatus.token_valid);
  const authReady = Boolean(authStatus.runtime_auth_ready);
  const authInProgress = Boolean(authStatus.login_in_progress);
  const authStatusLabel = authReady ? "已登录" : authLoggedIn ? "登录待恢复" : "未登录";
  const authStatusClass = authReady ? "ok" : "warn";
  const autoDetectedBaseUrl = useMemo(() => browserOriginBaseUrl(), []);
  const effectivePublicBaseUrl = useMemo(() => {
    const manual = normalizeBaseUrlInput(settingData.public_base_url);
    if (manual) {
      return manual;
    }
    const legacy = legacyBaseUrl(settingData.hostname, settingData.public_port);
    return legacy || autoDetectedBaseUrl;
  }, [settingData.public_base_url, settingData.hostname, settingData.public_port, autoDetectedBaseUrl]);
  const themeSelectOptions = useMemo(
    () => [
      ...BUILT_IN_THEME_OPTIONS,
      ...customThemes.map((t) => ({ value: t.id, label: t.name })),
    ],
    [customThemes],
  );
  const { safeOffset, safeDuration } = useMemo(() => {
    const duration = Math.max(0, Math.floor(Number(status.duration || 0)));
    let offset = Math.max(0, Math.floor(Number(status.offset || 0)));
    if (duration > 0) {
      const maybeMilliseconds = offset > duration * 100 && Math.floor(offset / 1000) <= duration + 30;
      if (maybeMilliseconds) {
        offset = Math.floor(offset / 1000);
      }
      if (offset > duration + 30) {
        const elapsed = localPlaybackStartedAt
          ? Math.max(0, Math.floor((Date.now() - localPlaybackStartedAt) / 1000))
          : 0;
        offset = elapsed > 0 ? Math.min(elapsed, duration) : Math.min(offset, duration);
      }
    }
    return { safeOffset: offset, safeDuration: duration };
  }, [status.duration, status.offset, localPlaybackStartedAt]);
  const progress = useMemo(() => {
    if (!safeDuration) {
      return 0;
    }
    return Math.max(0, Math.min(100, Math.round((safeOffset / safeDuration) * 100)));
  }, [safeDuration, safeOffset]);
  const isSoundscapeLayout = activeLayout === "soundscape";
  const localSongFresh =
    localPlaybackStartedAt > 0 && Date.now() - Number(localPlaybackStartedAt || 0) < 12000;
  const currentMusicName = String(
    status.cur_music ||
      (status.is_playing ? rememberedPlayingSong : "") ||
      (localSongFresh ? localPlaybackSong : "") ||
      "",
  ).trim();
  const playbackText = status.is_playing ? `正在播放：${currentMusicName || "未知歌曲"}` : "空闲";

  useEffect(() => {
    statusRef.current = status;
  }, [status]);

  useEffect(() => {
    localPlaybackStartedAtRef.current = localPlaybackStartedAt;
  }, [localPlaybackStartedAt]);

  useEffect(() => {
    localPlaybackDurationRef.current = localPlaybackDuration;
  }, [localPlaybackDuration]);

  useEffect(() => {
    localPlaybackSongRef.current = localPlaybackSong;
  }, [localPlaybackSong]);

  useEffect(() => {
    rememberedPlayingSongRef.current = rememberedPlayingSong;
  }, [rememberedPlayingSong]);

  useEffect(() => {
    if (activeDid) {
      saveLocal("xm_ui_active_did", activeDid);
    }
  }, [activeDid]);

  useEffect(() => {
    activeDidRef.current = activeDid;
  }, [activeDid]);

  useEffect(() => {
    if (playlist) {
      saveLocal("xm_ui_playlist", playlist);
    }
  }, [playlist]);

  useEffect(() => {
    if (music) {
      saveLocal("xm_ui_music", music);
    }
  }, [music]);

  useEffect(() => {
    document.body.classList.add("index_page");
    document.body.classList.add("fonts-loaded");
    document.body.classList.add("webui-refactor-mode");
    return () => {
      document.body.classList.remove("index_page");
      document.body.classList.remove("webui-refactor-mode");
    };
  }, []);

  useEffect(() => {
    if (!showSettings) {
      return;
    }
    const timer = window.setInterval(() => {
      void loadAuthStatus();
    }, 2500);
    return () => window.clearInterval(timer);
  }, [showSettings]);

  useEffect(() => {
    if (!qrcodeExpireAt) {
      return;
    }
    const tick = () => {
      const remain = Math.max(0, Math.ceil((qrcodeExpireAt - Date.now()) / 1000));
      setQrcodeRemain(remain);
      if (remain === 0) {
        setQrcodeStatus("二维码已过期，请重新获取");
        setQrcodeExpireAt(0);
      } else {
        setQrcodeStatus(`请使用米家 App 扫码（约 ${remain}s）`);
      }
    };
    tick();
    const timer = window.setInterval(tick, 1000);
    return () => window.clearInterval(timer);
  }, [qrcodeExpireAt]);

  useEffect(() => {
    const loggedIn = Boolean(authStatus.runtime_auth_ready);
    const inProgress = Boolean(authStatus.login_in_progress);
    if (loggedIn && !inProgress) {
      if (qrcodeUrl) {
        setQrcodeUrl("");
      }
      if (qrcodeExpireAt) {
        setQrcodeExpireAt(0);
        setQrcodeRemain(0);
      }
      setQrcodeStatus("已登录，可获取设备列表");
      void loadDevices();
    }
  }, [authStatus.runtime_auth_ready, authStatus.login_in_progress]);

  async function tryResolveDid(device: Device): Promise<string> {
    const candidates = deviceCandidates(device);
    for (const did of candidates) {
      const out = await getPlayerState(did);
      if (isApiOk(out)) {
        return did;
      }
    }
    return "";
  }

  async function loadVersion() {
    const out = await getSystemStatus();
    if (isApiOk(out)) {
      setVersion(String(out.data.version || ""));
      return;
    }
    setVersion("");
  }

  async function loadAuthStatus() {
    const out = (await fetchAuthStatus()) as AuthStatus;
    setAuthStatus(out);
  }

  function withPublicBaseCompat(data: Record<string, unknown>, baseUrl: string): Record<string, unknown> {
    const normalized = normalizeBaseUrlInput(baseUrl);
    if (!normalized) {
      return data;
    }
    const legacy = baseUrlToLegacyHostPort(normalized);
    if (!legacy) {
      return data;
    }
    return {
      ...data,
      public_base_url: normalized,
      hostname: legacy.hostname,
      public_port: legacy.public_port,
    };
  }

  async function loadSettingData() {
    const out = (await fetchSettingsWithDevices<Record<string, unknown>>()) as Record<string, unknown> & { device_list?: Device[] };
    const dids = String(out.mi_did || "")
      .split(",")
      .map((x) => x.trim())
      .filter(Boolean);

    const manual = normalizeBaseUrlInput(out.public_base_url);
    const legacy = legacyBaseUrl(out.hostname, out.public_port);
    const auto = autoDetectedBaseUrl;
    let hydrated = { ...out };
    if (!manual) {
      if (legacy) {
        hydrated = withPublicBaseCompat(hydrated, legacy);
      } else if (auto) {
        hydrated = withPublicBaseCompat(hydrated, auto);
      }
    }

    setSettingData(hydrated);
    setSettingJsonText(JSON.stringify(hydrated, null, 2));
    setSelectedSettingDids(dids);
    const rows = Array.isArray(out.device_list) ? out.device_list : [];
    setSettingDeviceList(rows);
    if (rows.length) {
      setDevices(rows);
      const fallbackDid = deviceCandidates(rows[0])[0] || "";
      const preferredRaw = dids[0] || fallbackDid;
      const owner = rows.find((d) => deviceCandidates(d).includes(preferredRaw));
      const preferredDid = owner ? preferredDidFromDevice(owner) : preferredRaw;
      if (preferredDid) {
        setActiveDid((prev) => prev || preferredDid);
      }
    }
    setPullAskEnabled(Boolean(out.enable_pull_ask));

    if (!publicBaseMigratedRef.current && !manual) {
      const shouldUseLegacy = Boolean(legacy) && !legacyLooksUnconfigured(out.hostname, out.public_port);
      const target = shouldUseLegacy ? legacy : auto;
      if (target) {
        publicBaseMigratedRef.current = true;
        const payload = {
          ...withPublicBaseCompat({ ...out }, target),
          mi_did: dids.join(",") || String(out.mi_did || ""),
        };
        await saveSettingsPayload(payload);
      }
    }
  }

  function updateSettingField(key: string, value: unknown) {
    setSettingData((prev) => {
      const next = { ...prev, [key]: value };
      setSettingJsonText(JSON.stringify(next, null, 2));
      return next;
    });
  }

  function toggleSettingDid(did: string, checked: boolean) {
    setSelectedSettingDids((prev) => {
      if (checked) {
        return prev.includes(did) ? prev : [...prev, did];
      }
      return prev.filter((x) => x !== did);
    });
  }

  async function loadDevices() {
    const v1Out = await v1GetDevices();
    const rows: Device[] = isApiOk(v1Out)
      ? (v1Out.data.devices || []).map((d) => ({ miotDID: d.device_id, name: d.name || d.model || d.device_id }))
      : [];
    setDevices(rows);
    if (!rows.length) {
      setMessage("未获取到设备，请在设置页完成认证登录。");
      return;
    }
    const fallbackDid = deviceCandidates(rows[0])[0] || "";
    const currentDid = String(activeDid || "").trim();
    if (currentDid) {
      const owner = rows.find((d) => deviceCandidates(d).includes(currentDid));
      if (!owner) {
        removeLocal("xm_ui_active_did");
        setActiveDid("");
      } else {
        const preferred = preferredDidFromDevice(owner);
        if (preferred && preferred !== currentDid) {
          setActiveDid(preferred);
          saveLocal("xm_ui_active_did", preferred);
        }
      }
    }
    if (fallbackDid) {
      setActiveDid((prev) => prev || fallbackDid);
      void (async () => {
        const probe = await getPlayerState(fallbackDid);
        if (isApiOk(probe)) {
          return;
        }
        const resolved = await tryResolveDid(rows[0]);
        if (resolved) {
          setActiveDid(resolved);
          return;
        }
        setMessage("未匹配到可用设备 DID，请在设置页刷新设备列表。");
      })();
      return;
    }

    const did = await tryResolveDid(rows[0]);
    if (did) {
      setActiveDid(did);
      return;
    }
    setMessage("未匹配到可用设备 DID，请在设置页刷新设备列表。");
  }

  async function loadPlaylists() {
    const out = await getLibraryPlaylists();
    if (!isApiOk(out)) {
      setPlaylists({});
      setMessage("获取歌单失败");
      return;
    }
    const playlistsData = out.data.playlists || {};
    setPlaylists(playlistsData);
    const names = Object.keys(playlistsData);
    if (names.length) {
      setPlaylist((prev) => (prev && playlistsData[prev] ? prev : names[0]));
    }
  }

  function isLatestStatusRequest(seq: number, did: string): boolean {
    return seq === statusRequestSeqRef.current && activeDidRef.current === did;
  }

  function beginActionSync(): number {
    const next = activeActionSeqRef.current + 1;
    activeActionSeqRef.current = next;
    return next;
  }

  function isActionSyncCurrent(seq: number): boolean {
    return activeActionSeqRef.current === seq;
  }

  function finishActionSync(seq: number): void {
    if (activeActionSeqRef.current === seq) {
      activeActionSeqRef.current = 0;
    }
  }

  async function waitForPlayerState(
    did: string,
    actionSeq: number,
    matcher: (state: PlayingInfo) => boolean,
    attempts = 8,
    delayMs = 500,
  ): Promise<PlayingInfo | null> {
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      if (!isActionSyncCurrent(actionSeq) || activeDidRef.current !== did) {
        return null;
      }
      const current = await loadStatus(did);
      if (!isActionSyncCurrent(actionSeq) || activeDidRef.current !== did) {
        return null;
      }
      if (current && matcher(current)) {
        return current;
      }
      if (attempt < attempts - 1) {
        await new Promise<void>((resolve) => {
          window.setTimeout(resolve, delayMs);
        });
      }
    }
    return null;
  }

  async function loadStatus(did: string): Promise<PlayingInfo | null> {
    if (!did) {
      return null;
    }
    const requestSeq = statusRequestSeqRef.current + 1;
    statusRequestSeqRef.current = requestSeq;
    const stateResp = await Promise.resolve(getPlayerState(did)).then(
      (value) => ({ status: "fulfilled" as const, value }),
      (reason) => ({ status: "rejected" as const, reason }),
    );

    const mergePlayingViewState = (prev: PlayingInfo, merged: PlayingInfo, fallbackSong: string): PlayingInfo => {
      const mergedOffsetRaw = Number(merged.offset);
      const mergedHasOffset = Number.isFinite(mergedOffsetRaw) && mergedOffsetRaw >= 0;
      const mergedDurationRaw = Number(merged.duration);
      const mergedHasDuration = Number.isFinite(mergedDurationRaw) && mergedDurationRaw > 0;
      const mergedSong = String(merged.cur_music || "").trim();
      const prevSong = String(prev.cur_music || "").trim();
      const prevOffset = Math.max(0, Number(prev.offset || 0));
      const prevDuration = Math.max(0, Number(prev.duration || 0));
      const elapsed = Math.max(
        0,
        Math.floor((Date.now() - Number(localPlaybackStartedAtRef.current || 0)) / 1000),
      );

      let resolvedOffset = Math.max(prevOffset, elapsed);
      if (mergedHasOffset && mergedOffsetRaw > 0) {
        resolvedOffset = Math.floor(mergedOffsetRaw);
      } else if (mergedHasOffset && mergedOffsetRaw === 0) {
        const songChanged = Boolean(mergedSong && prevSong && mergedSong !== prevSong);
        const durationChanged = mergedHasDuration && Math.abs(Math.floor(mergedDurationRaw) - prevDuration) >= 8;
        const atBoundary = prevDuration > 0 && prevOffset >= Math.max(0, prevDuration - 5);
        if (songChanged || durationChanged || atBoundary) {
          resolvedOffset = 0;
        }
      }

      const resolvedDuration = mergedHasDuration
        ? Math.floor(mergedDurationRaw)
        : Math.max(prevDuration, Number(localPlaybackDurationRef.current || 0));

      if (resolvedDuration > 0 && resolvedOffset > resolvedDuration + 5) {
        resolvedOffset = mergedHasOffset && mergedOffsetRaw === 0 ? 0 : Math.min(resolvedOffset, resolvedDuration);
      }

      return {
        ...prev,
        ...merged,
        cur_music: String(merged.cur_music || prev.cur_music || fallbackSong || ""),
        duration: resolvedDuration,
        offset: resolvedOffset,
      };
    };

    if (stateResp.status === "fulfilled") {
      const envelope = stateResp.value as ApiEnvelope<PlayerStateData>;
      if (!isApiOk(envelope)) {
        if (Number(envelope.code || -1) === 40004) {
          const owner = devices.find((d) => deviceCandidates(d).includes(did));
          const preferred = owner ? preferredDidFromDevice(owner) : "";
          if (preferred && preferred !== did) {
            setActiveDid(preferred);
            saveLocal("xm_ui_active_did", preferred);
          }
        }
      } else {
        const next = (envelope.data || {}) as PlayingInfo;
        const pending = pendingPlayRef.current;
        let merged: PlayingInfo = {
          ...next,
          is_playing: Boolean(next.is_playing),
          offset: Math.max(0, Number(next.offset || 0)),
          duration: Math.max(0, Number(next.duration || 0)),
        };
        if (Date.now() < stopSuppressUntilRef.current) {
          merged = {
            ...merged,
            is_playing: false,
            offset: 0,
          };
        }
        if (merged.is_playing) {
          lastPositivePlaybackAtRef.current = Date.now();
        } else {
          const liveStatus = statusRef.current;
          const withinStabilityWindow = Date.now() - lastPositivePlaybackAtRef.current < 12000;
          if (withinStabilityWindow) {
            merged.is_playing = true;
            merged.cur_music = String(
              merged.cur_music ||
                liveStatus.cur_music ||
                localPlaybackSongRef.current ||
                rememberedPlayingSongRef.current ||
                "",
            );
            merged.duration =
              Number(merged.duration || 0) ||
              Number(liveStatus.duration || 0) ||
              localPlaybackDurationRef.current;
            merged.offset =
              Number(merged.offset || 0) ||
              Number(liveStatus.offset || 0) ||
              Math.max(
                0,
                Math.floor(
                  (Date.now() - Number(localPlaybackStartedAtRef.current || 0)) / 1000,
                ),
              );
          }
        }
        if (merged.is_playing && !String(merged.cur_music || "").trim()) {
          merged.cur_music = "";
        }

        if (merged.is_playing && String(merged.cur_music || "").trim()) {
          const remembered = String(merged.cur_music || "").trim();
          saveRememberedPlayingSong(did, remembered);
          setRememberedPlayingSong(remembered);
          rememberedPlayingSongRef.current = remembered;
          const startedAtFromOffset = Math.max(
            0,
            Date.now() - Math.max(0, Math.floor(Number(merged.offset || 0))) * 1000,
          );
          saveLocal(
            playbackSnapshotKey(did),
            JSON.stringify({
              song: remembered,
              started_at: startedAtFromOffset || Date.now(),
              duration: Math.max(0, Math.floor(Number(merged.duration || 0))),
            }),
          );
        }
        if (isLatestStatusRequest(requestSeq, did)) {
          if (pending && pending.did === did) {
            if (merged.is_playing) {
              pendingPlayRef.current = null;
              setStatus((prev) => mergePlayingViewState(prev, merged, pending.song));
            } else if (Date.now() < pending.expiresAt) {
              setStatus((prev) => ({ ...mergePlayingViewState(prev, merged, pending.song), is_playing: true }));
            } else {
              pendingPlayRef.current = null;
              if (!merged.is_playing) {
                setLocalPlaybackStartedAt(0);
                setLocalPlaybackDuration(0);
                setLocalPlaybackSong("");
                localPlaybackStartedAtRef.current = 0;
                localPlaybackDurationRef.current = 0;
                localPlaybackSongRef.current = "";
                removeLocal(playbackSnapshotKey(did));
              }
              setStatus(merged);
            }
          } else {
            if (merged.is_playing) {
              setStatus((prev) => mergePlayingViewState(prev, merged, localPlaybackSongRef.current));
            } else {
              setLocalPlaybackStartedAt(0);
              setLocalPlaybackDuration(0);
              setLocalPlaybackSong("");
              localPlaybackStartedAtRef.current = 0;
              localPlaybackDurationRef.current = 0;
              localPlaybackSongRef.current = "";
              removeLocal(playbackSnapshotKey(did));
              setStatus(merged);
            }
          }
        } else {
          return merged;
        }
        return merged;
      }
    }
    return null;
  }

  useEffect(() => {
    void (async () => {
      await Promise.allSettled([loadVersion(), loadAuthStatus(), loadSettingData(), loadPlaylists()]);
      void loadDevices();
    })();
  }, []);

  useEffect(() => {
    if (!activeDid) {
      setRememberedPlayingSong("");
      rememberedPlayingSongRef.current = "";
      lastAutoSyncedPlayingSongRef.current = "";
      return;
    }
    lastAutoSyncedPlayingSongRef.current = "";
    setLocalPlaybackStartedAt(0);
    setLocalPlaybackDuration(0);
    setLocalPlaybackSong("");
    localPlaybackStartedAtRef.current = 0;
    localPlaybackDurationRef.current = 0;
    localPlaybackSongRef.current = "";
    const remembered = loadRememberedPlayingSong(activeDid);
    setVolume(loadRememberedVolume(activeDid));
    setRememberedPlayingSong(remembered);
    rememberedPlayingSongRef.current = remembered;
    void loadStatus(activeDid);
    const timer = window.setInterval(() => {
      if (activeActionSeqRef.current) {
        return;
      }
      void loadStatus(activeDid);
    }, 4000);
    return () => window.clearInterval(timer);
  }, [activeDid]);

  useEffect(() => {
    if (!localPlaybackStartedAt) {
      return;
    }
    const timer = window.setInterval(() => {
      setStatus((prev) => {
        if (!prev.is_playing) {
          return prev;
        }
        const elapsed = Math.max(0, Math.floor((Date.now() - localPlaybackStartedAt) / 1000));
        const currentOffset = Number(prev.offset || 0);
        const currentDuration = Number(prev.duration || 0);
        const offsetLooksReasonable =
          currentOffset > 0 &&
          ((currentDuration > 0 && currentOffset <= currentDuration + 30) ||
            (currentDuration <= 0 && currentOffset < 24 * 3600));
        const nextOffset = offsetLooksReasonable ? Math.max(currentOffset, elapsed) : elapsed;
        const nextDuration = Number(prev.duration || 0) > 0 ? Number(prev.duration || 0) : localPlaybackDuration;
        const nextMusic = String(prev.cur_music || "").trim() || localPlaybackSong;
        if (
          nextOffset === Number(prev.offset || 0) &&
          nextDuration === Number(prev.duration || 0) &&
          nextMusic === String(prev.cur_music || "")
        ) {
          return prev;
        }
        return {
          ...prev,
          offset: nextOffset,
          duration: nextDuration,
          cur_music: nextMusic,
        };
      });
    }, 1000);
    return () => window.clearInterval(timer);
  }, [localPlaybackStartedAt, localPlaybackDuration, localPlaybackSong]);

  useEffect(() => {
    if (!songs.length) {
      setMusic("");
      return;
    }
    setMusic((prev) => (prev && songs.includes(prev) ? prev : songs[0]));
  }, [songs]);

  useEffect(() => {
    if (!status.is_playing) {
      return;
    }
    if (!Object.keys(playlists).length) {
      return;
    }
    const playingName = String(status.cur_music || rememberedPlayingSong || localPlaybackSong || "").trim();
    if (!playingName) {
      return;
    }
    if (playingName === lastAutoSyncedPlayingSongRef.current) {
      return;
    }
    const currentListSongs = playlists[playlist] || [];
    if (currentListSongs.includes(playingName)) {
      if (music !== playingName) {
        setMusic(playingName);
      }
      lastAutoSyncedPlayingSongRef.current = playingName;
      return;
    }
    const matchedPlaylist = Object.keys(playlists).find((name) => (playlists[name] || []).includes(playingName));
    if (matchedPlaylist) {
      if (playlist !== matchedPlaylist) {
        setPlaylist(matchedPlaylist);
      }
      if (music !== playingName) {
        setMusic(playingName);
      }
      lastAutoSyncedPlayingSongRef.current = playingName;
      return;
    }
    lastAutoSyncedPlayingSongRef.current = playingName;
  }, [
    status.is_playing,
    status.cur_music,
    rememberedPlayingSong,
    localPlaybackSong,
    playlists,
    playlist,
    music,
  ]);

  function requireDid(): boolean {
    if (activeDid) {
      return true;
    }
    setMessage("当前无可用设备，无法执行设备控制。");
    return false;
  }

  async function switchTrack(action: "previous" | "next", okText: string) {
    if (!requireDid()) {
      return;
    }
    const actionSeq = beginActionSync();
    const did = activeDid;
    const baselineSong = String(statusRef.current.cur_music || "").trim();
    const baselineOffset = Math.max(0, Number(statusRef.current.offset || 0));
    const baselineDuration = Math.max(0, Number(statusRef.current.duration || 0));

    const hasTrackSwitched = (): boolean => {
      const nowSong = String(statusRef.current.cur_music || "").trim();
      const nowOffset = Math.max(0, Number(statusRef.current.offset || 0));
      const nowDuration = Math.max(0, Number(statusRef.current.duration || 0));
      if (baselineSong && nowSong && nowSong !== baselineSong) {
        return true;
      }
      if (baselineDuration > 0 && nowDuration > 0 && Math.abs(nowDuration - baselineDuration) >= 8 && nowOffset <= 5) {
        return true;
      }
      if (baselineDuration > 0 && baselineOffset >= Math.max(0, baselineDuration - 5) && nowOffset <= 3) {
        return true;
      }
      if (baselineOffset > 20 && nowOffset + 15 < baselineOffset) {
        return true;
      }
      return false;
    };

    setMessage(`${okText}，正在同步播放信息...`);
    try {
      const out = action === "previous" ? await v1Previous(did) : await v1Next(did);
      if (isApiOk(out)) {
        const synced = await waitForPlayerState(did, actionSeq, hasTrackSwitched, 12, 900);
        if (synced && hasTrackSwitched()) {
          setMessage(okText);
        } else {
          setMessage(`${okText}，设备已执行，页面状态已刷新`);
          await loadStatus(did);
        }
        return;
      }
      const err = apiErrorInfo(out);
      setMessage(err.message || "执行失败");
      await loadStatus(did);
    } finally {
      finishActionSync(actionSeq);
    }
  }

  async function playSongByName(songName: string) {
    if (!requireDid()) {
      return;
    }
    const actionSeq = beginActionSync();
    const deviceId = activeDid;
    const picked = String(songName || "").trim() || String(songs[0] || "").trim();
    if (!picked) {
      finishActionSync(actionSeq);
      setMessage("当前歌单为空，请先刷新歌单或切换列表");
      return;
    }
    setMusic(picked);
    setMessage(`正在切换到 ${picked}...`);
    try {
      const info = await getLibraryMusicInfo(picked);
      const infoDuration = isApiOk(info) ? Number(info.data.duration_seconds || 0) : 0;
      const playResp = await v1Play({
        device_id: deviceId,
        query: picked,
        source_hint: "local_library",
        options: {
          title: picked,
          context_hint: {
            context_type: "playlist",
            context_name: playlist,
          },
        },
      });

      if (isApiOk(playResp)) {
        stopSuppressUntilRef.current = 0;
        const startedAt = Date.now();
        pendingPlayRef.current = {
          did: deviceId,
          song: picked,
          expiresAt: startedAt + 15000,
        };
        setStatus((prev) => ({
          ...prev,
          is_playing: true,
          cur_music: picked,
          offset: 0,
          duration: infoDuration > 0 ? infoDuration : Number(prev.duration || 0),
        }));
        saveRememberedPlayingSong(deviceId, picked);
        setRememberedPlayingSong(picked);
        rememberedPlayingSongRef.current = picked;
        setLocalPlaybackSong(picked);
        localPlaybackSongRef.current = picked;
        setLocalPlaybackStartedAt(startedAt);
        localPlaybackStartedAtRef.current = startedAt;
        setLocalPlaybackDuration(infoDuration > 0 ? infoDuration : 0);
        localPlaybackDurationRef.current = infoDuration > 0 ? infoDuration : 0;
        lastPositivePlaybackAtRef.current = startedAt;
        saveLocal(
          playbackSnapshotKey(deviceId),
          JSON.stringify({
            song: picked,
            started_at: startedAt,
            duration: infoDuration > 0 ? Math.floor(infoDuration) : 0,
          }),
        );
        const synced = await waitForPlayerState(
          deviceId,
          actionSeq,
          (state) => {
            if (!state.is_playing && !pendingPlayRef.current) {
              return false;
            }
            const current = String(state.cur_music || "").trim();
            return !current || current === picked;
          },
          12,
          700,
        );
        if (synced) {
          const syncedStartedAt = Math.max(0, Date.now() - Math.max(0, Math.floor(Number(synced.offset || 0))) * 1000);
          setLocalPlaybackStartedAt(syncedStartedAt);
          localPlaybackStartedAtRef.current = syncedStartedAt;
          setLocalPlaybackDuration(infoDuration > 0 ? infoDuration : Math.max(0, Number(synced.duration || 0)));
          localPlaybackDurationRef.current = infoDuration > 0 ? infoDuration : Math.max(0, Number(synced.duration || 0));
          lastPositivePlaybackAtRef.current = Date.now();
          saveLocal(
            playbackSnapshotKey(deviceId),
            JSON.stringify({
              song: picked,
              started_at: syncedStartedAt,
              duration: infoDuration > 0 ? Math.floor(infoDuration) : Math.max(0, Math.floor(Number(synced.duration || 0))),
            }),
          );
          setMessage(`已发送播放《${picked}》`);
        } else {
          setMessage(`播放指令已发送，正在等待设备切换到 ${picked}`);
          await loadStatus(deviceId);
        }
      } else {
        pendingPlayRef.current = null;
        const err = apiErrorInfo(playResp);
        setMessage(`播放失败：${explainPlaybackError(err.errorCode, err.message, err.stage)}`);
        await loadStatus(deviceId);
      }
    } catch (err) {
      pendingPlayRef.current = null;
      const reason = err instanceof Error ? err.message : String(err || "未知错误");
      setMessage(`播放失败：${reason}`);
      await loadStatus(deviceId);
    } finally {
      finishActionSync(actionSeq);
    }
  }

  async function playCurrent() {
    await playSongByName(music);
  }

  async function togglePlayMode() {
    if (switchingPlayMode) {
      return;
    }
    const prev = playModeIndex;
    const next = (playModeIndex + 1) % PLAY_MODES.length;
    setPlayModeIndex(next);
    if (!requireDid()) {
      setPlayModeIndex(prev);
      return;
    }
    setSwitchingPlayMode(true);
    try {
      const nextMode = PLAY_MODES[next].value as PlayMode;
      const out = await v1SetPlayMode(activeDid, nextMode);
      if (isApiOk(out)) {
        setMessage(`已切换为${PLAY_MODES[next].label}`);
        await Promise.allSettled([loadStatus(activeDid), loadSettingData()]);
        return;
      }
      setPlayModeIndex(prev);
      const err = apiErrorInfo(out);
      setMessage(err.message || "切换播放模式失败");
    } finally {
      setSwitchingPlayMode(false);
    }
  }

  async function playLink(proxy: boolean) {
    if (!requireDid()) {
      return;
    }
    try {
      const url = String(linkUrl || "").trim();
      if (!url) {
        setMessage("请输入媒体链接后再播放");
        return;
      }

      const out = await v1Play({
        device_id: activeDid,
        query: url,
        source_hint: "auto",
        options: { no_cache: false, prefer_proxy: proxy, prefer_codec: "auto" },
      });
      const data = (out.data || {}) as Record<string, unknown>;
      const extra = (data.extra || {}) as Record<string, unknown>;
      const outcome = (extra.playback_outcome || {}) as Record<string, unknown>;
      const netAudio = (extra.network_audio || {}) as Record<string, unknown>;
      const finalPath = String(outcome.final_path || netAudio.mode || "");
      const fallbackTriggered = Boolean(outcome.fallback_triggered);
      if (isApiOk(out)) {
        setMessage(
          `播放已发送（来源: ${String(data.source_plugin || "unknown")}, 传输: ${String(data.transport || "unknown")}, 路径: ${
            finalPath || "unknown"
          }${fallbackTriggered ? "，已触发回退" : ""}${String(data.sid || out.request_id || "") ? `, sid: ${String(data.sid || out.request_id || "")}` : ""}）。点播模式播完会自动停止。`,
        );
      } else {
        const err = apiErrorInfo(out);
        setMessage(`播放失败：${explainPlaybackError(err.errorCode, err.message, err.stage)}`);
      }
    } catch (err) {
      const reason = err instanceof Error ? err.message : String(err || "未知错误");
      setMessage(`播放失败：${reason}`);
    }
  }

  async function playTts() {
    if (!requireDid()) {
      return;
    }
    const out = await v1Tts(activeDid, ttsText);
    if (isApiOk(out)) {
      setMessage("文字播放已发送");
      return;
    }
    const err = apiErrorInfo(out);
    setMessage(err.message || err.errorCode || "文字播放失败");
  }

  async function timedShutdown(minutes: number) {
    if (!requireDid()) {
      return;
    }
    const out = await v1SetShutdownTimer(activeDid, minutes);
    if (isApiOk(out)) {
      setMessage(`${minutes}分钟后关机已发送`);
      return;
    }
    const err = apiErrorInfo(out);
    setMessage(err.message || "定时关机设置失败");
  }

  async function addCurrentToFavorites() {
    if (!requireDid()) {
      return;
    }
    const musicName = String(status.cur_music || music || "").trim();
    if (!musicName) {
      setMessage("当前没有可收藏的歌曲");
      return;
    }
    const out = await v1AddFavorite(activeDid, musicName);
    if (isApiOk(out)) {
      setMessage(`已加入收藏：${musicName}`);
      return;
    }
    const err = apiErrorInfo(out);
    setMessage(err.message || "加入收藏失败");
  }

  async function searchOnline() {
    const kw = searchKeyword.trim();
    if (!kw) {
      setMessage("请输入搜索关键词");
      return;
    }
    const out = (await searchOnlineMusic(kw)) as {
      data?: OnlineSearchItem[];
      success?: boolean;
      error?: string;
    };
    if (out.success === false) {
      setMessage(out.error || "搜索失败");
      setSearchResults([]);
      return;
    }
    setSearchResults(out.data || []);
    setSelectedSearchIndex(-1);
    setMessage(`搜索到 ${out.data?.length || 0} 条结果`);
  }

  async function confirmSearch() {
    if (!requireDid()) {
      return;
    }
    const item = searchResults[selectedSearchIndex];
    if (!item) {
      setMessage("请先选择搜索结果");
      return;
    }
    const title = String(item.title || item.name || "");
    if (!title) {
      setMessage("选中结果缺少歌曲名");
      return;
    }
    const out = await v1Play({
      device_id: activeDid,
      query: title,
      source_hint: "auto",
      options: { search_key: searchKeyword || "" },
    });
    if (isApiOk(out)) {
      setMessage("已发送播放");
      setShowSearch(false);
      await loadStatus(activeDid);
    } else {
      const err = apiErrorInfo(out);
      setMessage(`播放失败：${explainPlaybackError(err.errorCode, err.message, err.stage)}`);
    }
  }

  async function getQrcode() {
    const out = (await fetchQrcode<QrcodeResp>()) as QrcodeResp;
    if (out.success === false) {
      setQrcodeStatus(out.message || out.error || "二维码获取失败");
      return;
    }
    if (out.already_logged_in) {
      setQrcodeUrl("");
      setQrcodeExpireAt(0);
      setQrcodeStatus(out.message || "已登录，无需扫码");
      await loadAuthStatus();
      await loadSettingData();
      await loadDevices();
      return;
    }
    setQrcodeUrl(out.qrcode_url || "");
    const expireSeconds = Number(out.expire_seconds || 120);
    setQrcodeExpireAt(Date.now() + expireSeconds * 1000);
    setQrcodeRemain(expireSeconds);
    setQrcodeStatus(`请使用米家 App 扫码（约 ${expireSeconds}s）`);
    await loadAuthStatus();
  }

  async function refreshAuthRuntime() {
    const out = (await reloadAuthRuntime()) as Record<string, unknown>;
    if (out.runtime_auth_ready) {
      setQrcodeStatus("运行时刷新成功，正在更新设备列表");
    } else {
      setQrcodeStatus(String(out.last_error || "运行时刷新失败"));
    }
    await loadAuthStatus();
    await loadSettingData();
    await loadDevices();
  }

  async function logoutAuth() {
    await logoutAuthRequest();
    setQrcodeUrl("");
    setQrcodeExpireAt(0);
    setQrcodeRemain(0);
    setQrcodeStatus("已退出登录");
    await loadAuthStatus();
    await loadSettingData();
    await loadDevices();
  }

  async function resetPublicBaseUrlToAuto() {
    if (!autoDetectedBaseUrl) {
      setMessage("当前无法自动识别访问地址，请手动填写覆盖地址");
      return;
    }
    const next = withPublicBaseCompat({ ...settingData, public_base_url: "" }, autoDetectedBaseUrl);
    updateSettingField("public_base_url", "");
    updateSettingField("hostname", String(next.hostname || ""));
    updateSettingField("public_port", Number(next.public_port || 0));

    const payload: Record<string, unknown> = {
      ...next,
      mi_did: selectedSettingDids.join(","),
    };
    const out = (await saveSettingsPayload(payload)) as unknown;
    if (typeof out === "string" && out.includes("save success")) {
      setMessage(`已恢复自动地址：${autoDetectedBaseUrl}`);
    } else {
      setMessage(`已恢复自动地址：${autoDetectedBaseUrl}`);
    }
    await loadSettingData();
  }

  async function saveSettings() {
    let parsed: Record<string, unknown> = settingData;
    try {
      parsed = JSON.parse(settingJsonText) as Record<string, unknown>;
    } catch {
      setMessage("配置 JSON 格式错误，请先修正");
      return;
    }
    const payload: Record<string, unknown> = {
      ...parsed,
      mi_did: selectedSettingDids.join(","),
    };
    const out = (await saveSettingsPayload(payload)) as unknown;
    if (typeof out === "string" && out.includes("save success")) {
      setMessage("配置已保存");
    } else {
      setMessage("配置已提交");
    }
    await loadSettingData();
    await loadDevices();
    await loadPlaylists();
  }

  useEffect(() => {
    const devices = (settingData.devices || {}) as Record<string, { play_type?: number }>;
    const mode = devices?.[activeDid]?.play_type;
    if (typeof mode === "number" && mode >= 0 && mode < PLAY_MODES.length) {
      setPlayModeIndex(mode);
    }
  }, [activeDid, settingData]);

  async function togglePullAsk() {
    const next = !pullAskEnabled;
    const out = (await updateSystemSetting<{ success?: boolean; message?: string }>({ enable_pull_ask: next })) as {
      success?: boolean;
      message?: string;
    };
    if (out.success) {
      setPullAskEnabled(next);
      updateSettingField("enable_pull_ask", next);
      setMessage(next ? "语音口令已开启" : "语音口令已关闭");
      return;
    }
    setMessage(out.message || "切换失败，请重试");
  }

  async function fetchMusicListJson() {
    const out = (await fetchPlaylistJson<{ ret?: string; content?: string }>(fieldValue("music_list_url"))) as {
      ret?: string;
      content?: string;
    };
    if (out.ret === "OK") {
      updateSettingField("music_list_json", out.content || "");
      setMessage("歌单内容已获取");
      return;
    }
    setMessage(out.ret || "获取歌单失败");
  }

  async function refreshMusicTag() {
    const out = (await refreshMusicTagRequest<{ ret?: string }>()) as { ret?: string };
    setMessage(out.ret || "已触发刷新");
  }

  function clearCache() {
    localStorage.clear();
    setMessage("浏览器缓存已清除");
  }

  async function cleanTempDir() {
    const out = (await cleanTempDirRequest<{ ret?: string }>()) as { ret?: string };
    setMessage(out.ret || "临时目录清理已触发");
  }

  async function onThemeCssFileSelected(file: File | null) {
    const out = await uploadThemePackage(file);
    if (!file) {
      return;
    }
    if (out.ok) {
      setMessage(`主题已应用：${out.name || file.name}`);
    } else {
      setMessage(out.error || "主题包校验失败");
    }
  }

  function applySoundscapePlaylist(nextPlaylist: string) {
    setPlaylist(nextPlaylist);
    const nextSongs = playlists[nextPlaylist] || [];
    if (!nextSongs.length) {
      setMusic("");
      return;
    }
    setMusic(nextSongs[0]);
  }

  function fieldValue(key: string): string {
    const v = settingData[key];
    if (v === undefined || v === null) {
      return "";
    }
    if (typeof v === "string") {
      return v;
    }
    if (typeof v === "number" || typeof v === "boolean") {
      return String(v);
    }
    try {
      return JSON.stringify(v);
    } catch {
      return "";
    }
  }

  function renderSettingField(field: SettingField) {
    const value = fieldValue(field.key);
    if (field.kind === "select") {
      const options = field.options || [];
      return (
        <>
          <label htmlFor={field.key}>{field.label}:</label>
          <select id={field.key} value={value} onChange={(e) => updateSettingField(field.key, e.target.value)}>
            {options.map((opt) => (
              <option key={`${field.key}-${opt.value || "empty"}`} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </>
      );
    }
    if (field.kind === "bool") {
      return (
        <>
          <label htmlFor={field.key}>{field.label}:</label>
          <select
            id={field.key}
            value={String(value === "true")}
            onChange={(e) => updateSettingField(field.key, e.target.value === "true")}
          >
            <option value="true">true</option>
            <option value="false">false</option>
          </select>
        </>
      );
    }
    if (field.kind === "number") {
      return (
        <>
          <label htmlFor={field.key}>{field.label}:</label>
          <input
            id={field.key}
            type="number"
            value={value}
            onChange={(e) => updateSettingField(field.key, Number(e.target.value || 0))}
          />
        </>
      );
    }
    if (field.kind === "password") {
      return (
        <>
          <label htmlFor={field.key}>{field.label}:</label>
          <input
            id={field.key}
            type="password"
            value={value}
            onChange={(e) => updateSettingField(field.key, e.target.value)}
          />
        </>
      );
    }
    if (field.kind === "textarea") {
      return (
        <>
          <label htmlFor={field.key}>{field.label}:</label>
          <textarea
            id={field.key}
            value={value}
            onChange={(e) => updateSettingField(field.key, e.target.value)}
          />
        </>
      );
    }
    return (
      <>
        <label htmlFor={field.key}>{field.label}:</label>
        <input
          id={field.key}
          type="text"
          value={value}
          onChange={(e) => updateSettingField(field.key, e.target.value)}
        />
      </>
    );
  }

  return (
    <>
      {isSoundscapeLayout ? (
        <div className="soundscape-app" role="main" aria-label="SoundScape 播放器">
          <aside className="soundscape-sidebar">
            <div className="soundscape-brand">
              <h1>SoundScape</h1>
              <p>xiaomusic音乐播放器</p>
            </div>
            <div className="soundscape-sidebar-head">
              <span>播放列表</span>
              <button
                onClick={() =>
                  void (async () => {
                    await v1LibraryRefresh();
                    await loadPlaylists();
                    setMessage("列表已刷新");
                  })()
                }
                className="soundscape-refresh"
              >
                <span className="material-icons" aria-hidden="true">
                  refresh
                </span>
              </button>
            </div>
            <div className="soundscape-playlist-list">
              {Object.keys(playlists).map((name) => (
                <button
                  key={`soundscape-${name}`}
                  className={`soundscape-playlist-item ${playlist === name ? "active" : ""}`}
                  onClick={() => applySoundscapePlaylist(name)}
                >
                  <span className="soundscape-playlist-name">{name}</span>
                  <span className="soundscape-playlist-count">{(playlists[name] || []).length}</span>
                </button>
              ))}
            </div>
            <button className="soundscape-settings-entry" onClick={() => setShowSettings(true)}>
              <span className="material-icons" aria-hidden="true">
                settings
              </span>
              设置
            </button>
          </aside>

          <section className="soundscape-main">
            <header className="soundscape-toolbar">
              <input
                type="text"
                value={soundscapeFilter}
                placeholder="搜索歌曲..."
                onChange={(e) => setSoundscapeFilter(e.target.value)}
              />
              <button onClick={() => setSoundscapeFilter(soundscapeFilter.trim())}>搜索</button>
              <div className="soundscape-toolbar-right">共 {filteredSongs.length} 首</div>
            </header>

            <div className="soundscape-meta-row">
              <span className="soundscape-meta-chip">当前列表：{playlist || "-"}</span>
              <div className="soundscape-device-group">
                <select id="did" className="device-selector" value={activeDid} onChange={(e) => setActiveDid(e.target.value)}>
                  {devices.map((d) => {
                    const did = d.miotDID || d.did || d.deviceID || "";
                    return (
                      <option key={`${did}-${resolveDisplayName(d)}`} value={did}>
                        {resolveDisplayName(d)}
                      </option>
                    );
                  })}
                </select>
                <div
                  id="pullAskToggle"
                  className={`toggle-switch ${pullAskEnabled ? "active" : ""}`}
                  role="switch"
                  aria-checked={pullAskEnabled ? "true" : "false"}
                  aria-label="语音口令开关"
                  onClick={() => void togglePullAsk()}
                >
                  <div className="toggle-slider"></div>
                </div>
              </div>
            </div>

            <div className="soundscape-table-wrap">
              <table className="soundscape-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>歌曲</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredSongs.map((name, idx) => (
                    <tr
                      key={`song-${name}-${idx}`}
                      className={music === name ? "active" : ""}
                      onClick={() => setMusic(name)}
                      onDoubleClick={() => void playSongByName(name)}
                    >
                      <td>{idx + 1}</td>
                      <td>{name}</td>
                      <td>
                        <button onClick={() => void playSongByName(name)}>播放</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <footer className="soundscape-dock">
            <div className="soundscape-dock-left">
              <button onClick={() => void playCurrent()} className="soundscape-dock-main-btn">
                <span className="material-icons-outlined" aria-hidden="true">
                  play_circle_outline
                </span>
              </button>
              <div className="soundscape-dock-song">{playbackText}</div>
            </div>

            <div className="soundscape-dock-center">
              <button onClick={() => void togglePlayMode()}>
                <span className="material-icons" aria-hidden="true">
                  {PLAY_MODES[playModeIndex]?.icon || "shuffle"}
                </span>
              </button>
              <button onClick={() => void switchTrack("previous", "已发送上一首")}>
                <span className="material-icons" aria-hidden="true">
                  skip_previous
                </span>
              </button>
              <button onClick={() => void switchTrack("next", "已发送下一首")}>
                <span className="material-icons" aria-hidden="true">
                  skip_next
                </span>
              </button>
              <button onClick={() => setShowVolume(true)}>
                <span className="material-icons" aria-hidden="true">
                  volume_up
                </span>
              </button>
              <button onClick={() => setShowSearch(true)}>
                <span className="material-icons" aria-hidden="true">
                  search
                </span>
              </button>
            </div>

            <div className="soundscape-dock-right">
              {formatTime(safeOffset)} / {formatTime(safeDuration)}
            </div>
          </footer>
        </div>
      ) : (
        <div className="player" role="main" aria-label="音乐播放器">
          <h1>
            XiaoMusic 播放器
            <a
              href="https://github.com/Akari787/xiaomusic-core"
              target="_blank"
              rel="noopener noreferrer"
              className="version-link"
            >
              <span>{version || "-"}</span>
            </a>
          </h1>

          <label htmlFor="did" className="label-with-toggle">
            选择播放设备:
            <div className="toggle-switch-container">
              <label className="toggle-label">语音口令</label>
              <div
                id="pullAskToggle"
                className={`toggle-switch ${pullAskEnabled ? "active" : ""}`}
                role="switch"
                aria-checked={pullAskEnabled ? "true" : "false"}
                aria-label="语音口令开关"
                onClick={() => void togglePullAsk()}
              >
                <div className="toggle-slider"></div>
              </div>
            </div>
          </label>
          <select id="did" className="device-selector" value={activeDid} onChange={(e) => setActiveDid(e.target.value)}>
            {devices.map((d) => {
              const did = d.miotDID || d.did || d.deviceID || "";
              return (
                <option key={`${did}-${resolveDisplayName(d)}`} value={did}>
                  {resolveDisplayName(d)}
                </option>
              );
            })}
          </select>

          <label htmlFor="music_list" className="label-with-action">
            选择播放列表:
            <div
              className="option-inline"
              role="button"
              tabIndex={0}
              onClick={() =>
                void (async () => {
                  await v1LibraryRefresh();
                  await loadPlaylists();
                  setMessage("列表已刷新");
                })()
              }
            >
              <span className="material-icons" aria-hidden="true">
                refresh
              </span>
              <span className="tooltip">刷新列表</span>
            </div>
          </label>
          <select id="music_list" className="playlist-selector" value={playlist} onChange={(e) => setPlaylist(e.target.value)}>
            {Object.keys(playlists).map((name) => (
              <option key={name} value={name}>
                {`${name} (${(playlists[name] || []).length})`}
              </option>
            ))}
          </select>

          <label htmlFor="music_name" className="label-with-action">
            选择歌曲:
          </label>
          <select id="music_name" className="song-selector" value={music} onChange={(e) => setMusic(e.target.value)}>
            {songs.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>

          <div id="device-audio" className="audio-section">
            <progress className="progress" id="progress" value={progress} max={100}></progress>
            <div className="time-info">
              <span className="current-time" id="current-time">
                {formatTime(safeOffset)}
              </span>
              <div className="current-song" id="playering-music">
                {playbackText}
              </div>
              <span className="duration" id="duration">
                {formatTime(safeDuration)}
              </span>
            </div>
          </div>

          <div className="buttons">
            <div className="player-controls button-group">
              <div id="modeBtn" onClick={() => void togglePlayMode()} className="control-button device-enable" role="button" tabIndex={0}>
                <span className="material-icons" aria-hidden="true">
                  {PLAY_MODES[playModeIndex]?.icon || "shuffle"}
                </span>
                <span className="tooltip">{PLAY_MODES[playModeIndex]?.label || "切换播放模式"}</span>
              </div>
              <div onClick={() => void switchTrack("previous", "已发送上一首")} className="control-button device-enable" role="button" tabIndex={0}>
                <span className="material-icons" aria-hidden="true">
                  skip_previous
                </span>
                <span className="tooltip">上一首</span>
              </div>
              <div onClick={() => void playCurrent()} className="control-button" role="button" tabIndex={0}>
                <span className="material-icons-outlined play" aria-hidden="true">
                  play_circle_outline
                </span>
                <span className="tooltip">播放</span>
              </div>
              <div onClick={() => void switchTrack("next", "已发送下一首")} className="control-button device-enable" role="button" tabIndex={0}>
                <span className="material-icons" aria-hidden="true">
                  skip_next
                </span>
                <span className="tooltip">下一首</span>
              </div>
              <div
                onClick={() =>
                  void (async () => {
                    if (!requireDid()) {
                      return;
                    }
                    const out = await v1Stop(activeDid);
                    if (isApiOk(out)) {
                      stopSuppressUntilRef.current = Date.now() + 6000;
                      pendingPlayRef.current = null;
                      setLocalPlaybackStartedAt(0);
                      setLocalPlaybackDuration(0);
                      setLocalPlaybackSong("");
                      localPlaybackStartedAtRef.current = 0;
                      localPlaybackDurationRef.current = 0;
                      localPlaybackSongRef.current = "";
                      lastPositivePlaybackAtRef.current = 0;
                      removeLocal(playbackSnapshotKey(activeDid));
                      setStatus((prev) => ({
                        ...prev,
                        is_playing: false,
                        offset: 0,
                      }));
                    }
                    setMessage(
                      isApiOk(out)
                        ? "已停止"
                        : (() => {
                            const err = apiErrorInfo(out);
                            return `停止失败：${explainPlaybackError(err.errorCode, err.message, err.stage)}`;
                          })(),
                    );
                    await loadStatus(activeDid);
                  })()
                }
                className="control-button device-enable"
                role="button"
                tabIndex={0}
              >
                <span className="material-icons" aria-hidden="true">
                  stop
                </span>
                <span className="tooltip">关机</span>
              </div>
            </div>

            <div className="mode-controls button-group">
              <div onClick={() => void addCurrentToFavorites()} className="favorite icon-item device-enable" role="button" tabIndex={0}>
                <span className="material-icons" aria-hidden="true">
                  favorite
                </span>
                <p>收藏</p>
              </div>
              <div onClick={() => setShowVolume(true)} className="icon-item device-enable" role="button" tabIndex={0}>
                <span className="material-icons" aria-hidden="true">
                  volume_up
                </span>
                <p>音量</p>
              </div>
              <div onClick={() => setShowSearch(true)} className="icon-item device-enable" role="button" tabIndex={0}>
                <span className="material-icons" aria-hidden="true">
                  search
                </span>
                <p>搜索</p>
              </div>
              <div onClick={() => setShowTimer(true)} className="icon-item device-enable" role="button" tabIndex={0}>
                <span className="material-icons" aria-hidden="true">
                  timer
                </span>
                <p>定时</p>
              </div>
              <div onClick={() => setShowPlaylink(true)} className="icon-item device-enable" role="button" tabIndex={0}>
                <span className="material-icons" aria-hidden="true">
                  emoji_nature
                </span>
                <p>测试</p>
              </div>
              <div onClick={() => setShowSettings(true)} className="icon-item" role="button" tabIndex={0}>
                <span className="material-icons" aria-hidden="true">
                  settings
                </span>
                <p>设置</p>
              </div>
            </div>
          </div>
        </div>
      )}

      <div
        className={`component-overlay ${
          showSearch || showTimer || showPlaylink || showVolume || showSettings ? "show" : ""
        }`}
        style={{ display: showSearch || showTimer || showPlaylink || showVolume || showSettings ? "block" : "none" }}
        onClick={() => {
          setShowSearch(false);
          setShowTimer(false);
          setShowPlaylink(false);
          setShowVolume(false);
          setShowSettings(false);
        }}
      ></div>

      <div className={`component ${showSearch ? "show" : ""}`} id="search-component" style={{ display: showSearch ? "block" : "none" }}>
        <h2>搜索歌曲</h2>
        <input
          type="text"
          className="search-input"
          placeholder="请输入搜索关键词(如:MV高清版 周杰伦 七里香)"
          value={searchKeyword}
          onChange={(e) => setSearchKeyword(e.target.value)}
        />
        <div className="component-button-group">
          <button onClick={() => void searchOnline()}>搜索</button>
        </div>
        <label>搜索结果:</label>
        <div className="search-results">
          {searchResults.map((it, idx) => {
            const title = String(it.title || it.name || `结果 ${idx + 1}`);
            return (
              <button
                key={`${title}-${idx}`}
                className={selectedSearchIndex === idx ? "selected-item" : ""}
                onClick={() => setSelectedSearchIndex(idx)}
                style={{ width: "100%", marginBottom: 8 }}
              >
                {title}
                {it.artist ? ` - ${String(it.artist)}` : ""}
              </button>
            );
          })}
        </div>
        <div className="component-button-group">
          <button onClick={() => void confirmSearch()}>确定</button>
          <button onClick={() => setShowSearch(false)} className="close-button">
            关闭
          </button>
        </div>
      </div>

      <div className={`component ${showTimer ? "show" : ""}`} id="timer-component" style={{ display: showTimer ? "block" : "none" }}>
        <h2>定时关机</h2>
        <p className="auth-hint">定时设置会直接通过正式控制接口发送到设备。</p>
        <button onClick={() => void timedShutdown(1)}>1分钟后关机</button>
        <button onClick={() => void timedShutdown(10)}>10分钟后关机</button>
        <button onClick={() => void timedShutdown(30)}>30分钟后关机</button>
        <button onClick={() => void timedShutdown(60)}>60分钟后关机</button>
        <div className="component-button-one">
          <button onClick={() => setShowTimer(false)} className="close-button">
            关闭
          </button>
        </div>
      </div>

      <div className={`component ${showPlaylink ? "show" : ""}`} id="playlink-component" style={{ display: showPlaylink ? "block" : "none" }}>
        <h2>播放测试</h2>
        <div className="card-section">
          <h3 className="card-title">🔗 直链媒体 / 网站媒体</h3>
          <p>支持两类输入：可直接播放的媒体直链，或 YouTube/B站等网站页面链接（自动识别为网站媒体来源）。</p>
          <input type="text" className="search-input" value={linkUrl} onChange={(e) => setLinkUrl(e.target.value)} />
          <div className="component-button-group">
            <button onClick={() => void playLink(false)}>播放链接</button>
            <button onClick={() => void playLink(true)}>代理播放</button>
          </div>
        </div>

        <div className="card-section">
          <h3 className="card-title">💬 播放文字</h3>
          <input type="text" className="search-input" value={ttsText} onChange={(e) => setTtsText(e.target.value)} />
          <div className="component-button-group">
            <button onClick={() => void playTts()}>播放文字</button>
          </div>
        </div>

        {message ? <div className="play-test-message">{message}</div> : null}

        <div className="component-button-one">
          <button onClick={() => setShowPlaylink(false)} className="close-button">
            关闭
          </button>
        </div>
      </div>

      <div className={`component ${showVolume ? "show" : ""}`} id="volume-component" style={{ display: showVolume ? "block" : "none" }}>
        <h2>调节音量</h2>
        <input
          type="range"
          id="volume"
          className="volume-slider"
          min={0}
          max={100}
          value={volume}
          onChange={(e) => setVolume(Number(e.target.value))}
          onMouseUp={() =>
            void (async () => {
              if (!requireDid()) {
                return;
              }
              const out = await v1SetVolume(activeDid, volume);
              if (isApiOk(out)) {
                saveRememberedVolume(activeDid, volume);
                setMessage("音量已设置");
              } else {
                const err = apiErrorInfo(out);
                setMessage(err.message || err.errorCode || "音量设置失败");
              }
              await loadStatus(activeDid);
            })()
          }
          onTouchEnd={() =>
            void (async () => {
              if (!requireDid()) {
                return;
              }
              const out = await v1SetVolume(activeDid, volume);
              if (isApiOk(out)) {
                saveRememberedVolume(activeDid, volume);
                setMessage("音量已设置");
              } else {
                const err = apiErrorInfo(out);
                setMessage(err.message || err.errorCode || "音量设置失败");
              }
              await loadStatus(activeDid);
            })()
          }
        />
        <div className="component-button-one">
          <button onClick={() => setShowVolume(false)} className="close-button">
            关闭
          </button>
        </div>
      </div>

      <div className={`component ${showSettings ? "show" : ""}`} id="settings-component" style={{ display: showSettings ? "block" : "none" }}>
        <h2>XiaoMusic 设置面板</h2>
        <div className="setting-card setting-panel">
          <div className="card-content">
            <label htmlFor="setting-mi-did">*勾选设备(至少勾选1个):</label>
            <button className="option-inline mini-button" onClick={() => void loadSettingData()}>
              获取设备列表
            </button>
            <div id="setting-mi-did" className="device-selection">
              {settingDeviceList.length ? (
                settingDeviceList.map((d) => {
                  const did = String(d.miotDID || "");
                  if (!did) {
                    return null;
                  }
                  const checked = selectedSettingDids.includes(did);
                  return (
                    <label key={`did-${did}`} className="checkbox-label" style={{ display: "block" }}>
                      <input
                        className="custom-checkbox"
                        type="checkbox"
                        checked={checked}
                        onChange={(e) => toggleSettingDid(did, e.target.checked)}
                      />
                      {displayModelName(d)}
                    </label>
                  );
                })
              ) : (
                <div className="login-tips">未发现可用设备，请先完成扫码认证登录。</div>
              )}
            </div>
          </div>
        </div>

        <div className="setting-card setting-panel">
          <h3 className="card-title">认证登录</h3>
          <div className="card-content">
            <div className="component-button-group">
              {!authLoggedIn || authInProgress ? (
                <button onClick={() => void getQrcode()}>{authInProgress ? "重新获取二维码" : "获取二维码"}</button>
              ) : null}
              {authLoggedIn && !authReady ? (
                <button onClick={() => void refreshAuthRuntime()}>刷新运行时</button>
              ) : null}
              {authLoggedIn ? <button onClick={() => void logoutAuth()}>退出登录</button> : null}
            </div>
            <div className="auth-status-item single">
              <span className="auth-label">登录状态</span>
              <span className={`status-pill ${authStatusClass}`}>{authStatusLabel}</span>
            </div>
            {!authLoggedIn || authInProgress ? (
              <p className="auth-hint">{qrcodeExpireAt ? `请使用米家 App 扫码（约 ${qrcodeRemain}s）` : qrcodeStatus}</p>
            ) : null}
            {qrcodeUrl ? <img src={qrcodeUrl} alt="二维码" style={{ width: 220, maxWidth: "100%" }} /> : null}
          </div>
        </div>

        <div className="setting-card setting-panel">
          <h3 className="card-title">基础设置</h3>
          <div className="card-content">
            <label htmlFor="jellyfin_enabled">启用 Jellyfin 客户端:</label>
            <select
              id="jellyfin_enabled"
              value={String(Boolean(settingData.jellyfin_enabled))}
              onChange={(e) => updateSettingField("jellyfin_enabled", e.target.value === "true")}
            >
              <option value="true">开启</option>
              <option value="false">关闭</option>
            </select>

            <label htmlFor="jellyfin_base_url">Jellyfin 地址:</label>
            <input
              id="jellyfin_base_url"
              type="text"
              value={String(settingData.jellyfin_base_url || "")}
              onChange={(e) => updateSettingField("jellyfin_base_url", e.target.value)}
            />

            <label htmlFor="jellyfin_api_key">Jellyfin API Key:</label>
            <input
              id="jellyfin_api_key"
              type="password"
              value={String(settingData.jellyfin_api_key || "")}
              onChange={(e) => updateSettingField("jellyfin_api_key", e.target.value)}
            />

            <label htmlFor="jellyfin_user_id">Jellyfin User ID(可选):</label>
            <input
              id="jellyfin_user_id"
              type="text"
              value={String(settingData.jellyfin_user_id || "")}
              onChange={(e) => updateSettingField("jellyfin_user_id", e.target.value)}
            />

            <h3 className="card-title" style={{ marginTop: 12 }}>主题</h3>
            <div className="theme-select-row">
              <select
                id="theme-mode-select"
                value={selectedThemeId}
                onChange={(e) => setTheme(e.target.value)}
              >
                {themeSelectOptions.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
              <button type="button" onClick={() => themeFileInputRef.current?.click()}>
                上传主题
              </button>
            </div>
            <input
              ref={themeFileInputRef}
              type="file"
              accept=".json,.xmtheme,application/json,text/plain"
              style={{ display: "none" }}
              onChange={(e) => {
                void onThemeCssFileSelected(e.target.files?.[0] || null);
                e.currentTarget.value = "";
              }}
            />
            {validationError ? <p className="auth-hint">{validationError}</p> : null}
          </div>
        </div>

        <div className="setting-card setting-panel">
          <div className="card-content">
            <div className="component-button-group">
              <button onClick={() => void saveSettings()}>保存配置</button>
              <button onClick={() => void loadSettingData()}>重载配置</button>
            </div>
          </div>
        </div>

        <div className="advanced-config-toggle" onClick={() => setAdvancedOpen((v) => !v)} role="button" tabIndex={0}>
          <span>⚠️</span>
          <span className="advanced-config-toggle-text">以下为高级配置，请谨慎修改</span>
          <span className="advanced-config-toggle-icon">{advancedOpen ? "▲" : "▼"}</span>
        </div>

        {advancedOpen ? (
          <div className="advanced-config-content">
            <div className="config-tabs" role="tablist" aria-label="高级配置分类">
              {ADVANCED_TABS.map((tab) => (
                <button
                  key={tab.key}
                  type="button"
                  className={`config-tab-button ${activeAdvancedTab === tab.key ? "active" : ""}`}
                  onClick={() => setActiveAdvancedTab(tab.key)}
                >
                  {tab.label}
                </button>
              ))}
            </div>
            <div className="config-tab-panels">
              {ADVANCED_TABS.map((tab) => (
                <div
                  key={`panel-${tab.key}`}
                  className={`config-tab-content setting-panel ${activeAdvancedTab === tab.key ? "active" : ""}`}
                  style={{ display: activeAdvancedTab === tab.key ? "block" : "none" }}
                >
                  <div className="card-content">
                    <div className="rows">
                      {tab.fields.map((field) => (
                        <div key={`${tab.key}-${field.key}`}>{renderSettingField(field)}</div>
                      ))}
                      {tab.key === "playlist" ? (
                        <div className="component-button-group">
                          <button onClick={() => void fetchMusicListJson()}>获取歌单</button>
                        </div>
                      ) : null}
                      {tab.key === "security" ? (
                        <div className="setting-card setting-panel" style={{ marginTop: 12 }}>
                          <h3 className="card-title">公共访问地址（高级）</h3>
                          <div className="card-content">
                            <p className="auth-hint">通常无需修改。仅当分享链接/设备无法访问时，才手动覆盖。</p>
                            <label htmlFor="public-base-url-auto">自动检测:</label>
                            <input id="public-base-url-auto" type="text" value={autoDetectedBaseUrl} readOnly />
                            <label htmlFor="public-base-url-effective">当前生效地址:</label>
                            <input id="public-base-url-effective" type="text" value={effectivePublicBaseUrl} readOnly />
                            <div className="component-button-group">
                              <button onClick={() => void resetPublicBaseUrlToAuto()}>重置为自动</button>
                            </div>
                            <div
                              className="section-header"
                              style={{ marginTop: 8 }}
                              onClick={() => setSecurityAdvancedOpen((v) => !v)}
                            >
                              <h3 className="button-section-title">手动覆盖公共访问地址</h3>
                              <span className="section-toggle-icon">{securityAdvancedOpen ? "▲" : "▼"}</span>
                            </div>
                            <div style={{ display: securityAdvancedOpen ? "block" : "none", marginTop: 8 }}>
                              <label htmlFor="public_base_url">PUBLIC_BASE_URL:</label>
                              <input
                                id="public_base_url"
                                type="text"
                                placeholder="例如: http://your-host:58090"
                                value={String(settingData.public_base_url || "")}
                                onChange={(e) => updateSettingField("public_base_url", e.target.value)}
                              />
                              <p className="auth-hint">留空表示使用自动检测地址（当前访问地址）。</p>
                              <details>
                                <summary>旧版兼容（host + port）</summary>
                                <label htmlFor="hostname">旧版 hostname:</label>
                                <input
                                  id="hostname"
                                  type="text"
                                  value={String(settingData.hostname || "")}
                                  onChange={(e) => updateSettingField("hostname", e.target.value)}
                                />
                                <label htmlFor="public_port">旧版 public_port:</label>
                                <input
                                  id="public_port"
                                  type="number"
                                  value={String(settingData.public_port ?? 58090)}
                                  onChange={(e) => updateSettingField("public_port", Number(e.target.value || 0))}
                                />
                              </details>
                            </div>
                          </div>
                        </div>
                      ) : null}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        <div className="setting-footer webui-setting-footer">
          <div className="button-section">
            <div className={`section-header ${operationOpen ? "" : "collapsed"}`} onClick={() => setOperationOpen((v) => !v)}>
              <h3 className="button-section-title">功能操作</h3>
              <span className="section-toggle-icon">{operationOpen ? "▲" : "▼"}</span>
            </div>
            <div className={`section-content ${operationOpen ? "" : "collapsed"}`} style={{ display: operationOpen ? "block" : "none" }}>
              <div className="button-grid">
                <button onClick={() => void refreshMusicTag()}>
                  <span className="material-icons">refresh</span>
                  <span>刷新tag</span>
                </button>
                <button onClick={() => clearCache()}>
                  <span className="material-icons">delete_sweep</span>
                  <span>清空缓存</span>
                </button>
                <button onClick={() => void cleanTempDir()}>
                  <span className="material-icons">folder_delete</span>
                  <span>清空临时目录</span>
                </button>
                <a href="/downloadlog" target="_blank" rel="noreferrer">
                  <button>
                    <span className="material-icons">download</span>
                    <span>下载日志文件</span>
                  </button>
                </a>
              </div>
            </div>
          </div>

          <div className="button-section">
            <div className={`section-header ${toolsOpen ? "" : "collapsed"}`} onClick={() => setToolsOpen((v) => !v)}>
              <h3 className="button-section-title">工具链接</h3>
              <span className="section-toggle-icon">{toolsOpen ? "▲" : "▼"}</span>
            </div>
            <div className={`section-content ${toolsOpen ? "" : "collapsed"}`} style={{ display: toolsOpen ? "block" : "none" }}>
              <div className="link-grid">
                <a href="/docs" target="_blank" className="link-card" rel="noreferrer">
                  <div className="link-card-icon"><span className="material-icons">description</span></div>
                  <div className="link-card-content"><div className="link-card-title">接口文档</div><div className="link-card-description">查看 API 接口文档</div></div>
                </a>
                <a href="#" className="link-card disabled" onClick={(e) => e.preventDefault()}>
                  <div className="link-card-icon"><span className="material-icons">swap_horiz</span></div>
                  <div className="link-card-content"><div className="link-card-title">m3u转换</div><div className="link-card-description">待迁移到 WebUI 工具页</div></div>
                </a>
                <a href="#" className="link-card disabled" onClick={(e) => e.preventDefault()}>
                  <div className="link-card-icon"><span className="material-icons">cloud_download</span></div>
                  <div className="link-card-content"><div className="link-card-title">歌曲下载工具</div><div className="link-card-description">待迁移到 WebUI 工具页</div></div>
                </a>
                <a href="#" className="link-card disabled" onClick={(e) => e.preventDefault()}>
                  <div className="link-card-icon"><span className="material-icons">merge_type</span></div>
                  <div className="link-card-content"><div className="link-card-title">歌单合并工具</div><div className="link-card-description">待迁移到 WebUI 工具页</div></div>
                </a>
              </div>
            </div>
          </div>
        </div>

        <div className="component-button-one">
          <button onClick={() => setShowSettings(false)} className="close-button">
            关闭
          </button>
        </div>
      </div>

      <div className="footer">Powered by XiaoMusic</div>
      <div id="sr-announcer" className="sr-only" role="status" aria-live="polite" aria-atomic="true">
        {message}
      </div>
    </>
  );
}
