import { apiGetJson, apiPostJson, apiPutJson } from "./apiClient";
import type { ApiEnvelope } from "./v1Api";

export type SourceStatus = "active" | "failed" | "disabled";
export type SourceOrigin = "builtin" | "external";

export interface SourceItem {
  name: string;
  origin: SourceOrigin;
  status: SourceStatus;
  version: string | null;
  error: string;
}

export interface SourcesData {
  registry_version: number;
  sources: SourceItem[];
}

export interface SourcesReloadData {
  reloaded: boolean;
  registry_version: number;
  loaded_count: number;
  failed_count: number;
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

async function safePut<T>(path: string, payload: unknown): Promise<ApiEnvelope<T>> {
  try {
    return await apiPutJson<ApiEnvelope<T>>(path, payload);
  } catch (err) {
    const msg = err instanceof Error ? err.message : "network error";
    return fallbackEnvelope<T>(msg);
  }
}

export async function fetchSources(): Promise<ApiEnvelope<SourcesData>> {
  return await safeGet<SourcesData>("/api/v1/sources");
}

export async function reloadSources(): Promise<ApiEnvelope<SourcesReloadData>> {
  return await safePost<SourcesReloadData>("/api/v1/sources/reload", {});
}

export async function enableSource(name: string): Promise<ApiEnvelope<SourceItem>> {
  return await safePut<SourceItem>(`/api/v1/sources/${encodeURIComponent(name)}/enable`, {});
}

export async function disableSource(name: string): Promise<ApiEnvelope<SourceItem>> {
  return await safePut<SourceItem>(`/api/v1/sources/${encodeURIComponent(name)}/disable`, {});
}
