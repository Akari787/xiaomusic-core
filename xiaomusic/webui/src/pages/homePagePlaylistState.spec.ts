import { describe, expect, it } from "vitest";

import type { PlayerStateData } from "../services/v1Api";
import {
  buildPendingSelectionForPlayback,
  doesServerStateConfirmPending,
  getPlaybackContextName,
  isSubmittingPending,
  markPendingSubmittingState,
  shouldClearPendingSelection,
  shouldGuardPendingNativeSwitch,
  type PendingSelection,
} from "./HomePage";

function makeState(overrides: Partial<PlayerStateData> = {}): PlayerStateData {
  return {
    device_id: "did-1",
    revision: 10,
    play_session_id: "session-10",
    transport_state: "playing",
    track: {
      id: "track-1",
      title: "Song A",
      artist: "Artist A",
      album: "Album A",
      source: "local_library",
    },
    context: {
      id: "playlist-1",
      name: "列表A",
      current_index: 0,
    },
    position_ms: 3000,
    duration_ms: 180000,
    volume: 50,
    snapshot_at_ms: 10000,
    ...overrides,
  };
}

function makePending(overrides: Partial<PendingSelection> = {}): PendingSelection {
  return {
    playlist: "列表A",
    trackId: "track-1",
    trackTitle: "Song A",
    anchorPlaySessionId: "session-9",
    anchorRevision: 9,
    submitting: true,
    ...overrides,
  };
}

