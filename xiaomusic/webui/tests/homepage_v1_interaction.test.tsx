// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

const mocked = vi.hoisted(() => ({
  getDevices: vi.fn(),
  getSystemStatus: vi.fn(),
  resolve: vi.fn(),
  play: vi.fn(),
  stop: vi.fn(),
  pause: vi.fn(),
  resume: vi.fn(),
  tts: vi.fn(),
  setVolume: vi.fn(),
  probe: vi.fn(),
}));

vi.mock("../src/services/v1Api", () => ({
  SOURCE_HINT_OPTIONS: [
    { value: "auto", label: "自动识别" },
    { value: "direct_url", label: "直链媒体" },
    { value: "site_media", label: "网站媒体" },
    { value: "jellyfin", label: "Jellyfin" },
    { value: "local_library", label: "本地媒体库" },
  ],
  mapPluginName: (p?: string) => p || "-",
  isApiOk: (out: { code: number }) => out.code === 0,
  apiErrorText: (out: { message?: string }) => String(out.message || "request failed"),
  getDevices: mocked.getDevices,
  getSystemStatus: mocked.getSystemStatus,
  resolve: mocked.resolve,
  play: mocked.play,
  stop: mocked.stop,
  pause: mocked.pause,
  resume: mocked.resume,
  tts: mocked.tts,
  setVolume: mocked.setVolume,
  probe: mocked.probe,
}));

import { ApiV1DebugPage } from "../src/pages/ApiV1DebugPage";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function ok(data: Record<string, unknown> = {}) {
  return { code: 0, message: "ok", data, request_id: "req-ok" };
}

function setInputValue(el: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")?.set;
  setter?.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

function setSelectValue(el: HTMLSelectElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, "value")?.set;
  setter?.call(el, value);
  el.dispatchEvent(new Event("change", { bubbles: true }));
}

describe("ApiV1DebugPage interaction", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(async () => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);

    mocked.getDevices.mockReset();
    mocked.getSystemStatus.mockReset();
    mocked.resolve.mockReset();
    mocked.play.mockReset();
    mocked.stop.mockReset();
    mocked.pause.mockReset();
    mocked.resume.mockReset();
    mocked.tts.mockReset();
    mocked.setVolume.mockReset();
    mocked.probe.mockReset();

    mocked.getDevices.mockResolvedValue(
      ok({ devices: [{ device_id: "did-1", name: "XiaoAI", model: "OH2P", online: true }] }),
    );
    mocked.getSystemStatus.mockResolvedValue(ok({ status: "ok", version: "1.0.0", devices_count: 1 }));
    mocked.resolve.mockResolvedValue(ok({ resolved: true, source_plugin: "site_media", media: { title: "song" } }));
    mocked.play.mockResolvedValue(
      ok({ status: "playing", device_id: "did-1", source_plugin: "site_media", transport: "mina" }),
    );
    mocked.stop.mockResolvedValue(ok({ status: "stopped", device_id: "did-1", transport: "miio" }));
    mocked.pause.mockResolvedValue(ok({ status: "paused", device_id: "did-1", transport: "miio" }));
    mocked.resume.mockResolvedValue(ok({ status: "resumed", device_id: "did-1", transport: "miio" }));
    mocked.tts.mockResolvedValue(ok({ status: "ok", device_id: "did-1", transport: "miio" }));
    mocked.setVolume.mockResolvedValue(ok({ status: "ok", device_id: "did-1", transport: "miio" }));
    mocked.probe.mockResolvedValue(ok({ status: "ok", device_id: "did-1", transport: "miio", reachable: true }));

    await act(async () => {
      root.render(<ApiV1DebugPage />);
    });
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
  });

  it("calls resolve then play with selected source_hint", async () => {
    const queryInput = container.querySelector('[data-testid="query-input"]') as HTMLInputElement;
    const sourceSelect = container.querySelector('[data-testid="source-hint-select"]') as HTMLSelectElement;
    const resolveButton = container.querySelector('[data-testid="resolve-button"]') as HTMLButtonElement;
    const playButton = container.querySelector('[data-testid="play-button"]') as HTMLButtonElement;

    await act(async () => {
      setInputValue(queryInput, "https://youtube.com/watch?v=abc");
      setSelectValue(sourceSelect, "site_media");
    });

    await act(async () => {
      resolveButton.click();
    });
    expect(mocked.resolve).toHaveBeenCalledWith({
      query: "https://youtube.com/watch?v=abc",
      source_hint: "site_media",
      options: {},
    });

    await act(async () => {
      playButton.click();
    });
    expect(mocked.play).toHaveBeenCalledWith({
      device_id: "did-1",
      query: "https://youtube.com/watch?v=abc",
      source_hint: "site_media",
      options: {},
    });
  });

  it("shows unified error message and request_id for code!=0", async () => {
    mocked.resolve.mockResolvedValueOnce({
      code: 40002,
      message: "transport dispatch failed",
      data: { stage: "dispatch" },
      request_id: "req-fail-1",
    });
    const queryInput = container.querySelector('[data-testid="query-input"]') as HTMLInputElement;
    const resolveButton = container.querySelector('[data-testid="resolve-button"]') as HTMLButtonElement;

    await act(async () => {
      setInputValue(queryInput, "https://example.com/bad.mp3");
      resolveButton.click();
    });

    const msg = (container.querySelector('[data-testid="global-message"]') as HTMLElement).textContent || "";
    expect(msg).toContain("transport dispatch failed");
    expect(msg).toContain("request_id=req-fail-1");
    expect(msg).not.toContain("失败：ok");
  });

  it("shows standardized source naming and hides legacy naming", () => {
    const text = container.textContent || "";
    expect(text).toContain("直链媒体");
    expect(text).toContain("网站媒体");
    expect(text).toContain("Jellyfin");
    expect(text).toContain("本地媒体库");
    expect(text).not.toContain("http_url");
    expect(text).not.toContain("network_audio");
    expect(text).not.toContain("local_music");
  });
});
