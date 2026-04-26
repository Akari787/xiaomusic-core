import { apiGetJson, apiPostJson } from "./apiClient";

// Public API only: this module only wraps `/api/v1/*` interfaces.

export type SourceHint = "auto" | "direct_url" | "site_media" | "jellyfin" | "local_library";

export const SOURCE_HINT_OPTIONS: Array<{ value: SourceHint; label: string }> = [
  { value: "auto", label: "自动识别" },
  { value: "direct_url", label: "直链媒体" },
  { value: "site_media", label: "网站媒体" },
  { value: "jellyfin", label: "Jellyfin" },
  { value: "local_library", label: "本地媒体库" },
];

export interface ApiEnvelope<T> {
  code: number;
  message: string;
  data: T;
  request_id: string;
}

export interface ResolveMedia {
  title?: string;
  stream_url?: string;
  duration_seconds?: number;
}

export interface ResolveData {
  resolved?: boolean;
  source_plugin?: string;
  media?: ResolveMedia;
  stage?: string;
}

export interface PlayData {
  status?: string;
  device_id?: string;
  source_plugin?: string;
  transport?: string;
  stage?: string;
  sid?: string;
  error_code?: string;
  state?: PlayerStateData;
  extra?: Record<string, unknown>;
}

export interface ControlData {
  status?: string;
  device_id?: string;
  transport?: string;
  reachable?: boolean;
  stage?: string;
  error_code?: string;
}

export interface PlayerStateTrack {
  id: string;
  entity_id?: string;
  title: string;
  artist?: string;
  album?: string;
  source?: string;
}

export interface PlayerStateContext {
  id: string;
  name: string;
  current_index: number | null;
}

export type TransportState = "idle" | "starting" | "switching" | "playing" | "paused" | "stopped" | "error";

export interface PlayerStateData {
  // 新规范字段
  device_id: string;
  revision: number;
  play_session_id: string;
  transport_state: TransportState;
  track: PlayerStateTrack | null;
  context: PlayerStateContext | null;
  position_ms: number;
  duration_ms: number;
  volume: number;
  snapshot_at_ms: number;

  // 兼容旧字段（deprecated，新代码不得读取）
  /** @deprecated 改读 transport_state */
  is_playing?: boolean;
  /** @deprecated 改读 track?.title */
  cur_music?: string;
  /** @deprecated 改读 position_ms */
  offset?: number;
  /** @deprecated 改读 duration_ms */
  duration?: number;
  /** @deprecated 改读 track?.id */
  current_track_id?: string;
  /** @deprecated 改读 context?.current_index */
  current_index?: number | null;
  /** @deprecated 改读 context?.id */
  context_type?: string | null;
  /** @deprecated 改读 context?.id */
  context_id?: string | null;
  /** @deprecated 改读 context?.name */
  context_name?: string | null;
}

export type PlayMode = "one" | "all" | "random" | "single" | "sequence";

export interface DeviceRow {
  device_id: string;
  name?: string;
  model?: string;
  online?: boolean;
}

export interface DevicesData {
  devices: DeviceRow[];
}

export interface PlaylistItem {
  id: string;
  entity_id?: string;
  title: string;
}

export interface LibraryPlaylistsData {
  playlists: Record<string, PlaylistItem[]>;
}

export interface LibraryMusicInfoData {
  name?: string;
  url?: string;
  duration_seconds?: number;
}

export interface OnlineSearchItemData {
  name?: string;
  title?: string;
  artist?: string;
}

export interface OnlineSearchData {
  items: OnlineSearchItemData[];
  total?: number;
}

export interface SystemStatusData {
  status?: string;
  version?: string;
  devices_count?: number;
}

export interface SystemSettingsDeviceRow {
  device_id: string;
  name?: string;
  model?: string;
  online?: boolean;
}

export interface SystemSettingsData {
  settings: Record<string, unknown>;
  device_ids: string[];
  devices: SystemSettingsDeviceRow[];
}

export interface SystemSettingsSaveData {
  status?: string;
  saved?: boolean;
}

export interface SystemSettingItemUpdateData {
  status?: string;
  updated?: boolean;
  key?: string;
}

export interface ShutdownTimerData {
  status?: string;
  device_id?: string;
  minutes?: number;
}

