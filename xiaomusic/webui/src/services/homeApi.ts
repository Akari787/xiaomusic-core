import { apiGet, apiPost } from "./apiClient";

export async function fetchVersion(): Promise<{ version?: string }> {
  return (await apiGet<{ version?: string }>("/getversion")) as { version?: string };
}

export async function fetchSettingsWithDevices<T extends Record<string, unknown>>(): Promise<T & { device_list?: unknown[] }> {
  return (await apiGet<T & { device_list?: unknown[] }>("/getsetting?need_device_list=true")) as T & { device_list?: unknown[] };
}

export async function saveSettingsPayload(payload: Record<string, unknown>): Promise<unknown> {
  return (await apiPost<unknown>("/savesetting", payload)) as unknown;
}

export async function fetchMusicList<T extends Record<string, string[]>>(): Promise<T> {
  return (await apiGet<T>("/musiclist")) as T;
}

export async function fetchMusicInfo(name: string): Promise<{ ret?: string; name?: string; url?: string; tags?: { duration?: number } }> {
  return (await apiGet<{ ret?: string; name?: string; url?: string; tags?: { duration?: number } }>(
    `/musicinfo?name=${encodeURIComponent(name)}&musictag=true`,
  )) as { ret?: string; name?: string; url?: string; tags?: { duration?: number } };
}

export async function searchOnlineMusic(keyword: string): Promise<{ data?: unknown[]; success?: boolean; error?: string }> {
  return (await apiGet<{ data?: unknown[]; success?: boolean; error?: string }>(
    `/api/search/online?keyword=${encodeURIComponent(keyword)}&plugin=all&page=1&limit=20`,
  )) as { data?: unknown[]; success?: boolean; error?: string };
}

export async function fetchQrcode<T>(): Promise<T> {
  return (await apiGet<T>("/api/get_qrcode")) as T;
}

export async function updateSystemSetting<T>(payload: Record<string, unknown>): Promise<T> {
  return (await apiPost<T>("/api/system/modifiysetting", payload)) as T;
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

export async function refreshMusicLibrary<T>(): Promise<T> {
  return (await apiPost<T>("/api/music/refreshlist", {})) as T;
}
