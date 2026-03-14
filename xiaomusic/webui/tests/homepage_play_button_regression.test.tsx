// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

const mockedApi = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
}));

const mockedV1 = vi.hoisted(() => ({
  play: vi.fn(),
  getDevices: vi.fn(),
  getPlayerState: vi.fn(),
  tts: vi.fn(),
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
  play: mockedV1.play,
  getDevices: mockedV1.getDevices,
  getPlayerState: mockedV1.getPlayerState,
  tts: mockedV1.tts,
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
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);

    mockedApi.apiGet.mockReset();
    mockedApi.apiPost.mockReset();
    mockedV1.play.mockReset();
    mockedV1.getDevices.mockReset();
    mockedV1.getPlayerState.mockReset();
    mockedV1.tts.mockReset();
    mockedV1.setVolume.mockReset();
    mockedV1.stop.mockReset();

    mockedApi.apiGet.mockImplementation(async (path: string) => {
      if (path === "/getversion") {
        return { version: "1.0.0" };
      }
      if (path === "/api/auth/status") {
        return { token_valid: true, runtime_auth_ready: true, login_in_progress: false };
      }
      if (path === "/getsetting?need_device_list=true") {
        return {
          mi_did: "981257654",
          public_base_url: "http://127.0.0.1:58090",
          enable_pull_ask: false,
          device_list: [{ miotDID: "981257654", name: "XiaoAI" }],
        };
      }
      if (path === "/musiclist") {
        return { 所有歌曲: ["Song A"] };
      }
      if (path.startsWith("/musicinfo?name=")) {
        return {
          ret: "OK",
          name: "Song A",
          url: "http://127.0.0.1:58090/static/media/song-a.mp3",
        };
      }
      if (path === "/device_list") {
        return { devices: [{ miotDID: "981257654", name: "XiaoAI" }] };
      }
      if (path.startsWith("/getvolume?did=")) {
        return { volume: 45 };
      }
      return {};
    });

    mockedApi.apiPost.mockResolvedValue({ ret: "OK" });
    mockedV1.play.mockResolvedValue({
      code: 0,
      message: "ok",
      data: { status: "playing", device_id: "981257654", source_plugin: "local_library", transport: "mina" },
      request_id: "rid-play",
    });
    mockedV1.getPlayerState.mockResolvedValue({
      code: 0,
      message: "ok",
      data: { device_id: "981257654", is_playing: false, cur_music: "", offset: 0, duration: 0 },
      request_id: "rid-state",
    });
    mockedV1.getDevices.mockResolvedValue({
      code: 0,
      message: "ok",
      data: { devices: [{ device_id: "981257654", name: "XiaoAI", model: "OH2P", online: true }] },
      request_id: "rid-dev",
    });
    mockedV1.tts.mockResolvedValue({ code: 0, message: "ok", data: {}, request_id: "rid-tts" });
    mockedV1.setVolume.mockResolvedValue({ code: 0, message: "ok", data: {}, request_id: "rid-vol" });
    mockedV1.stop.mockResolvedValue({ code: 0, message: "ok", data: {}, request_id: "rid-stop" });

    await act(async () => {
      root.render(<HomePage />);
    });
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
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
    });

    const infoCalls = mockedApi.apiGet.mock.calls.filter((args) => String(args[0]).startsWith("/musicinfo?name="));
    expect(infoCalls).toHaveLength(1);

    expect(mockedV1.play).toHaveBeenCalledTimes(1);
    expect(mockedV1.play).toHaveBeenCalledWith({
      device_id: "981257654",
      query: "http://127.0.0.1:58090/static/media/song-a.mp3",
      source_hint: "auto",
      options: { list_name: "所有歌曲" },
    });

    const announcer = container.querySelector("#sr-announcer");
    expect((announcer?.textContent || "").trim()).not.toContain("/api/v1/play");
  });
});
