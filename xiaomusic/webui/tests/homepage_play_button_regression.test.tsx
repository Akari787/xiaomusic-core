// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

const mockedApi = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
}));

const mockedV1 = vi.hoisted(() => ({
  addFavorite: vi.fn(),
  getLibraryMusicInfo: vi.fn(),
  getLibraryPlaylists: vi.fn(),
  next: vi.fn(),
  play: vi.fn(),
  previous: vi.fn(),
  getDevices: vi.fn(),
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
}));

vi.mock("../src/services/apiClient", () => ({
  apiGet: mockedApi.apiGet,
  apiPost: mockedApi.apiPost,
}));

vi.mock("../src/services/v1Api", () => ({
  isApiOk: (out: { code?: number }) => Number(out.code || -1) === 0,
  apiErrorText: (out: { message?: string }) => String(out.message || "request failed"),
  apiErrorInfo: (out: { message?: string }) => ({
    message: String(out.message || "request failed"),
    errorCode: "",
    stage: null,
  }),
  addFavorite: mockedV1.addFavorite,
  getLibraryMusicInfo: mockedV1.getLibraryMusicInfo,
  getLibraryPlaylists: mockedV1.getLibraryPlaylists,
  next: mockedV1.next,
  play: mockedV1.play,
  previous: mockedV1.previous,
  getDevices: mockedV1.getDevices,
  getSystemSettings: mockedV1.getSystemSettings,
  getSystemStatus: mockedV1.getSystemStatus,
  getPlayerState: mockedV1.getPlayerState,
  libraryRefresh: mockedV1.libraryRefresh,
  saveSystemSettings: mockedV1.saveSystemSettings,
  setPlayMode: mockedV1.setPlayMode,
  setShutdownTimer: mockedV1.setShutdownTimer,
  tts: mockedV1.tts,
  updateSystemSettingItem: mockedV1.updateSystemSettingItem,
  setVolume: mockedV1.setVolume,
  stop: mockedV1.stop,
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

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("HomePage play button regression", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(async () => {
    vi.useFakeTimers();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);

    mockedApi.apiGet.mockReset();
    mockedApi.apiPost.mockReset();
    mockedV1.play.mockReset();
    mockedV1.previous.mockReset();
    mockedV1.next.mockReset();
    mockedV1.getLibraryMusicInfo.mockReset();
    mockedV1.getLibraryPlaylists.mockReset();
    mockedV1.setPlayMode.mockReset();
    mockedV1.setShutdownTimer.mockReset();
    mockedV1.addFavorite.mockReset();
    mockedV1.getDevices.mockReset();
    mockedV1.getSystemSettings.mockReset();
    mockedV1.getSystemStatus.mockReset();
    mockedV1.getPlayerState.mockReset();
    mockedV1.libraryRefresh.mockReset();
    mockedV1.saveSystemSettings.mockReset();
    mockedV1.tts.mockReset();
    mockedV1.updateSystemSettingItem.mockReset();
    mockedV1.setVolume.mockReset();
    mockedV1.stop.mockReset();

    mockedApi.apiGet.mockImplementation(async (path: string) => {
      if (path === "/api/auth/status") {
        return { token_valid: true, runtime_auth_ready: true, login_in_progress: false };
      }
      return {};
    });

    mockedApi.apiPost.mockResolvedValue({ ret: "OK" });
    let currentSong = "";
    let currentDuration = 0;
    mockedV1.play.mockImplementation(async () => {
      currentSong = "Song A";
      currentDuration = 180;
      return {
        code: 0,
        message: "ok",
        data: { status: "playing", device_id: "981257654", source_plugin: "local_library", transport: "mina" },
        request_id: "rid-play",
      };
    });
    mockedV1.getPlayerState.mockImplementation(async () => {
      if (!currentSong) {
        return {
          code: 0,
          message: "ok",
          data: { device_id: "981257654", is_playing: false, cur_music: "", offset: 0, duration: 0 },
          request_id: "rid-state-idle",
        };
      }
      return {
        code: 0,
        message: "ok",
        data: { device_id: "981257654", is_playing: true, cur_music: currentSong, offset: 1, duration: currentDuration || 180 },
        request_id: `rid-state-${currentSong}`,
      };
    });
    mockedV1.getDevices.mockResolvedValue({
      code: 0,
      message: "ok",
      data: { devices: [{ device_id: "981257654", name: "XiaoAI", model: "OH2P", online: true }] },
      request_id: "rid-dev",
    });
    mockedV1.getSystemStatus.mockResolvedValue({
      code: 0,
      message: "ok",
      data: { status: "ok", version: "1.0.0", devices_count: 1 },
      request_id: "rid-status",
    });
    mockedV1.getSystemSettings.mockResolvedValue({
      code: 0,
      message: "ok",
      data: {
        settings: {
          mi_did: "981257654",
          public_base_url: "http://127.0.0.1:58090",
          enable_pull_ask: false,
        },
        device_ids: ["981257654"],
        devices: [{ device_id: "981257654", name: "XiaoAI", model: "OH2P", online: true }],
      },
      request_id: "rid-settings",
    });
    mockedV1.getLibraryPlaylists.mockResolvedValue({
      code: 0,
      message: "ok",
      data: { playlists: { 所有歌曲: ["Song A"] } },
      request_id: "rid-playlists",
    });
    mockedV1.getLibraryMusicInfo.mockResolvedValue({
      code: 0,
      message: "ok",
      data: { name: "Song A", url: "http://127.0.0.1:58090/static/media/song-a.mp3", duration_seconds: 180 },
      request_id: "rid-musicinfo",
    });
    mockedV1.libraryRefresh.mockResolvedValue({ code: 0, message: "ok", data: { status: "ok", refreshed: true }, request_id: "rid-refresh" });
    mockedV1.saveSystemSettings.mockResolvedValue({ code: 0, message: "ok", data: { status: "ok", saved: true }, request_id: "rid-save-settings" });
    mockedV1.tts.mockResolvedValue({ code: 0, message: "ok", data: {}, request_id: "rid-tts" });
    mockedV1.updateSystemSettingItem.mockResolvedValue({ code: 0, message: "ok", data: { status: "ok", updated: true, key: "enable_pull_ask" }, request_id: "rid-update-setting" });
    mockedV1.setVolume.mockResolvedValue({ code: 0, message: "ok", data: {}, request_id: "rid-vol" });
    mockedV1.stop.mockResolvedValue({ code: 0, message: "ok", data: {}, request_id: "rid-stop" });
    mockedV1.previous.mockImplementation(async () => {
      currentSong = "Song Prev";
      currentDuration = 210;
      return { code: 0, message: "ok", data: {}, request_id: "rid-prev" };
    });
    mockedV1.next.mockImplementation(async () => {
      currentSong = "Song Next";
      currentDuration = 220;
      return { code: 0, message: "ok", data: {}, request_id: "rid-next" };
    });
    mockedV1.setPlayMode.mockResolvedValue({ code: 0, message: "ok", data: { play_mode: "sequence" }, request_id: "rid-mode" });
    mockedV1.setShutdownTimer.mockResolvedValue({ code: 0, message: "ok", data: { minutes: 1 }, request_id: "rid-timer" });
    mockedV1.addFavorite.mockResolvedValue({ code: 0, message: "ok", data: { music_name: "Song A" }, request_id: "rid-fav" });

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

  it("clicking play calls v1 service with unified payload", async () => {
    const playTooltip = Array.from(container.querySelectorAll(".control-button .tooltip")).find(
      (el) => (el.textContent || "").trim() === "播放",
    );
    const playButton = playTooltip?.parentElement as HTMLElement | null;
    expect(playButton).not.toBeNull();

    await act(async () => {
      playButton?.click();
      await vi.advanceTimersByTimeAsync(3000);
    });

    expect(mockedV1.getLibraryMusicInfo).toHaveBeenCalledWith("Song A");

    expect(mockedV1.play).toHaveBeenCalledTimes(1);
    expect(mockedV1.play).toHaveBeenCalledWith({
      device_id: "981257654",
      query: "Song A",
      source_hint: "local_library",
      options: {
        title: "Song A",
        context_hint: { context_type: "playlist", context_name: "所有歌曲", context_id: "所有歌曲" },
        source_payload: {
          source: "local_library",
          playlist_name: "所有歌曲",
          music_name: "Song A",
          context_type: "playlist",
          context_name: "所有歌曲",
        },
      },
    });

    const announcer = container.querySelector("#sr-announcer");
    expect((announcer?.textContent || "").trim()).not.toContain("/api/v1/play");
  });

  it("routes next/previous/play-mode/timer/favorite through v1 services instead of /cmd", async () => {
    const buttons = Array.from(container.querySelectorAll(".control-button .tooltip"));
    const prev = buttons.find((el) => (el.textContent || "").trim() === "上一首")?.parentElement as HTMLElement;
    const next = buttons.find((el) => (el.textContent || "").trim() === "下一首")?.parentElement as HTMLElement;
    const mode = container.querySelector(".player-controls.button-group .control-button") as HTMLElement | null;
    const favorite = Array.from(container.querySelectorAll(".favorite p")).find((el) => (el.textContent || "").trim() === "收藏")?.parentElement as HTMLElement;

    await act(async () => {
      prev?.click();
      next?.click();
      mode?.click();
      favorite?.click();
      await vi.advanceTimersByTimeAsync(4000);
    });

    const timerEntry = Array.from(container.querySelectorAll(".icon-item p")).find(
      (el) => (el.textContent || "").trim() === "定时",
    )?.parentElement as HTMLElement | undefined;

    await act(async () => {
      timerEntry?.click();
      await vi.advanceTimersByTimeAsync(1000);
    });

    const timerButton = Array.from(container.querySelectorAll("button")).find(
      (el) => (el.textContent || "").trim() === "1分钟后关机",
    ) as HTMLButtonElement | undefined;

    await act(async () => {
      timerButton?.click();
      await vi.advanceTimersByTimeAsync(1000);
    });

    expect(mockedV1.previous).toHaveBeenCalledWith("981257654");
    expect(mockedV1.next).toHaveBeenCalledWith("981257654");
    expect(mockedV1.setPlayMode).toHaveBeenCalledTimes(1);
    expect(mockedV1.setPlayMode.mock.calls[0][0]).toBe("981257654");
    expect(["one", "all", "random", "single", "sequence"]).toContain(mockedV1.setPlayMode.mock.calls[0][1]);
    expect(mockedV1.addFavorite).toHaveBeenCalledWith("981257654", "Song A");
    expect(mockedV1.setShutdownTimer).toHaveBeenCalledWith("981257654", 1);

    const cmdCalls = mockedApi.apiPost.mock.calls.filter((args) => args[0] === "/cmd");
    expect(cmdCalls).toHaveLength(0);
  });

  it("removes deprecated custom command entry from play test modal", async () => {
    const testEntry = Array.from(container.querySelectorAll(".icon-item p")).find(
      (el) => (el.textContent || "").trim() === "测试",
    )?.parentElement as HTMLElement | undefined;

    await act(async () => {
      testEntry?.click();
      await vi.advanceTimersByTimeAsync(1000);
    });

    expect(container.textContent || "").not.toContain("自定义口令");
  });

  it("shows timer copy aligned with v1 formal controls", async () => {
    const timerEntry = Array.from(container.querySelectorAll(".icon-item p")).find(
      (el) => (el.textContent || "").trim() === "定时",
    )?.parentElement as HTMLElement | undefined;

    await act(async () => {
      timerEntry?.click();
      await vi.advanceTimersByTimeAsync(1000);
    });

    const text = container.textContent || "";
    expect(text).toContain("定时设置会直接通过正式控制接口发送到设备。");
    expect(text).not.toContain("兼容口令入口");
    expect(text).not.toContain("语音命令链路");
  });
});
