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

export type ApiJsonWithStatus<T> = {
  status: number;
  ok: boolean;
  data: T;
};

async function requestJson<T>(path: string, init: RequestInit): Promise<T> {
  const response = await fetch(buildUrl(path), {
    credentials: "include",
    ...init,
  });
  return (await response.json()) as T;
}

async function requestJsonWithStatus<T>(path: string, init: RequestInit, timeoutMs?: number): Promise<ApiJsonWithStatus<T>> {
  const controller = timeoutMs && timeoutMs > 0 && typeof AbortController !== "undefined" ? new AbortController() : null;
  const timeoutId = controller ? globalThis.setTimeout(() => controller.abort(), timeoutMs) : null;
  try {
    const response = await fetch(buildUrl(path), {
      credentials: "include",
      ...init,
      ...(controller ? { signal: controller.signal } : {}),
    });
    let data = {} as T;
    try {
      data = (await response.json()) as T;
    } catch {
      // Preserve the HTTP status even when the error body is empty/non-JSON.
    }
    return { status: response.status, ok: response.ok, data };
  } catch {
    return { status: 0, ok: false, data: {} as T };
  } finally {
    if (timeoutId !== null) globalThis.clearTimeout(timeoutId);
  }
}

export async function apiGetJson<T>(path: string): Promise<T> {
  return await requestJson<T>(path, { method: "GET" });
}

export async function apiGetJsonWithStatus<T>(path: string, options?: { timeoutMs?: number }): Promise<ApiJsonWithStatus<T>> {
  return await requestJsonWithStatus<T>(path, { method: "GET" }, options?.timeoutMs);
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
