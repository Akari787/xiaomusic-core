// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

const mockedApi = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
}));

vi.mock("../src/services/apiClient", () => ({
  apiGet: mockedApi.apiGet,
  apiPost: mockedApi.apiPost,
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

    mockedApi.apiGet.mockImplementation(async (path: string) => {
      if (path === "/getversion") {
        return { version: "1.0.0" };
      }
      if (path === "/api/oauth2/status") {
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
      if (path.startsWith("/playingmusic?did=")) {
        return { ret: "OK", is_playing: false, cur_music: "", cur_playlist: "所有歌曲", offset: 0, duration: 0 };
      }
      if (path.startsWith("/getvolume?did=")) {
        return { volume: 45 };
      }
      return {};
    });

    mockedApi.apiPost.mockResolvedValue({ ret: "OK" });

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

  it("clicking play sends playlist+song commands for auto-next playback", async () => {
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

    const cmdCalls = mockedApi.apiPost.mock.calls.filter((args) => args[0] === "/cmd");
    expect(cmdCalls).toHaveLength(2);

    const switchPlaylist = cmdCalls[0][1] as Record<string, unknown>;
    const playSong = cmdCalls[1][1] as Record<string, unknown>;
    expect(switchPlaylist).toMatchObject({ did: "981257654", cmd: "播放列表所有歌曲" });
    expect(playSong).toMatchObject({ did: "981257654", cmd: "播放本地歌曲Song A" });

    const announcer = container.querySelector("#sr-announcer");
    expect((announcer?.textContent || "").trim()).toContain("自动切歌");
  });
});
