// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

vi.mock("../src/services/apiClient", () => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
}));

const mockedEventSource = vi.hoisted(() => {
  class MockEventSource {
    static instances: MockEventSource[] = [];
    url: string;
    onopen: ((event: Event) => void) | null = null;
    onerror: ((event: Event) => void) | null = null;
    listeners = new Map<string, Set<(event: MessageEvent) => void>>();

    constructor(url: string) {
      this.url = url;
      MockEventSource.instances.push(this);
    }

    addEventListener(type: string, listener: (event: MessageEvent) => void) {
      if (!this.listeners.has(type)) {
        this.listeners.set(type, new Set());
      }
      this.listeners.get(type)?.add(listener);
    }

    removeEventListener(type: string, listener: (event: MessageEvent) => void) {
      this.listeners.get(type)?.delete(listener);
    }

    close() {}
  }

  return { MockEventSource };
});

vi.mock("../src/services/v1Api", () => ({
  isApiOk: (out: { code?: number }) => Number(out.code ?? -1) === 0,
  apiErrorText: (out: { message?: string }) => String(out.message || "request failed"),
  apiErrorInfo: (out: { message?: string }) => ({
    message: String(out.message || "request failed"),
    errorCode: "",
    stage: null,
  }),
  addFavorite: vi.fn(),
  getLibraryMusicInfo: vi.fn(),
  getLibraryPlaylists: vi.fn(),
  getPlayerStreamUrl: vi.fn((deviceId: string) => `http://127.0.0.1:58090/api/v1/player/stream?device_id=${deviceId}`),
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
(globalThis as typeof globalThis & { EventSource?: typeof mockedEventSource.MockEventSource }).EventSource = mockedEventSource.MockEventSource;

async function flushUi(cycles = 1) {
  for (let i = 0; i < cycles; i += 1) {
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
  }
}

async function advanceAndFlush(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
  await flushUi(3);
}

async function waitForText(container: HTMLDivElement, expected: string, rounds = 6) {
  for (let i = 0; i < rounds; i += 1) {
    if ((container.textContent || "").includes(expected)) {
      return;
    }
    await advanceAndFlush(3000);
  }
  expect(container.textContent || "").toContain(expected);
}

function ok(data: Record<string, unknown> = {}) {
  return { code: 0, message: "ok", data, request_id: "req-ok" };
}

function state(title: string, overrides: Record<string, unknown> = {}) {
  return ok({
    device_id: "did-001",
    revision: 1,
    play_session_id: `ps-${title || "idle"}`,
    transport_state: title ? "playing" : "idle",
    track: title ? { id: title, title } : null,
    context: { type: "playlist", id: "Playlist1", name: "Playlist1", current_index: 0 },
    position_ms: 5000,
    duration_ms: 180000,
    volume: 50,
    snapshot_at_ms: Date.now(),
    ...overrides,
  });
}

describe("HomePage playback song switch boundary", () => {
  let container: HTMLDivElement;
  let root: Root;

  async function renderHome() {
    await act(async () => {
      root.render(<HomePage />);
    });
    await flushUi(4);
  }

  beforeEach(async () => {
    vi.useFakeTimers();
    localStorage.clear();
    localStorage.setItem("xm_ui_active_did", "did-001");
    mockedEventSource.MockEventSource.instances.length = 0;
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
      ok({
        playlists: {
          Playlist1: [
            { id: "song-a", title: "Song A" },
            { id: "song-b", title: "Song B" },
            { id: "song-c", title: "Song C" },
          ],
        },
      }),
    );
    (setVolume as ReturnType<typeof vi.fn>).mockResolvedValue(ok({}));
    (getPlayerState as ReturnType<typeof vi.fn>).mockResolvedValue(state("InitialSong"));
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    vi.useRealTimers();
    container.remove();
  });

  it("render shows song from status when status.track.title is populated", async () => {
    await renderHome();
    await waitForText(container, "InitialSong");
  });

  it("b: render switches from old song to new song as getPlayerState returns new data", async () => {
    let callCount = 0;
    (getPlayerState as ReturnType<typeof vi.fn>).mockImplementation(async () => {
      callCount += 1;
      if (callCount === 1) {
        return state("Song A", { revision: 1, play_session_id: "ps-song-a", position_ms: 10000, duration_ms: 180000 });
      }
      return state("Song B", { revision: 2, play_session_id: "ps-song-b", position_ms: 0, duration_ms: 200000 });
    });

    await renderHome();
    await waitForText(container, "正在播放：Song A");

    const text1 = container.textContent || "";
    expect(text1).toContain("正在播放：Song A");

    await waitForText(container, "正在播放：Song B");

    const text2 = container.textContent || "";
    expect(text2).toContain("正在播放：Song B");
  });

  it("d: UI switches to new song when backend returns new track.title", async () => {
    let callCount = 0;
    (getPlayerState as ReturnType<typeof vi.fn>).mockImplementation(async () => {
      callCount += 1;
      if (callCount === 1) {
        return state("Song X", { revision: 1, play_session_id: "ps-song-x", position_ms: 30000, duration_ms: 150000 });
      }
      return state("Song Y", { revision: 2, play_session_id: "ps-song-y", position_ms: 0, duration_ms: 160000 });
    });

    await renderHome();
    await waitForText(container, "正在播放：Song X");

    const text1 = container.textContent || "";
    expect(text1).toContain("正在播放：Song X");

    await waitForText(container, "正在播放：Song Y");

    const text2 = container.textContent || "";
    expect(text2).toContain("正在播放：Song Y");
  });

  it("c: auto-sync uses status.track.title as primary sync source", async () => {
    let callCount = 0;
    (getPlayerState as ReturnType<typeof vi.fn>).mockImplementation(async () => {
      callCount += 1;
      if (callCount === 1) {
        return state("Song API1", { revision: 1, play_session_id: "ps-song-api1", position_ms: 5000, duration_ms: 180000 });
      }
      return state("Song API2", { revision: 2, play_session_id: "ps-song-api2", position_ms: 0, duration_ms: 200000 });
    });

    await renderHome();
    await waitForText(container, "正在播放：Song API1");

    const text = container.textContent || "";
    expect(text).toContain("正在播放：Song API1");

    await waitForText(container, "正在播放：Song API2");

    const text2 = container.textContent || "";
    expect(text2).toContain("正在播放：Song API2");
  });
});
