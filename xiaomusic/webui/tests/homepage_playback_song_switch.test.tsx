// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

vi.mock("../src/services/apiClient", () => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
}));

vi.mock("../src/services/v1Api", () => ({
  isApiOk: (out: { code?: number }) => Number(out.code || -1) === 0,
  apiErrorText: (out: { message?: string }) => String(out.message || "request failed"),
  apiErrorInfo: (out: { message?: string }) => ({
    message: String(out.message || "request failed"),
    errorCode: "",
    stage: null,
  }),
  addFavorite: vi.fn(),
  getLibraryMusicInfo: vi.fn(),
  getLibraryPlaylists: vi.fn(),
  next: vi.fn(),
  play: vi.fn(),
  previous: vi.fn(),
  getDevices: vi.fn(),
  searchOnline: vi.fn(),
  getSystemSettings: vi.fn(),
  getSystemStatus: vi.fn(),
  getPlayerState: vi.fn(),
  libraryRefresh: vi.fn(),
  saveSystemSettings: vi.fn(),
  setPlayMode: vi.fn(),
  setShutdownTimer: vi.fn(),
  tts: vi.fn(),
  updateSystemSettingItem: vi.fn(),
  setVolume: vi.fn(),
  stop: vi.fn(),
  pause: vi.fn(),
  resume: vi.fn(),
}));

vi.mock("../src/theme/ThemeProvider", () => ({
  useTheme: () => ({
    selectedThemeId: "default",
    activeLayout: "default",
    customThemes: [],
    setTheme: vi.fn(),
    uploadThemePackage: vi.fn().mockResolvedValue({ ok: true, name: "" }),
    validationError: "",
  }),
}));

import { HomePage } from "../src/pages/HomePage";
import { apiGet, apiPost } from "../src/services/apiClient";
import {
  getPlayerState,
  getDevices,
  getSystemStatus,
  getSystemSettings,
  getLibraryPlaylists,
  setVolume,
} from "../src/services/v1Api";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function ok(data: Record<string, unknown> = {}) {
  return { code: 0, message: "ok", data, request_id: "req-ok" };
}

describe("HomePage playback song switch boundary", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(async () => {
    vi.useFakeTimers();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);

    vi.clearAllMocks();
    (apiGet as ReturnType<typeof vi.fn>).mockImplementation(async (path: string) => {
      if (path === "/api/auth/status") {
        return { token_valid: true, runtime_auth_ready: true, login_in_progress: false };
      }
      return {};
    });
    (apiPost as ReturnType<typeof vi.fn>).mockResolvedValue({ ret: "OK" });
    (getDevices as ReturnType<typeof vi.fn>).mockResolvedValue(
      ok({ devices: [{ device_id: "did-001", name: "TestSpeaker", model: "OH2P", online: true }] }),
    );
    (getSystemStatus as ReturnType<typeof vi.fn>).mockResolvedValue(ok({ status: "ok", version: "1.0.0", devices_count: 1 }));
    (getSystemSettings as ReturnType<typeof vi.fn>).mockResolvedValue(
      ok({
        settings: { mi_did: "did-001", public_base_url: "http://127.0.0.1:58090", enable_pull_ask: false },
        device_ids: ["did-001"],
        devices: [{ device_id: "did-001", name: "TestSpeaker", model: "OH2P", online: true }],
      }),
    );
    (getLibraryPlaylists as ReturnType<typeof vi.fn>).mockResolvedValue(
      ok({ playlists: { Playlist1: ["Song A", "Song B", "Song C"] } }),
    );
    (setVolume as ReturnType<typeof vi.fn>).mockResolvedValue(ok({}));
    (getPlayerState as ReturnType<typeof vi.fn>).mockResolvedValue(
      ok({ device_id: "did-001", is_playing: true, cur_music: "InitialSong", offset: 5, duration: 180 }),
    );

    await act(async () => {
      root.render(<HomePage />);
    });
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    vi.useRealTimers();
    container.remove();
  });

  it("render shows song from status when status.cur_music is populated", async () => {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    const text = container.textContent || "";
    expect(text).toContain("InitialSong");
  });

  it("b: render switches from old song to new song as getPlayerState returns new data", async () => {
    let callCount = 0;
    (getPlayerState as ReturnType<typeof vi.fn>).mockImplementation(async () => {
      callCount += 1;
      if (callCount === 1) {
        return ok({ device_id: "did-001", is_playing: true, cur_music: "Song A", offset: 10, duration: 180 });
      }
      return ok({ device_id: "did-001", is_playing: true, cur_music: "Song B", offset: 0, duration: 200 });
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });

    const text1 = container.textContent || "";
    expect(text1).toContain("Song A");
    expect(text1).not.toContain("Song B");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });

    const text2 = container.textContent || "";
    expect(text2).toContain("Song B");
    expect(text2).not.toContain("Song A");
  });

  it("d: UI switches to new song when backend returns new cur_music", async () => {
    let callCount = 0;
    (getPlayerState as ReturnType<typeof vi.fn>).mockImplementation(async () => {
      callCount += 1;
      if (callCount === 1) {
        return ok({ device_id: "did-001", is_playing: true, cur_music: "Song X", offset: 30, duration: 150 });
      }
      return ok({ device_id: "did-001", is_playing: true, cur_music: "Song Y", offset: 0, duration: 160 });
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });

    const text1 = container.textContent || "";
    expect(text1).toContain("Song X");
    expect(text1).not.toContain("Song Y");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });

    const text2 = container.textContent || "";
    expect(text2).toContain("Song Y");
    expect(text2).not.toContain("Song X");
  });

  it("c: auto-sync uses status.cur_music as primary sync source", async () => {
    let callCount = 0;
    (getPlayerState as ReturnType<typeof vi.fn>).mockImplementation(async () => {
      callCount += 1;
      if (callCount === 1) {
        return ok({ device_id: "did-001", is_playing: true, cur_music: "Song API1", offset: 5, duration: 180 });
      }
      return ok({ device_id: "did-001", is_playing: true, cur_music: "Song API2", offset: 0, duration: 200 });
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });

    const text = container.textContent || "";
    expect(text).toContain("Song API1");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });

    const text2 = container.textContent || "";
    expect(text2).toContain("Song API2");
  });
});
