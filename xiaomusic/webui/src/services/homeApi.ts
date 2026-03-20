import { apiGet, apiPost } from "./apiClient";

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