describe("HomePage playlist pending state helpers", () => {
  describe("8.1 pending 状态不清理场景", () => {
    it("browsing 态不会因为服务端 track/context 推进而被清理", () => {
      const prevState = makeState({
        revision: 9,
        play_session_id: "session-9",
        track: { id: "track-old", title: "Old Song" },
        context: { id: "playlist-old", name: "旧列表", current_index: 1 },
      });
      const nextState = makeState({
        revision: 10,
        play_session_id: "session-10",
        track: { id: "track-2", title: "Song B" },
        context: { id: "playlist-2", name: "列表B", current_index: 0 },
      });

      expect(isSubmittingPending(makePending({ submitting: false }))).toBe(false);
      expect(
        shouldClearPendingSelection(
          makePending({ submitting: false }),
          prevState,
          nextState,
        ),
      ).toBe(false);
    });

    it("submitting 态在 revision 仅推进 position/snapshot 时不会被清理", () => {
      const prevState = makeState({
        revision: 9,
        play_session_id: "session-9",
        position_ms: 1000,
        snapshot_at_ms: 9000,
      });
      const nextState = makeState({
        revision: 10,
        play_session_id: "session-9",
        position_ms: 2000,
        snapshot_at_ms: 10000,
      });

      expect(
        shouldClearPendingSelection(
          makePending({
            anchorPlaySessionId: "session-9",
            anchorRevision: 9,
            playlist: "列表B",
            trackId: "track-2",
            trackTitle: "Song B",
          }),
          prevState,
          nextState,
        ),
      ).toBe(false);
    });

    it("submitting 态在 revision 推进但目标未命中时不会被清理", () => {
      const prevState = makeState({
        revision: 9,
        play_session_id: "session-9",
      });
      const nextState = makeState({
        revision: 10,
        play_session_id: "session-9",
        track: { id: "track-other", title: "Other Song" },
        context: { id: "playlist-other", name: "其他列表", current_index: 3 },
      });

      expect(
        shouldClearPendingSelection(
          makePending({
            anchorPlaySessionId: "session-9",
            anchorRevision: 9,
          }),
          prevState,
          nextState,
        ),
      ).toBe(false);
    });
  });

  describe("8.2 pending 在服务端确认后清理", () => {
    it("submitting 态在 play_session_id 变化且 playlist + track 命中时会被清理", () => {
      const prevState = makeState({
        revision: 9,
        play_session_id: "session-9",
        track: { id: "track-old", title: "Old Song" },
        context: { id: "playlist-old", name: "旧列表", current_index: 1 },
      });
      const nextState = makeState({
        revision: 10,
        play_session_id: "session-10",
        track: { id: "track-1", title: "Song A" },
        context: { id: "playlist-1", name: "列表A", current_index: 0 },
      });

      expect(doesServerStateConfirmPending(makePending(), nextState)).toBe(true);
      expect(shouldClearPendingSelection(makePending(), prevState, nextState)).toBe(true);
    });

    it("trackId 缺失时会回退到 trackTitle 命中以确认 pending", () => {
      const state = makeState({
        context: { id: "playlist-1", name: "  列表A  ", current_index: 0 },
        track: { id: "track-1", title: "Song A" },
      });

      expect(getPlaybackContextName(state)).toBe("列表A");
      expect(
        doesServerStateConfirmPending(
          makePending({ trackId: null, trackTitle: "Song A" }),
          state,
        ),
      ).toBe(true);
    });
  });

  describe("8.3 submitting 态 revision 前进且 transport 停止时清理", () => {
    it("revision 推进且进入 stopped 时会被清理", () => {
      const prevState = makeState({
        revision: 9,
        play_session_id: "session-9",
      });
      const nextState = makeState({
        revision: 10,
        play_session_id: "session-9",
        transport_state: "stopped",
      });

      expect(
        shouldClearPendingSelection(
          makePending({
            anchorPlaySessionId: "session-9",
            anchorRevision: 9,
            playlist: "列表B",
            trackId: "track-2",
            trackTitle: "Song B",
          }),
          prevState,
          nextState,
        ),
      ).toBe(true);
    });

    it("revision 推进且进入 idle 时也会被清理", () => {
      const prevState = makeState({
        revision: 9,
        play_session_id: "session-9",
      });
      const nextState = makeState({
        revision: 10,
        play_session_id: "session-9",
        transport_state: "idle",
      });

      expect(
        shouldClearPendingSelection(
          makePending({
            anchorPlaySessionId: "session-9",
            anchorRevision: 9,
            playlist: "列表B",
            trackId: "track-2",
            trackTitle: "Song B",
          }),
          prevState,
          nextState,
        ),
      ).toBe(true);
    });
  });

  describe("8.4 browsing/submitting 双态与 native switch guard", () => {
    it("playPlaylistTrack 开头会补齐 browsing pending，即使之前没有浏览态", () => {
      const state = makeState({
        revision: 12,
        play_session_id: "session-12",
        track: { id: "track-server", title: "Server Song" },
      });

      expect(buildPendingSelectionForPlayback(null, "列表B", "Song B", "track-2", state)).toEqual({
        playlist: "列表B",
        trackId: "track-2",
        trackTitle: "Song B",
        anchorPlaySessionId: "session-12",
        anchorRevision: 12,
        submitting: false,
      });
    });

    it("markPendingSubmitting 会把 browsing pending 切到 submitting 并刷新 anchor", () => {
      const prevPending = makePending({
        anchorPlaySessionId: "session-7",
        anchorRevision: 7,
        submitting: false,
      });
      const latestState = makeState({
        revision: 13,
        play_session_id: "session-13",
      });

      expect(markPendingSubmittingState(prevPending, latestState)).toEqual({
        ...prevPending,
        submitting: true,
        anchorPlaySessionId: "session-13",
        anchorRevision: 13,
      });
      expect(markPendingSubmittingState(null, latestState)).toBeNull();
    });

    it("switchTrack 在 pending 存在且 songs 为空时阻止走设备原生 next/previous", () => {
      expect(shouldGuardPendingNativeSwitch(makePending(), 0)).toBe(true);
      expect(shouldGuardPendingNativeSwitch(makePending(), 2)).toBe(false);
      expect(shouldGuardPendingNativeSwitch(null, 0)).toBe(false);
    });
  });

  describe("8.5 验收标准对应", () => {
    it("辅助规则组合符合阶段4验收口径：浏览态保留、确认后清理、停止态收敛", () => {
      const browsingPending = makePending({
        submitting: false,
        playlist: "列表B",
        trackId: "track-2",
        trackTitle: "Song B",
      });
      const submittingPending = makePending({
        submitting: true,
        playlist: "列表B",
        trackId: "track-2",
        trackTitle: "Song B",
      });
      const prevState = makeState({
        revision: 9,
        play_session_id: "session-9",
        track: { id: "track-old", title: "Old Song" },
        context: { id: "playlist-old", name: "旧列表", current_index: 1 },
      });
      const confirmedState = makeState({
        revision: 10,
        play_session_id: "session-10",
        track: { id: "track-2", title: "Song B" },
        context: { id: "playlist-2", name: "列表B", current_index: 0 },
      });
      const stoppedState = makeState({
        revision: 10,
        play_session_id: "session-9",
        transport_state: "stopped",
      });

      expect(shouldClearPendingSelection(browsingPending, prevState, confirmedState)).toBe(false);
      expect(shouldClearPendingSelection(submittingPending, prevState, confirmedState)).toBe(true);
      expect(shouldClearPendingSelection(submittingPending, prevState, stoppedState)).toBe(true);
    });
  });
});
