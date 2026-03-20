import { apiGet, apiPost } from "./apiClient";

export async function searchOnlineMusic(keyword: string): Promise<{ data?: unknown[]; success?: boolean; error?: string }> {
  return (await apiGet<{ data?: unknown[]; success?: boolean; error?: string }>(
    `/api/search/online?keyword=${encodeURIComponent(keyword)}&plugin=all&page=1&limit=20`,
  )) as { data?: unknown[]; success?: boolean; error?: string };
}

export async function fetchQrcode<T>(): Promise<T> {
  return (await apiGet<T>("/api/get_qrcode")) as T;
}

export async function fetchPlaylistJson<T>(url: string): Promise<T> {
  return (await apiPost<T>("/api/file/fetch_playlist_json", { url })) as T;
}

export async function refreshMusicTag<T>(): Promise<T> {
  return (await apiPost<T>("/refreshmusictag", {})) as T;
}

export async function cleanTempDir<T>(): Promise<T> {
  return (await apiPost<T>("/api/file/cleantempdir", {})) as T;
}
