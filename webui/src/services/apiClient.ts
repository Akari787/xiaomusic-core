export interface ApiResponse<T = unknown> {
  ok?: boolean;
  success?: boolean;
  error_code?: string | null;
  message?: string | null;
  [key: string]: unknown;
  data?: T;
}

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";

function buildUrl(path: string): string {
  if (!API_BASE_URL) {
    return path;
  }
  return `${API_BASE_URL.replace(/\/$/, "")}${path}`;
}

export async function apiGet<T = unknown>(path: string): Promise<ApiResponse<T>> {
  const resp = await fetch(buildUrl(path), {
    method: "GET",
    credentials: "include",
  });
  return (await resp.json()) as ApiResponse<T>;
}
