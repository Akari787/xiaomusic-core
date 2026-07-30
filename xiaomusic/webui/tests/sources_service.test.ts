import { describe, expect, it, vi, beforeEach } from "vitest";

// Mock apiClient with safe wrappers
vi.mock("../src/services/apiClient", () => ({
  apiGetJson: vi.fn(),
  apiPostJson: vi.fn(),
  apiPutJson: vi.fn(),
}));

import { apiGetJson, apiPostJson, apiPutJson } from "../src/services/apiClient";
import {
  fetchSources,
  reloadSources,
  enableSource,
  disableSource,
} from "../src/services/sources";

const mockedGet = vi.mocked(apiGetJson);
const mockedPost = vi.mocked(apiPostJson);
const mockedPut = vi.mocked(apiPutJson);

describe("sources service", () => {
  beforeEach(() => {
    mockedGet.mockReset();
    mockedPost.mockReset();
    mockedPut.mockReset();
    mockedGet.mockResolvedValue({ code: 0, message: "ok", data: {}, request_id: "rid" });
    mockedPost.mockResolvedValue({ code: 0, message: "ok", data: {}, request_id: "rid" });
    mockedPut.mockResolvedValue({ code: 0, message: "ok", data: {}, request_id: "rid" });
  });

  it("fetchSources calls GET /api/admin/v1/sources", async () => {
    await fetchSources();
    expect(mockedGet).toHaveBeenCalledWith("/api/admin/v1/sources");
    expect(mockedGet).toHaveBeenCalledTimes(1);
  });

  it("reloadSources calls POST /api/admin/v1/sources/reload", async () => {
    await reloadSources();
    expect(mockedPost).toHaveBeenCalledWith("/api/admin/v1/sources/reload", {});
    expect(mockedPost).toHaveBeenCalledTimes(1);
  });

  it("enableSource calls PUT /api/admin/v1/sources/{name}/enable", async () => {
    await enableSource("test-plugin");
    expect(mockedPut).toHaveBeenCalledWith(
      "/api/admin/v1/sources/test-plugin/enable",
      {},
    );
    expect(mockedPut).toHaveBeenCalledTimes(1);
  });

  it("disableSource calls PUT /api/admin/v1/sources/{name}/disable", async () => {
    await disableSource("test-plugin");
    expect(mockedPut).toHaveBeenCalledWith(
      "/api/admin/v1/sources/test-plugin/disable",
      {},
    );
    expect(mockedPut).toHaveBeenCalledTimes(1);
  });

  it("enableSource encodes special characters in name", async () => {
    await enableSource("my plugin/with?special");
    expect(mockedPut).toHaveBeenCalledWith(
      "/api/admin/v1/sources/my%20plugin%2Fwith%3Fspecial/enable",
      {},
    );
  });
});
