import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../src/services/apiClient", () => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
}));

import { apiGet, apiPost } from "../src/services/apiClient";
import { fetchAuthStatus, logoutAuth, reloadAuthRuntime } from "../src/services/auth";

const mockedGet = vi.mocked(apiGet);
const mockedPost = vi.mocked(apiPost);

describe("auth service", () => {
  beforeEach(() => {
    mockedGet.mockReset();
    mockedPost.mockReset();
    mockedGet.mockResolvedValue({});
    mockedPost.mockResolvedValue({});
  });

  it("uses canonical /api/auth endpoints", async () => {
    await fetchAuthStatus();
    await reloadAuthRuntime();
    await logoutAuth();

    expect(mockedGet).toHaveBeenCalledWith("/api/auth/status");
    expect(mockedPost).toHaveBeenCalledWith("/api/auth/refresh", {});
    expect(mockedPost).toHaveBeenCalledWith("/api/auth/logout", {});
  });
});
