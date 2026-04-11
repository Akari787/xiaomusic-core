const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";

export interface ApiResponse<T = unknown> {
  [key: string]: unknown;
}

function buildUrl(path: string): string {
  if (!API_BASE_URL) {
    return path;
  }
  return `${API_BASE_URL.replace(/\/$/, "")}${path}`;
}

async function requestJson<T>(path: string, init: RequestInit): Promise<T> {
  const response = await fetch(buildUrl(path), {
    credentials: "include",
    ...init,
  });
  return (await response.json()) as T;
}

export async function apiGetJson<T>(path: string): Promise<T> {
  return await requestJson<T>(path, { method: "GET" });
}

export async function apiPostJson<T>(path: string, payload: unknown): Promise<T> {
  return await requestJson<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function apiPutJson<T>(path: string, payload: unknown): Promise<T> {
  return await requestJson<T>(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function apiGet<T = unknown>(path: string): Promise<ApiResponse<T>> {
  return await apiGetJson<ApiResponse<T>>(path);
}

export async function apiPost<T = unknown>(path: string, payload: unknown): Promise<ApiResponse<T>> {
  return await apiPostJson<ApiResponse<T>>(path, payload);
}

export async function apiPut<T = unknown>(path: string, payload: unknown): Promise<ApiResponse<T>> {
  return await apiPutJson<ApiResponse<T>>(path, payload);
}
