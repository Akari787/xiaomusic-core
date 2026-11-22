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

    closed = false;
    close() { this.closed = true; }
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
  getPlayerStateWithStatus: vi.fn(),
  libraryRefresh: vi.fn(),
  saveSystemSettings: vi.fn(),
  setPlayMode: vi.fn(),
  setShutdownTimer: vi.fn(),
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
  getPlayerStateWithStatus,
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

function rawState(title: string, overrides: Record<string, unknown> = {}) {
  return {
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
    server_instance_id: "test-epoch",
    ...overrides,
  };
}

function state(title: string, overrides: Record<string, unknown> = {}) {
  return ok({
    ...rawState(title, overrides),
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
    (getPlayerStateWithStatus as ReturnType<typeof vi.fn>).mockImplementation(async (did: string) => {
      const data = await (getPlayerState as ReturnType<typeof vi.fn>)(did);
      return { status: 200, ok: true, data: { ...data, data: { ...data.data, server_instance_id: "test-epoch" } } };
    });
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

  it("does not create an SSE storm while the initial state request is still busy", async () => {
    let initialCalls = 0;
    let resolveInitial: ((value: unknown) => void) | null = null;
    (getPlayerStateWithStatus as ReturnType<typeof vi.fn>).mockImplementation(() => {
      initialCalls += 1;
      return new Promise((resolve) => {
        resolveInitial = resolve;
      });
    });

    await renderHome();
    expect(mockedEventSource.MockEventSource.instances).toHaveLength(1);
    const first = mockedEventSource.MockEventSource.instances[0];
    await act(async () => {
      first.onerror?.(new Event("error"));
      first.onerror?.(new Event("error"));
    });
    await advanceAndFlush(6000);
    expect(initialCalls).toBe(1);
    expect(mockedEventSource.MockEventSource.instances).toHaveLength(1);

    await act(async () => {
      resolveInitial?.({ status: 200, ok: true, data: state("InitialSong") });
    });
    await flushUi(4);
    expect(mockedEventSource.MockEventSource.instances).toHaveLength(2);
    await advanceAndFlush(6000);
    expect(mockedEventSource.MockEventSource.instances).toHaveLength(2);
  });

  it("keeps the latest HomePage device load and active DID when deferred refreshes resolve out of order", async () => {
    const makeDeferred = () => {
      let resolve!: (value: unknown) => void;
      const promise = new Promise((nextResolve) => { resolve = nextResolve; });
      return { promise, resolve };
    };
    const initial = makeDeferred();
    const latest = makeDeferred();
    (apiGet as ReturnType<typeof vi.fn>).mockImplementation(async (path: string) => {
      if (path === "/api/auth/status") return { token_valid: true, runtime_auth_ready: false, login_in_progress: false };
      return {};
    });
    (getDevices as ReturnType<typeof vi.fn>)
      .mockImplementationOnce(() => initial.promise)
      .mockImplementationOnce(() => latest.promise);

    await renderHome();
    const refresh = Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("刷新运行时"));
    expect(refresh).toBeTruthy();
    await act(async () => {
      refresh?.click();
      await Promise.resolve();
      await Promise.resolve();
    });
    await flushUi(4);

    await act(async () => {
      latest.resolve(ok({ devices: [{ device_id: "did-002", name: "Device B", model: "B", online: true }] }));
      await Promise.resolve();
      initial.resolve(ok({ devices: [{ device_id: "did-001", name: "Device A", model: "A", online: true }] }));
    });
    await flushUi(6);

    expect(localStorage.getItem("xm_ui_active_did")).toBe("did-002");
    expect(Array.from(container.querySelectorAll("select.device-selector")[0].options).map((option) => option.value)).toEqual(["did-002"]);
  });

  it("clears active DID when the latest device refresh is empty", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockImplementation(async (path: string) => {
      if (path === "/api/auth/status") return { token_valid: true, runtime_auth_ready: false, login_in_progress: false };
      return {};
    });
    await renderHome();
    (getDevices as ReturnType<typeof vi.fn>).mockResolvedValueOnce(ok({ devices: [] }));
    const refresh = Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("刷新运行时"));
    await act(async () => { refresh?.click(); await Promise.resolve(); await Promise.resolve(); });
    await flushUi(5);
    expect(localStorage.getItem("xm_ui_active_did")).toBeNull();
    expect(Array.from(container.querySelectorAll("select.device-selector")[0].options)).toHaveLength(0);
  });

  it("recovers from an empty device list to the latest non-empty list", async () => {
    (apiGet as ReturnType<typeof vi.fn>).mockImplementation(async (path: string) => {
      if (path === "/api/auth/status") return { token_valid: true, runtime_auth_ready: false, login_in_progress: false };
      return {};
    });
    (getDevices as ReturnType<typeof vi.fn>).mockResolvedValueOnce(ok({ devices: [] }));
    await renderHome();
    (getDevices as ReturnType<typeof vi.fn>).mockResolvedValueOnce(ok({ devices: [{ device_id: "did-002", name: "Device B", model: "B", online: true }] }));
    const refresh = Array.from(container.querySelectorAll("button")).find((button) => button.textContent?.includes("刷新运行时"));
    await act(async () => { refresh?.click(); await Promise.resolve(); await Promise.resolve(); });
    await flushUi(5);
    expect(localStorage.getItem("xm_ui_active_did")).toBe("did-002");
    expect(Array.from(container.querySelectorAll("select.device-selector")[0].options).map((option) => option.value)).toEqual(["did-002"]);
  });

  it("cancels the old reconnect timer when fallback recovery opens one replacement stream", async () => {
    let statusCalls = 0;
    (getPlayerStateWithStatus as ReturnType<typeof vi.fn>).mockImplementation(async () => {
      statusCalls += 1;
      if (statusCalls === 1) return { status: 200, ok: true, data: state("InitialSong") };
      if (statusCalls === 2) return { status: 503, ok: false, data: { code: 10000, message: "down" } };
      return { status: 200, ok: true, data: state("RecoveredSong", { revision: 2 }) };
    });

    await renderHome();
    expect(mockedEventSource.MockEventSource.instances).toHaveLength(1);
    await act(async () => {
      mockedEventSource.MockEventSource.instances[0].onerror?.(new Event("error"));
    });
    await flushUi(3);
    await advanceAndFlush(3000);
    expect(mockedEventSource.MockEventSource.instances).toHaveLength(2);
    await advanceAndFlush(1);
    expect(mockedEventSource.MockEventSource.instances).toHaveLength(2);
  });

  it("runs connected reconciliation at the five-second boundary", async () => {
    let statusCalls = 0;
    (getPlayerStateWithStatus as ReturnType<typeof vi.fn>).mockImplementation(async () => {
      statusCalls += 1;
      return { status: 200, ok: true, data: state(`Song ${statusCalls}`, { revision: statusCalls }) };
    });
    await renderHome();
    expect(statusCalls).toBe(1);
    await advanceAndFlush(4999);
    expect(statusCalls).toBe(1);
    await advanceAndFlush(1);
    expect(statusCalls).toBe(2);
  });

  it("recovers after reconcile transient failure and keeps reconciling", async () => {
    let calls = 0;
    let resolveInitial: ((value: unknown) => void) | null = null;
    (getPlayerStateWithStatus as ReturnType<typeof vi.fn>).mockImplementation(() => {
      calls += 1;
      if (calls === 1) return new Promise((resolve) => { resolveInitial = resolve; });
      if (calls === 2) return Promise.resolve({ status: 503, ok: false, data: { code: 10000 } });
      return Promise.resolve({ status: 200, ok: true, data: state("recovered", { revision: calls }) });
    });
    await renderHome();
    await advanceAndFlush(5000);
    expect(calls).toBe(1);
    await act(async () => { resolveInitial?.({ status: 200, ok: true, data: state("initial") }); });
    await flushUi(4);
    await advanceAndFlush(5000);
    expect(calls).toBe(2);
    await advanceAndFlush(3000);
    expect(calls).toBe(3);
    expect(mockedEventSource.MockEventSource.instances).toHaveLength(2);
    await advanceAndFlush(5000);
    expect(calls).toBe(4);
    expect(mockedEventSource.MockEventSource.instances).toHaveLength(2);
  });

  it("recovers once after a 404 low-frequency probe and does not reopen repeatedly", async () => {
    let calls = 0;
    (getPlayerStateWithStatus as ReturnType<typeof vi.fn>).mockImplementation(async () => {
      calls += 1;
      if (calls === 1) return { status: 200, ok: true, data: state("InitialSong") };
      if (calls === 2) return { status: 404, ok: false, data: { code: 10000 } };
      return { status: 200, ok: true, data: state("Recovered", { revision: calls }) };
    });
    await renderHome();
    const first = mockedEventSource.MockEventSource.instances[0];
    await act(async () => { first.onerror?.(new Event("error")); });
    await flushUi(5);
    await advanceAndFlush(6999);
    expect(calls).toBe(2);
    await advanceAndFlush(1);
    expect(calls).toBe(3);
    expect(mockedEventSource.MockEventSource.instances).toHaveLength(2);
    const recovered = mockedEventSource.MockEventSource.instances[1];
    await act(async () => { recovered.onopen?.(new Event("open")); });
    await advanceAndFlush(14000);
    expect(mockedEventSource.MockEventSource.instances).toHaveLength(2);
    expect(calls).toBe(6);
  });

  it("classifies auth failure as a low-frequency probe instead of a reconnect storm", async () => {
    let statusCalls = 0;
    (getPlayerStateWithStatus as ReturnType<typeof vi.fn>).mockImplementation(async () => {
      statusCalls += 1;
      if (statusCalls === 1) return { status: 200, ok: true, data: state("InitialSong") };
      return { status: 401, ok: false, data: { code: 10000, message: "auth" } };
    });

    await renderHome();
    await act(async () => {
      mockedEventSource.MockEventSource.instances[0].onerror?.(new Event("error"));
    });
    await flushUi(10);
    await advanceAndFlush(3000);
    expect(statusCalls).toBe(2);
    await advanceAndFlush(3999);
    expect(statusCalls).toBe(2);
    await advanceAndFlush(1);
    expect(statusCalls).toBe(3);
  });

  it("cleans reconciliation and EventSource on unmount", async () => {
    await renderHome();
    const source = mockedEventSource.MockEventSource.instances[0];
    (getPlayerStateWithStatus as ReturnType<typeof vi.fn>).mockClear();
    await act(async () => { root.unmount(); });
    await advanceAndFlush(10000);
    expect(getPlayerStateWithStatus).not.toHaveBeenCalled();
    expect(source.closed).toBe(true);
  });

  it("routes stream_error through the same fallback path as onerror", async () => {
    let calls = 0;
    (getPlayerStateWithStatus as ReturnType<typeof vi.fn>).mockImplementation(async () => {
      calls += 1;
      if (calls === 1) return { status: 200, ok: true, data: state("InitialSong") };
      if (calls === 2) return { status: 503, ok: false, data: { code: 10000 } };
      return { status: 200, ok: true, data: state("Recovered", { revision: 3 }) };
    });
    await renderHome();
    const first = mockedEventSource.MockEventSource.instances[0];
    await act(async () => {
      first.listeners.get("stream_error")?.forEach((listener) => listener({ data: "{}" } as MessageEvent));
    });
    await advanceAndFlush(3000);
    expect(calls).toBe(3);
    expect(mockedEventSource.MockEventSource.instances).toHaveLength(2);
  });

  it("rejects old device GET/message/error callbacks after switching to device B", async () => {
    (getDevices as ReturnType<typeof vi.fn>).mockResolvedValue(
      ok({ devices: [
        { device_id: "did-001", name: "Device A", model: "A", online: true },
        { device_id: "did-002", name: "Device B", model: "B", online: true },
      ] }),
    );
    let resolveOldA: ((value: unknown) => void) | null = null;
    (getPlayerStateWithStatus as ReturnType<typeof vi.fn>).mockImplementation((did: string) => {
      if (did === "did-001") {
        return new Promise((resolve) => { resolveOldA = resolve; });
      }
      return Promise.resolve({
        status: 200,
        ok: true,
        data: state("B song", {
          device_id: did,
          server_instance_id: "epoch-b",
          revision: 1,
        }),
      });
    });

    await renderHome();
    const oldSource = mockedEventSource.MockEventSource.instances[0];
    (getPlayerState as ReturnType<typeof vi.fn>).mockClear();
    const selector = container.querySelector("select.device-selector") as HTMLSelectElement;
    await act(async () => {
      selector.value = "did-002";
      selector.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await flushUi(4);
    expect(getPlayerState).not.toHaveBeenCalled();
    const sourceCount = mockedEventSource.MockEventSource.instances.length;
    expect(container.textContent || "").toContain("B song");
    await act(async () => {
      resolveOldA?.({
        status: 200,
        ok: true,
        data: rawState("old A GET", { device_id: "did-001", server_instance_id: "epoch-a", revision: 100 }),
      });
      oldSource.listeners.get("player_state")?.forEach((listener) => listener({
        data: JSON.stringify(rawState("old A SSE", { device_id: "did-001", server_instance_id: "epoch-a", revision: 101 })),
      } as MessageEvent));
      oldSource.onerror?.(new Event("error"));
    });
    await advanceAndFlush(4000);
    expect(container.textContent || "").toContain("B song");
    expect(oldSource.closed).toBe(true);
    expect(mockedEventSource.MockEventSource.instances).toHaveLength(sourceCount);
  });

  it("applies epoch B revision 1, rejects stale same-revision state, and blocks retired epoch A", async () => {
    (getPlayerStateWithStatus as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: 200,
      ok: true,
      data: ok(rawState("A", { server_instance_id: "epoch-a", revision: 100, snapshot_at_ms: 1000 })),
    });
    await renderHome();
    const source = mockedEventSource.MockEventSource.instances[0];
    const send = async (snapshot: Record<string, unknown>) => {
      await act(async () => {
        source.listeners.get("player_state")?.forEach((listener) => listener({ data: JSON.stringify(snapshot) } as MessageEvent));
      });
      await flushUi(2);
    };
    await send(rawState("B", { server_instance_id: "epoch-b", revision: 1, snapshot_at_ms: 2000 }));
    expect(container.textContent || "").toContain("正在播放：B");
    await send(rawState("stale", { server_instance_id: "epoch-b", revision: 1, snapshot_at_ms: 1000, position_ms: 9999 }));
    expect(container.textContent || "").toContain("正在播放：B");
    await send(rawState("retired A", { server_instance_id: "epoch-a", revision: 101, snapshot_at_ms: 3000 }));
    expect(container.textContent || "").toContain("正在播放：B");
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
