import type { PlayerStateData, TransportState } from "./v1Api";

export const PROGRESS_BASELINE_TTL_MS = 30_000;
export const MAX_INTERPOLATION_MS = 5_000;

export type ProgressBaseline = {
  device_id: string;
  server_instance_id: string;
  play_session_id: string;
  track_identity: string;
  revision: number;
  position_ms: number;
  snapshot_at_ms: number;
  saved_at_ms: number;
  ttl_ms: number;
};

export type ProgressAnchor = { position_ms: number; snapshot_at_ms: number };

export function trackIdentity(track: PlayerStateData["track"]): string {
  if (!track) return "";
  return String(track.id || track.entity_id || track.title || "").trim();
}

export function interpolatePosition(basePositionMs: number, snapshotAtMs: number, nowMs: number, durationMs: number): number {
  const anchor = snapshotAtMs > 0 ? snapshotAtMs : nowMs;
  const elapsed = Math.min(MAX_INTERPOLATION_MS, Math.max(0, nowMs - anchor));
  const position = Math.max(0, basePositionMs + elapsed);
  return durationMs > 0 ? Math.min(position, durationMs) : position;
}

export function isProgressBaselineValid(
  baseline: Partial<ProgressBaseline> | null | undefined,
  state: PlayerStateData,
  nowMs: number,
): baseline is ProgressBaseline {
  if (!baseline || typeof baseline.saved_at_ms !== "number" || typeof baseline.snapshot_at_ms !== "number" || typeof baseline.position_ms !== "number") return false;
  if (!Number.isFinite(baseline.position_ms) || !Number.isFinite(baseline.saved_at_ms) || !Number.isFinite(baseline.snapshot_at_ms)) return false;
  if (baseline.device_id !== state.device_id || baseline.server_instance_id !== state.server_instance_id) return false;
  if (baseline.play_session_id !== state.play_session_id || baseline.track_identity !== trackIdentity(state.track)) return false;
  if (baseline.revision !== state.revision || baseline.ttl_ms !== PROGRESS_BASELINE_TTL_MS) return false;
  if (baseline.saved_at_ms > nowMs + 5_000 || nowMs - baseline.saved_at_ms > baseline.ttl_ms) return false;
  return true;
}

export function selectProgressAnchor(state: PlayerStateData, baseline: Partial<ProgressBaseline> | null | undefined, nowMs: number): ProgressAnchor {
  const serverAnchor: ProgressAnchor = { position_ms: state.position_ms, snapshot_at_ms: state.snapshot_at_ms };
  if (!isProgressBaselineValid(baseline, state, nowMs)) return serverAnchor;
  const serverDisplayed = interpolatePosition(state.position_ms, state.snapshot_at_ms, nowMs, state.duration_ms);
  const cachedDisplayed = interpolatePosition(baseline.position_ms, baseline.snapshot_at_ms, nowMs, state.duration_ms);
  return cachedDisplayed > serverDisplayed + 750
    ? { position_ms: baseline.position_ms, snapshot_at_ms: baseline.snapshot_at_ms }
    : serverAnchor;
}

export function shouldClearProgressBaseline(previous: PlayerStateData | null, next: PlayerStateData): boolean {
  if (!previous) return next.transport_state !== "playing";
  return next.transport_state !== "playing" || previous.server_instance_id !== next.server_instance_id ||
    previous.play_session_id !== next.play_session_id || trackIdentity(previous.track) !== trackIdentity(next.track);
}

export function isPlayingState(transportState: TransportState): boolean {
  return transportState === "playing";
}