export interface FavoritesData {
  status?: string;
  device_id?: string;
  track_name?: string;
}

export interface ApiErrorInfo {
  message: string;
  errorCode: string;
  stage: string | null;
}

export interface PlayRequest {
  device_id: string;
  query: string;
  source_hint?: SourceHint;
  options?: Record<string, unknown>;
  request_id?: string;
}

export interface ResolveRequest {
  query: string;
  source_hint?: SourceHint;
  options?: Record<string, unknown>;
  request_id?: string;
}

function fallbackEnvelope<T>(message: string): ApiEnvelope<T> {
  return {
    code: 10000,
    message,
    data: {} as T,
    request_id: "",
  };
}

async function safeGet<T>(path: string): Promise<ApiEnvelope<T>> {
  try {
    return await apiGetJson<ApiEnvelope<T>>(path);
  } catch (err) {
    const msg = err instanceof Error ? err.message : "network error";
    return fallbackEnvelope<T>(msg);
  }
}

async function safePost<T>(path: string, payload: unknown): Promise<ApiEnvelope<T>> {
  try {
    return await apiPostJson<ApiEnvelope<T>>(path, payload);
  } catch (err) {
    const msg = err instanceof Error ? err.message : "network error";
    return fallbackEnvelope<T>(msg);
  }
}

export function isApiOk<T>(out: ApiEnvelope<T>): boolean {
  return Number(out.code) === 0;
}

export function apiErrorText<T>(out: ApiEnvelope<T>): string {
  const stage = (out.data as { stage?: string })?.stage;
  const stageText = stage ? `阶段=${stage}，` : "";
  return `${stageText}${out.message || "请求失败"}`;
}

export function apiErrorInfo<T>(out: ApiEnvelope<T>): ApiErrorInfo {
  const data = (out.data || {}) as Record<string, unknown>;
  const numCode = Number(out.code ?? 0);
  const codeToErrorCode: Record<number, string> = {
    40001: "E_INVALID_REQUEST",
    20002: "E_RESOLVE_NONZERO_EXIT",
    30001: "E_STREAM_NOT_FOUND",
    40002: "E_XIAOMI_PLAY_FAILED",
    40004: "E_DEVICE_NOT_FOUND",
  };
  const codeToStage: Record<number, string> = {
    40001: "request",
    20002: "resolve",
    30001: "prepare",
    40002: "dispatch",
    40004: "request",
    10000: "system",
  };
  const stage =
    String(data.stage || "") ||
    (numCode !== 0 ? codeToStage[numCode] || "" : "");
  const errorCode = String(data.error_code || "") || (numCode !== 0 ? codeToErrorCode[numCode] || "" : "");
  return {
    message: String(out.message || data.message || "请求失败"),
    errorCode,
    stage: stage || null,
  };
}

export function mapPluginName(plugin: string | undefined): string {
  if (!plugin) {
    return "-";
  }
  const value = plugin.toLowerCase();
  if (value.includes("direct")) {
    return "DirectUrlSourcePlugin";
  }
  if (value.includes("site")) {
    return "SiteMediaSourcePlugin";
  }
  if (value.includes("jellyfin")) {
    return "JellyfinSourcePlugin";
  }
  if (value.includes("local")) {
    return "LocalLibrarySourcePlugin";
  }
  return plugin;
}

export async function play(request: PlayRequest): Promise<ApiEnvelope<PlayData>> {
  return await safePost<PlayData>("/api/v1/play", request);
}

export async function resolve(request: ResolveRequest): Promise<ApiEnvelope<ResolveData>> {
  return await safePost<ResolveData>("/api/v1/resolve", request);
}

export async function stop(deviceId: string): Promise<ApiEnvelope<ControlData>> {
  return await safePost<ControlData>("/api/v1/control/stop", { device_id: deviceId });
}

export async function pause(deviceId: string): Promise<ApiEnvelope<ControlData>> {
  return await safePost<ControlData>("/api/v1/control/pause", { device_id: deviceId });
}

export async function resume(deviceId: string): Promise<ApiEnvelope<ControlData>> {
  return await safePost<ControlData>("/api/v1/control/resume", { device_id: deviceId });
}

export async function tts(deviceId: string, text: string): Promise<ApiEnvelope<ControlData>> {
  return await safePost<ControlData>("/api/v1/control/tts", { device_id: deviceId, text });
}

