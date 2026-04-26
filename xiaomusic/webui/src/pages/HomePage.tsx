import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";

import {
  addFavorite as v1AddFavorite,
  apiErrorInfo,
  getDevices as v1GetDevices,
  getLibraryMusicInfo,
  getLibraryPlaylists,
  searchOnline as v1SearchOnline,
  getSystemSettings,
  getSystemStatus,
  getPlayerState,
  getPlayerStreamUrl,
  isApiOk,
  libraryRefresh as v1LibraryRefresh,
  next as v1Next,
  play as v1Play,
  previous as v1Previous,
  saveSystemSettings,
  setPlayMode as v1SetPlayMode,
  setShutdownTimer as v1SetShutdownTimer,
  setVolume as v1SetVolume,
  stop as v1Stop,
  tts as v1Tts,
  updateSystemSettingItem,
  type ApiEnvelope,
  type PlayMode,
  type PlayerStateData,
  type PlaylistItem,
  type TransportState,
} from "../services/v1Api";
import { fetchAuthStatus, logoutAuth as logoutAuthRequest, reloadAuthRuntime } from "../services/auth";
import {
  cleanTempDir as cleanTempDirRequest,
  fetchPlaylistJson,
  fetchQrcode,
} from "../services/homeApi";

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

export type PendingSelection = {
  playlist: string | null;
  trackId: string | null;
  entityId: string | null;
  trackTitle: string | null;
  anchorPlaySessionId: string;
  anchorRevision: number;
  submitting: boolean;
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

const EMPTY_SERVER_STATE: PlayerStateData = {
  device_id: "",
  revision: -1,
  play_session_id: "",
  transport_state: "idle" as TransportState,
  track: null,
  context: null,
  position_ms: 0,
  duration_ms: 0,
  volume: 0,
  snapshot_at_ms: 0,
};

type UiState = {
  connectionStatus: "connected" | "reconnecting" | "fallback_polling";
  initializing: boolean;
  switchingHint: boolean;
};

const EMPTY_UI_STATE: UiState = {
  connectionStatus: "reconnecting",
  initializing: true,
  switchingHint: false,
};

type ServerStateAction =
  | { type: "APPLY_SNAPSHOT"; snapshot: PlayerStateData }
  | { type: "RESET" };

function serverStateReducer(_state: PlayerStateData, action: ServerStateAction): PlayerStateData {
  switch (action.type) {
    case "APPLY_SNAPSHOT":
      return action.snapshot;
    case "RESET":
      return EMPTY_SERVER_STATE;
    default:
      return _state;
  }
}

/**
 * 判断 pending 是否处于提交态
 */
export function isSubmittingPending(pending: PendingSelection | null): pending is PendingSelection {
  return Boolean(pending?.submitting);
}

/**
 * 从 serverState 中提取播放上下文名称
 */
export function getPlaybackContextName(state: PlayerStateData): string {
  return String(state.context?.name || "").trim();
}

/**
 * 判断服务端状态是否已确认了 pending 的目标
 */
export function doesServerStateConfirmPending(pending: PendingSelection, state: PlayerStateData): boolean {
  const contextName = getPlaybackContextName(state);
  const stateTrackId = String(state.track?.id || "").trim();
  const stateTrackTitle = String(state.track?.title || "").trim();
  const playlistMatched = !pending.playlist || contextName === pending.playlist;
  const trackMatched = pending.trackId
    ? stateTrackId === pending.trackId
    : !pending.trackTitle || stateTrackTitle === pending.trackTitle;
  return playlistMatched && trackMatched;
}

/**
 * 判断是否应该清理 pendingSelection
 * 核心规则：
 * - 浏览态(submitting=false)默认不清理
 * - 提交态(submitting=true)在确认成功或确认性停止时清理
 */
export function shouldClearPendingSelection(
  pending: PendingSelection,
  prevState: PlayerStateData,
  nextState: PlayerStateData,
): boolean {
  void prevState;

  // 浏览态不因状态推进而清理
  if (!isSubmittingPending(pending)) {
    return false;
  }

  const sessionChanged =
    String(nextState.play_session_id || "") !== String(pending.anchorPlaySessionId || "");
  const revisionAdvanced = Number(nextState.revision || 0) > Number(pending.anchorRevision || 0);
  const transportStopped = nextState.transport_state === "stopped" || nextState.transport_state === "idle";

  // session变化且目标命中
  if (sessionChanged && doesServerStateConfirmPending(pending, nextState)) {
    return true;
  }
  // revision前进且目标命中
  if (revisionAdvanced && doesServerStateConfirmPending(pending, nextState)) {
    return true;
  }
  // revision前进且进入停止态（用于stop或失败后回稳路径）
  if (revisionAdvanced && transportStopped) {
    return true;
  }

  return false;
}

export function buildPendingSelectionForPlayback(
  prev: PendingSelection | null,
  playlistName: string,
  picked: string,
  trackId: string | null | undefined,
  state: PlayerStateData,
  entityId?: string | null,
): PendingSelection {
  return {
    playlist: playlistName || prev?.playlist || null,
    trackId: trackId ?? prev?.trackId ?? null,
    entityId: entityId ?? prev?.entityId ?? null,
    trackTitle: picked || prev?.trackTitle || null,
    anchorPlaySessionId: String(state.play_session_id || ""),
    anchorRevision: Number(state.revision || 0),
    submitting: false,
  };
}

export function markPendingSubmittingState(
  prev: PendingSelection | null,
  state: PlayerStateData,
): PendingSelection | null {
  if (!prev) {
    return prev;
  }
  return {
    ...prev,
    submitting: true,
    anchorPlaySessionId: String(state.play_session_id || ""),
    anchorRevision: Number(state.revision || 0),
  };
}

export function shouldGuardPendingNativeSwitch(pending: PendingSelection | null, songsLength: number): boolean {
  return Boolean(pending) && songsLength === 0;
}

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

function clearXmCache(): void {
  const keysToRemove: string[] = [];
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i);
    if (key && (key.startsWith("xm_") || key.startsWith("xm_last_playing_song_"))) {
      keysToRemove.push(key);
    }
  }
  keysToRemove.forEach((k) => {
    try {
      localStorage.removeItem(k);
    } catch {
      // ignore
    }
  });
}

