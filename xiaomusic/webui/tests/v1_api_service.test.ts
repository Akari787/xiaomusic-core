import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../src/services/apiClient", () => ({
  apiGetJson: vi.fn(),
  apiPostJson: vi.fn(),
}));

import { apiGetJson, apiPostJson } from "../src/services/apiClient";
import {
  apiErrorInfo,
  apiErrorText,
  getDevices,
  getPlayerState,
  getSystemStatus,
  isApiOk,
  pause,
  play,
  probe,
  resolve,
  resume,
  setVolume,
  stop,
  tts,
} from "../src/services/v1Api";

const mockedGet = vi.mocked(apiGetJson);
const mockedPost = vi.mocked(apiPostJson);

describe("v1Api service", () => {
  beforeEach(() => {
    mockedGet.mockReset();
    mockedPost.mockReset();
    mockedGet.mockResolvedValue({ code: 0, message: "ok", data: {}, request_id: "rid" });
    mockedPost.mockResolvedValue({ code: 0, message: "ok", data: {}, request_id: "rid" });
  });

  it("calls play and resolve via official endpoints", async () => {
    await play({ device_id: "did-1", query: "q", source_hint: "auto" });
    await resolve({ query: "q", source_hint: "site_media" });
    expect(mockedPost).toHaveBeenCalledWith("/api/v1/play", expect.any(Object));
    expect(mockedPost).toHaveBeenCalledWith("/api/v1/resolve", expect.any(Object));
  });

  it("calls control endpoints via /api/v1/control/*", async () => {
    await stop("did-1");
    await pause("did-1");
    await resume("did-1");
    await tts("did-1", "hello");
    await setVolume("did-1", 25);
    await probe("did-1");

    expect(mockedPost).toHaveBeenCalledWith("/api/v1/control/stop", { device_id: "did-1" });
    expect(mockedPost).toHaveBeenCalledWith("/api/v1/control/pause", { device_id: "did-1" });
    expect(mockedPost).toHaveBeenCalledWith("/api/v1/control/resume", { device_id: "did-1" });
    expect(mockedPost).toHaveBeenCalledWith("/api/v1/control/tts", { device_id: "did-1", text: "hello" });
    expect(mockedPost).toHaveBeenCalledWith("/api/v1/control/volume", { device_id: "did-1", volume: 25 });
    expect(mockedPost).toHaveBeenCalledWith("/api/v1/control/probe", { device_id: "did-1" });
  });

  it("calls official device and system status endpoints", async () => {
    await getDevices();
    await getSystemStatus();
    await getPlayerState("did-1");
    expect(mockedGet).toHaveBeenCalledWith("/api/v1/devices");
    expect(mockedGet).toHaveBeenCalledWith("/api/v1/system/status");
    expect(mockedGet).toHaveBeenCalledWith("/api/v1/player/state?device_id=did-1");
  });

  it("parses standardized envelope error details", () => {
    const out = {
      code: 40002,
      message: "dispatch failed",
      data: { stage: "dispatch" },
      request_id: "rid-1",
    };
    expect(isApiOk(out)).toBe(false);
    expect(apiErrorText(out)).toContain("dispatch");
    expect(apiErrorInfo(out)).toEqual({
      message: "dispatch failed",
      errorCode: "E_XIAOMI_PLAY_FAILED",
      stage: "dispatch",
    });
  });
});
