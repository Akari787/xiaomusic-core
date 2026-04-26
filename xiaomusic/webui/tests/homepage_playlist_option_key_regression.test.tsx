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

function ok(data: Record<string, unknown> = {}) {
  return { code: 0, message: "ok", data, request_id: "req-ok" };
}

function state(overrides: Record<string, unknown> = {}) {
  return ok({
    device_id: "did-001",
    revision: 1,
    play_session_id: "ps-initial",
    transport_state: "idle",
    track: null,
    context: { type: "playlist", id: "所有歌曲", name: "所有歌曲", current_index: 0 },
    position_ms: 0,
    duration_ms: 0,
    volume: 50,
    snapshot_at_ms: Date.now(),
    ...overrides,
  });
}

async function flushUi(cycles = 1) {
  for (let i = 0; i < cycles; i += 1) {
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
  }
}

describe("HomePage song selector key regression", () => {
  let container: HTMLDivElement;
  let root: Root;

  async function renderHome() {
    await act(async () => {
      root.render(<HomePage />);
    });
    await flushUi(4);
  }

  beforeEach(() => {
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
          所有歌曲: [
            { id: "dup-a", title: "Ana-Lia" },
            { id: "dup-a", title: "Ana-Lia-[58ccd8]" },
            { id: "dup-b", title: "EXEC_COSMOFLIPS/.-KOKIA" },
            { id: "dup-b", title: "EXEC_COSMOFLIPS/.-KOKIA-[135cd9]" },
            { id: "jp-1", title: "831143-nero" },
            { id: "jp-2", title: "Akari-初音ミク" },
            { id: "jp-3", title: "Alice in 冷凍庫-めありー" },
          ],
          日语: [
            { id: "jp-1", title: "831143-nero" },
            { id: "jp-2", title: "Akari-初音ミク" },
            { id: "jp-3", title: "Alice in 冷凍庫-めありー" },
          ],
        },
      }),
    );
    (setVolume as ReturnType<typeof vi.fn>).mockResolvedValue(ok({}));
    (getPlayerState as ReturnType<typeof vi.fn>).mockResolvedValue(state());
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  it("switching playlist does not leak stale songs when source list contains duplicate ids", async () => {
    await renderHome();

    const playlistSelect = container.querySelector("#music_list") as HTMLSelectElement | null;
    expect(playlistSelect).toBeTruthy();

    await act(async () => {
      if (!playlistSelect) return;
      playlistSelect.value = "日语";
      playlistSelect.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await flushUi(3);

    const songSelect = container.querySelector("#music_name") as HTMLSelectElement | null;
    expect(songSelect).toBeTruthy();
    const renderedSongs = Array.from(songSelect?.options || []).map((option) => ({
      value: option.value,
      text: option.textContent || "",
    }));

    expect(renderedSongs).toEqual([
      { value: "jp-1", text: "831143-nero" },
      { value: "jp-2", text: "Akari-初音ミク" },
      { value: "jp-3", text: "Alice in 冷凍庫-めありー" },
    ]);
  });
});