type ProgressBaselineCache = {
  play_session_id: string;
  revision: number;
  position_ms: number;
  captured_at_ms: number;
};

function progressBaselineStorageKey(did: string): string {
  return `xm_progress_baseline_${did}`;
}

function loadProgressBaseline(did: string): ProgressBaselineCache | null {
  if (!did) {
    return null;
  }
  try {
    const raw = localStorage.getItem(progressBaselineStorageKey(did));
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw) as ProgressBaselineCache;
    if (
      !parsed ||
      typeof parsed.play_session_id !== "string" ||
      typeof parsed.revision !== "number" ||
      typeof parsed.position_ms !== "number" ||
      typeof parsed.captured_at_ms !== "number"
    ) {
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

function saveProgressBaseline(did: string, baseline: ProgressBaselineCache): void {
  if (!did) {
    return;
  }
  try {
    localStorage.setItem(progressBaselineStorageKey(did), JSON.stringify(baseline));
  } catch {
    return;
  }
}

function clearProgressBaseline(did: string): void {
  if (!did) {
    return;
  }
  removeLocal(progressBaselineStorageKey(did));
}

function useProgressInterpolation(
  serverState: PlayerStateData,
  transportState: TransportState,
  suspendUpdates = false,
): {
  currentPositionMs: number;
  progress: number;
} {
  const [currentPositionMs, setCurrentPositionMs] = useState<number>(() => serverState.position_ms);
  const timerRef = useRef<number | null>(null);
  const lastRevisionRef = useRef<number>(-1);
  const lastSessionRef = useRef<string>('');
  const lastSnapshotAtRef = useRef<number>(0);
  const lastPositionRef = useRef<number>(0);

  const clampPosition = (positionMs: number, durationMs: number): number => {
    if (durationMs > 0) {
      return Math.min(positionMs, durationMs);
    }
    return positionMs;
  };

  const resolveInterpolatedPosition = (basePosition: number, snapshotAtMs: number, durationMs: number): number => {
    const effectiveSnapshotAt = snapshotAtMs > 0 ? snapshotAtMs : Date.now();
    const elapsed = Math.max(0, Date.now() - effectiveSnapshotAt);
    return clampPosition(basePosition + elapsed, durationMs);
  };

  useEffect(() => {
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }

    const sessionChanged = serverState.play_session_id !== lastSessionRef.current;
    const revisionChanged = serverState.revision !== lastRevisionRef.current;
    const snapshotChanged = serverState.snapshot_at_ms !== lastSnapshotAtRef.current;
    const positionChanged = serverState.position_ms !== lastPositionRef.current;

    lastSessionRef.current = serverState.play_session_id;
    lastRevisionRef.current = serverState.revision;
    lastSnapshotAtRef.current = serverState.snapshot_at_ms;
    lastPositionRef.current = serverState.position_ms;

    if (transportState !== "playing" || suspendUpdates) {
      if (transportState !== "playing") {
        clearProgressBaseline(serverState.device_id);
      }
      setCurrentPositionMs(serverState.position_ms);
      return;
    }

    const durationMs = serverState.duration_ms;
    const serverDisplayedPosition = resolveInterpolatedPosition(
      serverState.position_ms,
      serverState.snapshot_at_ms,
      durationMs,
    );

    let effectiveBasePosition = serverState.position_ms;
    let effectiveSnapshotAtMs = serverState.snapshot_at_ms;

    const cachedBaseline = loadProgressBaseline(serverState.device_id);
    if (
      cachedBaseline &&
      cachedBaseline.play_session_id === serverState.play_session_id &&
      cachedBaseline.revision === serverState.revision
    ) {
      const cachedDisplayedPosition = clampPosition(
        cachedBaseline.position_ms + Math.max(0, Date.now() - cachedBaseline.captured_at_ms),
        durationMs,
      );
      if (cachedDisplayedPosition > serverDisplayedPosition + 750) {
        effectiveBasePosition = cachedDisplayedPosition;
        effectiveSnapshotAtMs = Date.now();
      }
    }

    if (sessionChanged || revisionChanged || snapshotChanged || positionChanged) {
      setCurrentPositionMs(resolveInterpolatedPosition(effectiveBasePosition, effectiveSnapshotAtMs, durationMs));
    }

    timerRef.current = window.setInterval(() => {
      const nextPosition = resolveInterpolatedPosition(effectiveBasePosition, effectiveSnapshotAtMs, durationMs);
      setCurrentPositionMs(nextPosition);
      saveProgressBaseline(serverState.device_id, {
        play_session_id: serverState.play_session_id,
        revision: serverState.revision,
        position_ms: nextPosition,
        captured_at_ms: Date.now(),
      });
    }, 250);

    return () => {
      if (timerRef.current !== null) {
        window.clearInterval(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [
    transportState,
    serverState.device_id,
    serverState.position_ms,
    serverState.snapshot_at_ms,
    serverState.play_session_id,
    serverState.revision,
    serverState.duration_ms,
    suspendUpdates,
  ]);

  const progress = useMemo(() => {
    if (serverState.duration_ms <= 0) {
      return 0;
    }
    return Math.max(0, Math.min(100, (currentPositionMs / serverState.duration_ms) * 100));
  }, [currentPositionMs, serverState.duration_ms]);

  return { currentPositionMs, progress };
}

function usePlayerStream(
  deviceId: string,
  applySnapshot: (snapshot: PlayerStateData, currentDeviceId: string) => boolean,
  onConnectionStatusChange: (status: UiState["connectionStatus"]) => void,
  lastAppliedRevisionRef: React.MutableRefObject<number>,
) {
  const eventSourceRef = useRef<EventSource | null>(null);
  const reconnectTimerRef = useRef<number | null>(null);
  const pollingTimerRef = useRef<number | null>(null);
  const currentDeviceIdRef = useRef<string>("");
  const isPollingRef = useRef<boolean>(false);
  const mountedRef = useRef<boolean>(true);

  function disconnectStream(): void {
    if (reconnectTimerRef.current !== null) {
      window.clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    if (eventSourceRef.current !== null) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
  }

  function stopPolling(): void {
    if (pollingTimerRef.current !== null) {
      window.clearInterval(pollingTimerRef.current);
      pollingTimerRef.current = null;
    }
    isPollingRef.current = false;
  }

  function startFallbackPolling(): void {
    if (!mountedRef.current || !currentDeviceIdRef.current) {
      return;
    }
    stopPolling();
    onConnectionStatusChange("fallback_polling");

    pollingTimerRef.current = window.setInterval(async () => {
      if (!mountedRef.current) {
        stopPolling();
        return;
      }
      const did = currentDeviceIdRef.current;
      if (!did) {
        stopPolling();
        return;
      }
      if (isPollingRef.current) {
        return;
      }

      isPollingRef.current = true;
      try {
        const out = await getPlayerState(did);
        if (!mountedRef.current || did !== currentDeviceIdRef.current) {
          isPollingRef.current = false;
          return;
        }
        if (isApiOk(out)) {
          const snapshot = out.data || EMPTY_SERVER_STATE;
          const applied = applySnapshot(snapshot, did);
          if (applied && eventSourceRef.current !== null) {
            stopPolling();
            onConnectionStatusChange("connected");
          }
        }
      } catch {
        // ignore polling errors
      } finally {
        isPollingRef.current = false;
      }
    }, 3000);
  }

  function scheduleReconnect(): void {
    if (!mountedRef.current || !currentDeviceIdRef.current) {
      return;
    }
    disconnectStream();
    onConnectionStatusChange("reconnecting");

    reconnectTimerRef.current = window.setTimeout(() => {
      if (!mountedRef.current) {
        return;
      }
      const did = currentDeviceIdRef.current;
      if (!did) {
        return;
      }
      startFallbackPolling();
      connectStream(did);
    }, 3000);
  }

  function connectStream(did: string): void {
    disconnectStream();
    if (!mountedRef.current) {
      return;
    }

    const url = getPlayerStreamUrl(did);
    const es = new EventSource(url);
    eventSourceRef.current = es;

    es.onopen = () => {
      if (!mountedRef.current) {
        es.close();
        return;
      }
      stopPolling();
      onConnectionStatusChange("connected");
    };

    es.onerror = () => {
      if (!mountedRef.current) {
        return;
      }
      scheduleReconnect();
    };

    es.addEventListener("player_state", (event: MessageEvent) => {
      if (!mountedRef.current) {
        return;
      }
      const did = currentDeviceIdRef.current;
      try {
        const snapshot = JSON.parse(event.data) as PlayerStateData;
        applySnapshot(snapshot, did);
      } catch {
        // ignore parse errors
      }
    });

    es.addEventListener("stream_error", (event: MessageEvent) => {
      if (!mountedRef.current) {
        return;
      }
      try {
        const errorData = JSON.parse(event.data) as { error_code?: string };
        if (errorData.error_code === "E_AUTH_EXPIRED") {
          // handle auth refresh if needed
        }
      } catch {
        // ignore parse errors
      }
    });
  }

  useEffect(() => {
    mountedRef.current = true;
    currentDeviceIdRef.current = deviceId;
    lastAppliedRevisionRef.current = -1;

    if (!deviceId) {
      disconnectStream();
      stopPolling();
      onConnectionStatusChange("reconnecting");
      return;
    }

    (async () => {
      const out = await getPlayerState(deviceId);
      if (!mountedRef.current || deviceId !== currentDeviceIdRef.current) {
        return;
      }
      if (isApiOk(out)) {
        applySnapshot(out.data || EMPTY_SERVER_STATE, deviceId);
      }
    })();

    startFallbackPolling();
    connectStream(deviceId);

    return () => {
      mountedRef.current = false;
      disconnectStream();
      stopPolling();
    };
  }, [deviceId]);

  useEffect(() => {
    return () => {
      mountedRef.current = false;
      disconnectStream();
      stopPolling();
    };
  }, []);
}

export function HomePage() {
  const [devices, setDevices] = useState<Device[]>([]);
  const [activeDid, setActiveDid] = useState<string>(() => loadLocal("xm_ui_active_did"));
  const [playlists, setPlaylists] = useState<Record<string, PlaylistItem[]>>({});
  const [pendingSelection, setPendingSelection] = useState<PendingSelection | null>(null);
  const [volume, setVolume] = useState<number>(50);
  const [message, setMessage] = useState<string>("");
  const [version, setVersion] = useState<string>("");
  const [playModeIndex, setPlayModeIndex] = useState<number>(2);
  const [switchingPlayMode, setSwitchingPlayMode] = useState<boolean>(false);

  const [showSearch, setShowSearch] = useState<boolean>(false);
  const [selectorInteracting, setSelectorInteracting] = useState<boolean>(false);
  const selectorInteractionTimerRef = useRef<number | null>(null);
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

  const [serverState, dispatchServerState] = useReducer(serverStateReducer, EMPTY_SERVER_STATE);
  const [uiState, setUiState] = useState<UiState>(EMPTY_UI_STATE);
  const serverStateRef = useRef<PlayerStateData>(EMPTY_SERVER_STATE);
  const previousPendingCheckStateRef = useRef<PlayerStateData>(EMPTY_SERVER_STATE);
  const prevPlaySessionRef = useRef<string>("");
  const lastStableTrackTitleRef = useRef<string>(loadRememberedPlayingSong(activeDid));
  const activeDidRef = useRef<string>(activeDid);
  const publicBaseMigratedRef = useRef<boolean>(false);
  const themeFileInputRef = useRef<HTMLInputElement | null>(null);
  const lastAppliedRevisionRef = useRef<number>(-1);
  const lastAppliedSnapshotAtRef = useRef<number>(-1);
  const lastAppliedPositionRef = useRef<number>(-1);

  const { currentPositionMs, progress } = useProgressInterpolation(
    serverState,
    serverState.transport_state,
    selectorInteracting,
  );

  const beginSelectorInteraction = useCallback(() => {
    if (selectorInteractionTimerRef.current !== null) {
      window.clearTimeout(selectorInteractionTimerRef.current);
      selectorInteractionTimerRef.current = null;
    }
    setSelectorInteracting(true);
  }, []);

  const endSelectorInteraction = useCallback((delayMs = 0) => {
    if (selectorInteractionTimerRef.current !== null) {
      window.clearTimeout(selectorInteractionTimerRef.current);
      selectorInteractionTimerRef.current = null;
    }
    if (delayMs > 0) {
      selectorInteractionTimerRef.current = window.setTimeout(() => {
        selectorInteractionTimerRef.current = null;
        setSelectorInteracting(false);
      }, delayMs);
      return;
    }
    setSelectorInteracting(false);
  }, []);

  useEffect(() => {
    return () => {
      if (selectorInteractionTimerRef.current !== null) {
        window.clearTimeout(selectorInteractionTimerRef.current);
      }
    };
  }, []);

  const applySnapshotFn = useMemo(
    () =>
      (snapshot: PlayerStateData, currentDeviceId: string): boolean => {
        if (snapshot.device_id !== currentDeviceId) {
          return false;
        }
        const isOlderRevision = snapshot.revision < lastAppliedRevisionRef.current;
        const sameRevision = snapshot.revision === lastAppliedRevisionRef.current;
        const hasNewTimingBaseline =
          snapshot.snapshot_at_ms > lastAppliedSnapshotAtRef.current ||
          snapshot.position_ms !== lastAppliedPositionRef.current;

        if (isOlderRevision) {
          if (lastAppliedRevisionRef.current > 0 && snapshot.revision < lastAppliedRevisionRef.current * 0.1) {
            lastAppliedRevisionRef.current = -1;
            lastAppliedSnapshotAtRef.current = -1;
            lastAppliedPositionRef.current = -1;
          } else {
            return false;
          }
        } else if (sameRevision && !hasNewTimingBaseline) {
          return false;
        }
        lastAppliedRevisionRef.current = snapshot.revision;
        lastAppliedSnapshotAtRef.current = snapshot.snapshot_at_ms;
        lastAppliedPositionRef.current = snapshot.position_ms;

        const prevState = serverStateRef.current;
        if (snapshot.play_session_id !== prevState.play_session_id) {
          prevPlaySessionRef.current = prevState.play_session_id;
          setUiState((prev) => ({ ...prev, switchingHint: true }));
        }

        if (snapshot.transport_state === "playing") {
          if (snapshot.track?.title) {
            saveRememberedPlayingSong(snapshot.device_id, snapshot.track.title);
            lastStableTrackTitleRef.current = snapshot.track.title;
          }
          setUiState((prev) => ({ ...prev, switchingHint: false }));
        }

        serverStateRef.current = snapshot;
        dispatchServerState({ type: "APPLY_SNAPSHOT", snapshot });
        setUiState((prev) => ({ ...prev, initializing: false }));
        return true;
      },
    [],
  );

  const handleConnectionStatusChange = (status: UiState["connectionStatus"]) => {
    setUiState((prev) => ({ ...prev, connectionStatus: status }));
  };

  usePlayerStream(activeDid, applySnapshotFn, handleConnectionStatusChange, lastAppliedRevisionRef);

  useEffect(() => {
    if (activeDid) {
      saveLocal("xm_ui_active_did", activeDid);
    }
  }, [activeDid]);

  useEffect(() => {
    activeDidRef.current = activeDid;
    lastAppliedRevisionRef.current = -1;
    lastAppliedSnapshotAtRef.current = -1;
    lastAppliedPositionRef.current = -1;
    previousPendingCheckStateRef.current = EMPTY_SERVER_STATE;
  }, [activeDid]);

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

  const playlistNames = useMemo(() => Object.keys(playlists), [playlists]);
  const fallbackPlaylist = playlistNames[0] || "";
  const playbackContextName = useMemo(
    () => String(serverState.context?.name || "").trim(),
    [serverState.context?.name],
  );
  const playbackPlaylist = playbackContextName;
  const playbackSongs = useMemo(() => playlists[playbackPlaylist] || [], [playlists, playbackPlaylist]);
  const playbackTrackId = serverState.track?.id || null;
  const playbackTrackTitle = serverState.track?.title || "";
  const playbackTrackItem = useMemo(
    () => playbackSongs.find((item) => item.id === playbackTrackId) || null,
    [playbackSongs, playbackTrackId],
  );

  const effectivePlaylist = pendingSelection?.playlist || playbackPlaylist || fallbackPlaylist;
  const playlistOptions = useMemo(() => {
    const names = Object.keys(playlists);
    if (!effectivePlaylist || names.includes(effectivePlaylist)) {
      return names;
    }
    return [effectivePlaylist, ...names];
  }, [effectivePlaylist, playlists]);
  const songs = useMemo(() => playlists[effectivePlaylist] || [], [playlists, effectivePlaylist]);
  const effectiveTrackId = useMemo(() => {
    if (pendingSelection?.trackId && songs.find((item) => item.id === pendingSelection.trackId)) {
      return pendingSelection.trackId;
    }
    if (!pendingSelection && effectivePlaylist === playbackPlaylist && playbackTrackId && songs.find((item) => item.id === playbackTrackId)) {
      return playbackTrackId;
    }
    return songs[0]?.id ?? null;
  }, [pendingSelection, songs, effectivePlaylist, playbackPlaylist, playbackTrackId]);
  const effectiveTrackItem = useMemo(
    () => songs.find((item) => item.id === effectiveTrackId) || null,
    [songs, effectiveTrackId],
  );
  const effectiveTrackTitle =
    pendingSelection?.trackTitle ||
    effectiveTrackItem?.title ||
    (!pendingSelection && effectivePlaylist === playbackPlaylist ? playbackTrackItem?.title || playbackTrackTitle : "") ||
    songs[0]?.title ||
    "";

  const clearPendingSelection = useCallback(() => {
    setPendingSelection(null);
  }, []);

  const applyPendingPlaylist = useCallback(
    (nextPlaylist: string) => {
      const nextSongs = playlists[nextPlaylist] || [];
      setPendingSelection({
        playlist: nextPlaylist,
        trackId: nextSongs[0]?.id ?? null,
        entityId: nextSongs[0]?.entity_id ?? null,
        trackTitle: nextSongs[0]?.title ?? null,
        anchorPlaySessionId: String(serverState.play_session_id || ""),
        anchorRevision: Number(serverState.revision || 0),
        submitting: false,
      });
    },
    [playlists, serverState.play_session_id, serverState.revision],
  );

  const applyPendingTrack = useCallback(
    (trackId: string | null, trackTitle?: string | null, playlistName?: string) => {
      const nextPlaylist = playlistName ?? effectivePlaylist;
      const nextSongs = playlists[nextPlaylist] || [];
      const item = nextSongs.find((entry) => entry.id === trackId) || null;
      setPendingSelection({
        playlist: nextPlaylist || null,
        trackId: item?.id ?? trackId ?? null,
        entityId: item?.entity_id ?? null,
        trackTitle: item?.title ?? trackTitle ?? null,
        anchorPlaySessionId: String(serverState.play_session_id || ""),
        anchorRevision: Number(serverState.revision || 0),
        submitting: false,
      });
    },
    [playlists, effectivePlaylist, serverState.play_session_id, serverState.revision],
  );

  const markPendingSubmitting = useCallback(() => {
    setPendingSelection((prev) => markPendingSubmittingState(prev, serverStateRef.current));
  }, []);

  const songTitles = useMemo(() => songs.map((s) => s.title), [songs]);
  const filteredSongs = useMemo(() => {
    const key = soundscapeFilter.trim().toLowerCase();
    if (!key) {
      return songs;
    }
    return songs.filter((item) => item.title.toLowerCase().includes(key));
  }, [songs, soundscapeFilter]);
  const authLoggedIn = Boolean(authStatus.token_valid);
  const authReady = Boolean(authStatus.runtime_auth_ready);
  const authInProgress = Boolean(authStatus.login_in_progress);
  const authStatusLabel = authReady ? "已登录" : authLoggedIn ? "登录待恢复" : "未登录";
  const authStatusClass = authReady ? "ok" : "warn";
  const { selectedThemeId, activeLayout, customThemes, setTheme, uploadThemePackage, validationError } =
    useTheme();

  const isSoundscapeLayout = activeLayout === "soundscape";

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

  const playbackText = useMemo(() => {
    const title = serverState.track?.title || "";
    const rememberedTitle = uiState.initializing ? loadRememberedPlayingSong(activeDid) : "";
    const visibleTitle = title || rememberedTitle;
    const bufferedTitle = lastStableTrackTitleRef.current || rememberedTitle;
    switch (serverState.transport_state) {
      case "playing":
        return visibleTitle ? `正在播放：${visibleTitle}` : "正在播放";
      case "paused":
        return visibleTitle ? `已暂停：${visibleTitle}` : "已暂停";
      case "starting":
        return visibleTitle ? `正在加载：${visibleTitle}` : "正在加载...";
      case "switching":
        return bufferedTitle ? `正在切换：${bufferedTitle}` : "正在切换...";
      case "stopped":
        return "空闲";
      case "error":
        return bufferedTitle ? `播放出错：${bufferedTitle}` : "播放出错";
      case "idle":
      default:
        return uiState.initializing && rememberedTitle ? `正在恢复：${rememberedTitle}` : "空闲";
    }
  }, [activeDid, serverState.transport_state, serverState.track?.title, uiState.initializing]);

  const safeOffsetSec = Math.max(0, currentPositionMs / 1000);
  const safeDurationSec = Math.max(0, serverState.duration_ms / 1000);

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
    const out = await getSystemSettings();
    if (!isApiOk(out)) {
      setMessage("获取设置失败");
      return;
    }
    const settings = { ...(out.data.settings || {}) };
    const dids = Array.isArray(out.data.device_ids)
      ? out.data.device_ids.map((x) => String(x || "").trim()).filter(Boolean)
      : [];

    const manual = normalizeBaseUrlInput(settings.public_base_url);
    const legacy = legacyBaseUrl(settings.hostname, settings.public_port);
    const auto = autoDetectedBaseUrl;
    let hydrated = { ...settings };
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
    const rows = Array.isArray(out.data.devices)
      ? out.data.devices.map((d) => ({ miotDID: d.device_id, name: d.name || d.model || d.device_id }))
      : [];
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
    setPullAskEnabled(Boolean(settings.enable_pull_ask));

    if (!publicBaseMigratedRef.current && !manual) {
      const shouldUseLegacy = Boolean(legacy) && !legacyLooksUnconfigured(settings.hostname, settings.public_port);
      const target = shouldUseLegacy ? legacy : auto;
      if (target) {
        publicBaseMigratedRef.current = true;
        const payload = {
          ...withPublicBaseCompat({ ...settings }, target),
          mi_did: dids.join(",") || String(settings.mi_did || ""),
        };
        await saveSystemSettings(payload, dids);
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
      clearPendingSelection();
      setMessage("获取歌单失败");
      return;
    }
    const playlistsData = out.data.playlists || {};
    setPlaylists(playlistsData);
  }

  useEffect(() => {
    void (async () => {
      await Promise.allSettled([loadVersion(), loadAuthStatus(), loadSettingData(), loadPlaylists()]);
      void loadDevices();
    })();
  }, []);

  useEffect(() => {
    if (!activeDid) {
      dispatchServerState({ type: "RESET" });
      setUiState(EMPTY_UI_STATE);
      serverStateRef.current = EMPTY_SERVER_STATE;
      prevPlaySessionRef.current = "";
      lastStableTrackTitleRef.current = "";
      clearPendingSelection();
      return;
    }

    clearPendingSelection();
    setVolume(loadRememberedVolume(activeDid));
    lastStableTrackTitleRef.current = loadRememberedPlayingSong(activeDid);
    setUiState((prev) => ({ ...prev, initializing: true }));
  }, [activeDid]);

  useEffect(() => {
    if (!activeDid) {
      return;
    }
    const serverVolume = Number(serverState.volume ?? -1);
    if (!Number.isFinite(serverVolume) || serverVolume < 0 || serverVolume > 100) {
      return;
    }
    setVolume(serverVolume);
    saveRememberedVolume(activeDid, serverVolume);
  }, [activeDid, serverState.volume]);

  useEffect(() => {
    if (pendingSelection?.playlist && !playlists[pendingSelection.playlist]) {
      clearPendingSelection();
    }
  }, [pendingSelection, playlists, clearPendingSelection]);

  useEffect(() => {
    const prevState = previousPendingCheckStateRef.current;
    previousPendingCheckStateRef.current = serverState;
    if (!pendingSelection) {
      return;
    }
    if (shouldClearPendingSelection(pendingSelection, prevState, serverState)) {
      clearPendingSelection();
    }
  }, [pendingSelection, serverState, clearPendingSelection]);

  useEffect(() => {
    const devices = (settingData.devices || {}) as Record<string, { play_type?: number }>;
    const mode = devices?.[activeDid]?.play_type;
    if (typeof mode === "number" && mode >= 0 && mode < PLAY_MODES.length) {
      setPlayModeIndex(mode);
    }
  }, [activeDid, settingData]);

  function requireDid(): boolean {
    if (activeDid) {
      return true;
    }
    setMessage("当前无可用设备，无法执行设备控制。");
    return false;
  }

  function applyPlayStateFromResponse(deviceId: string, state: PlayerStateData | undefined): void {
    if (!state) {
      return;
    }
    void applySnapshotFn(state, deviceId);
  }

  async function playPlaylistTrack(
    deviceId: string,
    playlistName: string,
    picked: string,
    trackId?: string | null,
    entityId?: string | null,
  ): Promise<boolean> {
    // 确保 pending 存在，即使之前没有浏览过
    setPendingSelection((prev) =>
      buildPendingSelectionForPlayback(prev, playlistName, picked, trackId, serverStateRef.current, entityId),
    );

    const contextHint = {
      context_type: "playlist",
      context_name: playlistName,
      context_id: playlistName,
    };
    const playlistItem = songs.find((item) => item.id === trackId) || null;
    const resolvedEntityId = String(entityId || playlistItem?.entity_id || pendingSelection?.entityId || "").trim() || undefined;
    const localLibraryOptions = {
      title: picked,
      context_hint: contextHint,
      source_payload: {
        source: "local_library",
        playlist_name: playlistName,
        music_name: picked,
        track_name: picked,
        track_id: trackId ?? undefined,
        entity_id: resolvedEntityId,
        context_type: "playlist",
        context_name: playlistName,
        context_id: playlistName,
      },
    };

    const playResp = await v1Play({
      device_id: deviceId,
      query: picked,
      source_hint: "local_library",
      options: localLibraryOptions,
    });

    if (isApiOk(playResp)) {
      markPendingSubmitting();
      applyPlayStateFromResponse(deviceId, playResp.data?.state);
      setMessage(`已发送播放《${picked}》`);
      return true;
    }

    const info = await getLibraryMusicInfo(picked);
    const streamUrl = String(info?.data?.url || "").trim();
    const durationSeconds = Number(info?.data?.duration_seconds || 0);
    if (streamUrl) {
      const fallbackResp = await v1Play({
        device_id: deviceId,
        query: streamUrl,
        source_hint: "jellyfin",
        options: {
          title: picked,
          context_hint: contextHint,
          source_payload: {
            source: "jellyfin",
            playlist_name: playlistName,
            music_name: picked,
            track_name: picked,
            track_id: trackId ?? undefined,
            entity_id: resolvedEntityId,
            context_type: "playlist",
            context_name: playlistName,
            context_id: playlistName,
            url: streamUrl,
            duration_seconds: Number.isFinite(durationSeconds) && durationSeconds > 0 ? durationSeconds : undefined,
          },
        },
      });
      if (isApiOk(fallbackResp)) {
        markPendingSubmitting();
        applyPlayStateFromResponse(deviceId, fallbackResp.data?.state);
        setMessage(`已发送播放《${picked}》`);
        return true;
      }
    }

    const err = apiErrorInfo(playResp);
    setMessage(`播放失败：${explainPlaybackError(err.errorCode, err.message, err.stage)}`);
    return false;
  }

  async function switchTrack(action: "previous" | "next", okText: string) {
    if (!requireDid()) {
      return;
    }
    const did = activeDid;
    setMessage(`${okText}，正在同步播放信息...`);
    try {
      if (pendingSelection && songs.length > 0) {
        const currentIndex = Math.max(0, songs.findIndex((item) => item.id === effectiveTrackId));
        const delta = action === "previous" ? -1 : 1;
        const nextIndex = (currentIndex + delta + songs.length) % songs.length;
        const nextItem = songs[nextIndex];
        if (!nextItem) {
          setMessage("当前歌单为空，请先刷新歌单或切换列表");
          return;
        }
        applyPendingTrack(nextItem.id, nextItem.title, effectivePlaylist);
        await playPlaylistTrack(did, effectivePlaylist, nextItem.title, nextItem.id, nextItem.entity_id);
        return;
      }

      if (shouldGuardPendingNativeSwitch(pendingSelection, songs.length)) {
        setMessage("当前列表尚未加载完成，请稍候");
        return;
      }

      const out = action === "previous" ? await v1Previous(did) : await v1Next(did);
      if (isApiOk(out)) {
        setMessage(okText);
        return;
      }
      const err = apiErrorInfo(out);
      setMessage(err.message || "执行失败");
    } catch {
      setMessage("执行失败");
    }
  }


  async function playSongByName(songName: string) {
    if (!requireDid()) {
      return;
    }
    const deviceId = activeDid;
    const currentItem = songs.find((s) => s.id === effectiveTrackId) || null;
    const targetPlaylist = effectivePlaylist;
    const picked = String(songName || currentItem?.title || effectiveTrackTitle || "").trim();
    const targetTrackId = currentItem?.title === picked ? currentItem.id : effectiveTrackId;
    const targetEntityId = currentItem?.title === picked ? currentItem.entity_id : pendingSelection?.entityId;
    if (!picked || !targetPlaylist) {
      setMessage("当前歌单为空，请先刷新歌单或切换列表");
      return;
    }
    setMessage(`正在切换到 ${picked}...`);

    try {
      await playPlaylistTrack(deviceId, targetPlaylist, picked, targetTrackId, targetEntityId);
    } catch (err) {
      const reason = err instanceof Error ? err.message : String(err || "未知错误");
      setMessage(`播放失败：${reason}`);
    }
  }

  async function playCurrent() {
    const currentItem = songs.find((s) => s.id === effectiveTrackId) || null;
    const songToPlay = currentItem?.title ?? effectiveTrackTitle;
    await playSongByName(songToPlay);
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
        void loadSettingData();
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
        clearPendingSelection();
        applyPlayStateFromResponse(activeDid, out.data?.state);
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
    const musicName = String(serverState.track?.title || effectiveTrackTitle || "").trim();
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
    const out = await v1SearchOnline(kw);
    if (!isApiOk(out)) {
      const err = apiErrorInfo(out);
      setMessage(err.message || "搜索失败");
      setSearchResults([]);
      return;
    }
    setSearchResults((out.data.items || []) as OnlineSearchItem[]);
    setSelectedSearchIndex(-1);
    setMessage(`搜索到 ${out.data.items?.length || 0} 条结果`);
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
      setQrcodeRemain(0);
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
    const out = await saveSystemSettings(payload, selectedSettingDids);
    if (!isApiOk(out)) {
      const err = apiErrorInfo(out);
      setMessage(err.message || "设置保存失败");
      return;
    }
    setMessage(`已恢复自动地址：${autoDetectedBaseUrl}`);
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
    const out = await saveSystemSettings(payload, selectedSettingDids);
    if (!isApiOk(out)) {
      const err = apiErrorInfo(out);
      setMessage(err.message || "配置保存失败");
      return;
    }
    setMessage("配置已保存");
    await loadSettingData();
    await loadDevices();
    await loadPlaylists();
  }

  async function togglePullAsk() {
    const next = !pullAskEnabled;
    const out = await updateSystemSettingItem("enable_pull_ask", next);
    if (isApiOk(out)) {
      setPullAskEnabled(next);
      updateSettingField("enable_pull_ask", next);
      setMessage(next ? "语音口令已开启" : "语音口令已关闭");
      return;
    }
    const err = apiErrorInfo(out);
    setMessage(err.message || "切换失败，请重试");
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

  function doClearCache() {
    clearXmCache();
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
    applyPendingPlaylist(nextPlaylist);
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
                  className={`soundscape-playlist-item ${effectivePlaylist === name ? "active" : ""}`}
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
              <span className="soundscape-meta-chip">当前列表：{effectivePlaylist || "-"}</span>
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
                  {filteredSongs.map((item, idx) => (
                    <tr
                      key={`song-${item.id}-${idx}`}
                      className={effectiveTrackId === item.id ? "active" : ""}
                      onClick={() => {
                        applyPendingTrack(item.id, item.title, effectivePlaylist);
                      }}
                      onDoubleClick={() => {
                        applyPendingTrack(item.id, item.title, effectivePlaylist);
                        void playSongByName(item.title);
                      }}
                    >
                      <td>{idx + 1}</td>
                      <td>{item.title}</td>
                      <td>
                        <button onClick={() => {
                          applyPendingTrack(item.id, item.title, effectivePlaylist);
                          void playSongByName(item.title);
                        }}>播放</button>
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
              {formatTime(safeOffsetSec)} / {formatTime(safeDurationSec)}
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
          <select
            id="music_list"
            className="playlist-selector"
            value={effectivePlaylist}
            onMouseDown={beginSelectorInteraction}
            onTouchStart={beginSelectorInteraction}
            onFocus={beginSelectorInteraction}
            onBlur={() => endSelectorInteraction()}
            onChange={(e) => {
              beginSelectorInteraction();
              applyPendingPlaylist(e.target.value);
              endSelectorInteraction(800);
            }}
          >
            {playlistOptions.map((name) => (
              <option key={name} value={name}>
                {`${name} (${name === effectivePlaylist ? songs.length : (playlists[name] || []).length})`}
              </option>
            ))}
          </select>

          <label htmlFor="music_name" className="label-with-action">
            选择歌曲:
          </label>
          <select
            id="music_name"
            className="song-selector"
            value={effectiveTrackId ?? ""}
            onMouseDown={beginSelectorInteraction}
            onTouchStart={beginSelectorInteraction}
            onFocus={beginSelectorInteraction}
            onBlur={() => endSelectorInteraction()}
            onChange={(e) => {
              beginSelectorInteraction();
              const selectedId = e.target.value;
              const item = songs.find(s => s.id === selectedId);
              if (item) {
                applyPendingTrack(selectedId, item.title, effectivePlaylist);
              }
              endSelectorInteraction(800);
            }}
          >
            {songs.map((item, idx) => (
              <option key={`${effectivePlaylist}:${item.id}:${idx}`} value={item.id}>
                {item.title}
              </option>
            ))}
          </select>

          <div id="device-audio" className="audio-section">
            <progress className="progress" id="progress" value={progress} max={100}></progress>
            <div className="time-info">
              <span className="current-time" id="current-time">
                {formatTime(safeOffsetSec)}
              </span>
              <div className="current-song" id="playering-music">
                {playbackText}
              </div>
              <span className="duration" id="duration">
                {formatTime(safeDurationSec)}
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
                      setMessage("已停止");
                    } else {
                      const err = apiErrorInfo(out);
                      setMessage(`停止失败：${explainPlaybackError(err.errorCode, err.message, err.stage)}`);
                    }
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
                <button onClick={() => doClearCache()}>
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
