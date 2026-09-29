import { describe, expect, it } from "vitest";
import { classifyPlayerStateFailure } from "./failureClassification";
import { shouldApplyPlayerSnapshot } from "./playerStateOrdering";
import {
  interpolatePosition,
  isProgressBaselineValid,
  selectProgressAnchor,
  PROGRESS_BASELINE_TTL_MS,
} from "./progress";
import type { PlayerStateData } from "./v1Api";

const state = (overrides: Partial<PlayerStateData> = {}): PlayerStateData => ({
  device_id: "did-a",
  server_instance_id: "epoch-b",
  revision: 1,
  play_session_id: "session-a",
  transport_state: "playing",
  track: { id: "track-a", title: "Song" },
  context: null,
  position_ms: 1000,
  duration_ms: 100000,
  volume: 50,
  snapshot_at_ms: 1000,
  ...overrides,
});

describe("stage 3B pure helpers", () => {
  it("accepts a new epoch even when its revision is lower", () => {
    expect(shouldApplyPlayerSnapshot(
      { serverInstanceId: "epoch-a", revision: 100, snapshotAtMs: 100000 },
      { server_instance_id: "epoch-b", revision: 1, snapshot_at_ms: 1 },
    )).toBe(true);
    expect(shouldApplyPlayerSnapshot(
      { serverInstanceId: "epoch-b", revision: 2, snapshotAtMs: 2000 },
      { server_instance_id: "epoch-b", revision: 1, snapshot_at_ms: 3000 },
    )).toBe(false);
    expect(shouldApplyPlayerSnapshot(
      { serverInstanceId: "epoch-b", revision: 2, snapshotAtMs: 2000 },
      { server_instance_id: "epoch-b", revision: 2, snapshot_at_ms: 1999 },
    )).toBe(false);
  });

  it("classifies HTTP and envelope failures without using EventSource status", () => {
    expect(classifyPlayerStateFailure(404)).toBe("device_missing");
    expect(classifyPlayerStateFailure(400, { code: 40004 })).toBe("device_missing");
    expect(classifyPlayerStateFailure(401)).toBe("auth_required");
    expect(classifyPlayerStateFailure(403)).toBe("auth_required");
    expect(classifyPlayerStateFailure(503)).toBe("transient");
    expect(classifyPlayerStateFailure(0)).toBe("transient");
  });

  it("caps interpolation at five seconds", () => {
    expect(interpolatePosition(1000, 15_000, 20_000, 100_000)).toBe(6000);
    expect(interpolatePosition(1000, 15_000, 120_000, 100_000)).toBe(6000);
    expect(interpolatePosition(1000, 15_000, 20_000, 4000)).toBe(4000);
  });

  it("requires matching epoch/session/track/revision and a fresh baseline", () => {
    const current = state();
    const baseline = {
      device_id: "did-a",
      server_instance_id: "epoch-b",
      play_session_id: "session-a",
      track_identity: "track-a",
      revision: 1,
      position_ms: 1000,
      snapshot_at_ms: 1000,
      saved_at_ms: 10_000,
      ttl_ms: PROGRESS_BASELINE_TTL_MS,
    };
    expect(isProgressBaselineValid(baseline, current, 20_000)).toBe(true);
    expect(isProgressBaselineValid({ ...baseline, server_instance_id: "old" }, current, 20_000)).toBe(false);
    expect(isProgressBaselineValid({ ...baseline, play_session_id: "old" }, current, 20_000)).toBe(false);
    expect(isProgressBaselineValid({ ...baseline, track_identity: "other" }, current, 20_000)).toBe(false);
    expect(isProgressBaselineValid({ ...baseline, saved_at_ms: 1 }, current, 40_000)).toBe(false);
    expect(isProgressBaselineValid({ ...baseline, saved_at_ms: 30_001 }, current, 20_000)).toBe(false);
    const anchor1 = selectProgressAnchor(current, baseline, 100_000);
    const anchor2 = selectProgressAnchor(current, baseline, 100_000);
    expect(anchor1).toEqual({ position_ms: 1000, snapshot_at_ms: 1000 });
    expect(anchor2).toEqual(anchor1);
    expect(interpolatePosition(anchor2.position_ms, anchor2.snapshot_at_ms, 100_000, current.duration_ms)).toBe(6000);
  });
});