export async function setVolume(deviceId: string, volume: number): Promise<ApiEnvelope<ControlData>> {
  return await safePost<ControlData>("/api/v1/control/volume", { device_id: deviceId, volume });
}

export async function probe(deviceId: string): Promise<ApiEnvelope<ControlData>> {
  return await safePost<ControlData>("/api/v1/control/probe", { device_id: deviceId });
}

export async function previous(deviceId: string): Promise<ApiEnvelope<ControlData>> {
  return await safePost<ControlData>("/api/v1/control/previous", { device_id: deviceId });
}

export async function next(deviceId: string): Promise<ApiEnvelope<ControlData>> {
  return await safePost<ControlData>("/api/v1/control/next", { device_id: deviceId });
}

export async function setPlayMode(deviceId: string, playMode: PlayMode): Promise<ApiEnvelope<ControlData & { play_mode?: PlayMode }>> {
  return await safePost<ControlData & { play_mode?: PlayMode }>("/api/v1/control/play-mode", {
    device_id: deviceId,
    play_mode: playMode,
  });
}

export async function setShutdownTimer(deviceId: string, minutes: number): Promise<ApiEnvelope<ShutdownTimerData>> {
  return await safePost<ShutdownTimerData>("/api/v1/control/shutdown-timer", { device_id: deviceId, minutes });
}

export async function addFavorite(deviceId: string, musicName: string): Promise<ApiEnvelope<FavoritesData>> {
  return await safePost<FavoritesData>("/api/v1/library/favorites/add", {
    device_id: deviceId,
    track_name: musicName,
  });
}

export async function removeFavorite(deviceId: string, trackName: string): Promise<ApiEnvelope<FavoritesData>> {
  return await safePost<FavoritesData>("/api/v1/library/favorites/remove", {
    device_id: deviceId,
    track_name: trackName,
  });
}

export async function libraryRefresh(): Promise<ApiEnvelope<{ status?: string; refreshed?: boolean }>> {
  return await safePost<{ status?: string; refreshed?: boolean }>("/api/v1/library/refresh", {});
}

export async function getLibraryPlaylists(): Promise<ApiEnvelope<LibraryPlaylistsData>> {
  return await safeGet<LibraryPlaylistsData>("/api/v1/library/playlists");
}

export async function getLibraryMusicInfo(name: string): Promise<ApiEnvelope<LibraryMusicInfoData>> {
  return await safeGet<LibraryMusicInfoData>(`/api/v1/library/music-info?name=${encodeURIComponent(name)}`);
}

export async function searchOnline(keyword: string): Promise<ApiEnvelope<OnlineSearchData>> {
  return await safeGet<OnlineSearchData>(
    `/api/v1/search/online?keyword=${encodeURIComponent(keyword)}&plugin=all&page=1&limit=20`,
  );
}

export async function getDevices(): Promise<ApiEnvelope<DevicesData>> {
  return await safeGet<DevicesData>("/api/v1/devices");
}

export async function getSystemStatus(): Promise<ApiEnvelope<SystemStatusData>> {
  return await safeGet<SystemStatusData>("/api/v1/system/status");
}

export async function getSystemSettings(): Promise<ApiEnvelope<SystemSettingsData>> {
  return await safeGet<SystemSettingsData>("/api/v1/system/settings");
}

export async function saveSystemSettings(
  settings: Record<string, unknown>,
  deviceIds: string[],
): Promise<ApiEnvelope<SystemSettingsSaveData>> {
  return await safePost<SystemSettingsSaveData>("/api/v1/system/settings", {
    settings,
    device_ids: deviceIds,
  });
}

export async function updateSystemSettingItem(
  key: string,
  value: unknown,
): Promise<ApiEnvelope<SystemSettingItemUpdateData>> {
  return await safePost<SystemSettingItemUpdateData>("/api/v1/system/settings/item", {
    key,
    value,
  });
}

export async function getPlayerState(deviceId: string): Promise<ApiEnvelope<PlayerStateData>> {
  return await safeGet<PlayerStateData>(`/api/v1/player/state?device_id=${encodeURIComponent(deviceId)}`);
}

export function getPlayerStreamUrl(deviceId: string): string {
  return `/api/v1/player/stream?device_id=${encodeURIComponent(deviceId)}`;
}
