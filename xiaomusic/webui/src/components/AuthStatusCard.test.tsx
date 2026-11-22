// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";

import { AuthStatusCard } from "./AuthStatusCard";
import { fetchAuthStatus } from "../services/auth";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("../services/auth", () => ({
  fetchAuthStatus: vi.fn(),
  logoutAuth: vi.fn(),
  reloadAuthRuntime: vi.fn(),
}));

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

it("warns about suspended renewal without hiding ready runtime; polls and cleans up", async () => {
  vi.useFakeTimers();
  vi.mocked(fetchAuthStatus).mockResolvedValue({
    status_reason: "persistent_auth_refresh_suspended",
    credential_warning: "refresh_suspended",
    runtime_auth_ready: true,
    token_exists: true,
  });
  const node = document.createElement("div");
  const root = createRoot(node);
  await act(async () => { root.render(<AuthStatusCard />); });
  expect(node.textContent).toContain("当前运行时仍可用");
  expect(node.textContent).toContain("自动续期已暂停");
  expect(node.querySelector("a")?.getAttribute("href")).toBe("/webui/#/");
  await act(async () => { vi.advanceTimersByTime(60_000); });
  expect(fetchAuthStatus).toHaveBeenCalledTimes(2);
  await act(async () => { root.unmount(); });
  vi.advanceTimersByTime(60_000);
  expect(fetchAuthStatus).toHaveBeenCalledTimes(2);
});
