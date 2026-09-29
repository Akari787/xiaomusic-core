import { afterEach, describe, expect, it, vi } from "vitest";
import { apiGetJsonWithStatus } from "../src/services/apiClient";

describe("apiClient status-aware GET timeout", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("aborts a pending GET and returns transient status 0", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => new Promise((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new DOMException("timeout", "AbortError")));
    }));
    vi.stubGlobal("fetch", fetchMock);

    const pending = apiGetJsonWithStatus("/api/v1/player/state?device_id=did-1", { timeoutMs: 8000 });
    await vi.advanceTimersByTimeAsync(8000);

    await expect(pending).resolves.toEqual({ status: 0, ok: false, data: {} });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
