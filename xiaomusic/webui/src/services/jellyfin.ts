import { apiPostJson } from "./apiClient";

export type JellyfinLoginResult = {
  ok: boolean;
  loggedIn: boolean;
  username: string;
  message: string;
};

type RawResult = {
  jellyfin_logged_in?: boolean;
  jellyfin_username?: string;
  detail?: string;
  sync?: { success?: boolean; list_count?: number; track_count?: number; error?: string };
  data?: RawResult;
};

function normalize(raw: RawResult | null | undefined): RawResult {
  if (raw && typeof raw === "object" && raw.data && typeof raw.data === "object") {
    return { ...raw.data, detail: raw.detail };
  }
  return raw || {};
}

function syncText(sync: RawResult["sync"]): string {
  if (!sync) return "";
  if (sync.success) return `，已同步 ${sync.list_count ?? 0} 个歌单 / ${sync.track_count ?? 0} 首`;
  return `，歌单同步失败：${sync.error || "未知错误"}`;
}

// The password is sent once to the backend and never stored client-side.
export async function loginJellyfin(baseUrl: string, username: string, password: string): Promise<JellyfinLoginResult> {
  try {
    const raw = normalize(
      await apiPostJson<RawResult>("/api/jellyfin/login", { base_url: baseUrl, username, password }),
    );
    if (raw.jellyfin_logged_in) {
      return {
        ok: true,
        loggedIn: true,
        username: String(raw.jellyfin_username || username),
        message: `Jellyfin 登录成功${syncText(raw.sync)}`,
      };
    }
    return { ok: false, loggedIn: false, username: "", message: String(raw.detail || "Jellyfin 登录失败") };
  } catch {
    return { ok: false, loggedIn: false, username: "", message: "Jellyfin 登录请求失败" };
  }
}

export async function logoutJellyfin(): Promise<boolean> {
  try {
    const raw = normalize(await apiPostJson<RawResult>("/api/jellyfin/logout", {}));
    return !raw.jellyfin_logged_in;
  } catch {
    return false;
  }
}
