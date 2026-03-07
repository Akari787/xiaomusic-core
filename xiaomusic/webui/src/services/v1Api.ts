import { apiGetJson, apiPostJson } from "./apiClient";

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
}

export interface ControlData {
  status?: string;
  device_id?: string;
  transport?: string;
  reachable?: boolean;
  stage?: string;
}

export interface DeviceRow {
  device_id: string;
  name?: string;
  model?: string;
  online?: boolean;
}

export interface DevicesData {
  devices: DeviceRow[];
}

export interface SystemStatusData {
  status?: string;
  version?: string;
  devices_count?: number;
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

export async function getDevices(): Promise<ApiEnvelope<DevicesData>> {
  return await safeGet<DevicesData>("/api/v1/devices");
}

export async function getSystemStatus(): Promise<ApiEnvelope<SystemStatusData>> {
  return await safeGet<SystemStatusData>("/api/v1/system/status");
}
