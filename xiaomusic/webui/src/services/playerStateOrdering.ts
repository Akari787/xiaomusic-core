import type { PlayerStateData } from "./v1Api";

export type AppliedSnapshotMeta = {
  serverInstanceId: string;
  revision: number;
  snapshotAtMs: number;
};

export function shouldApplyPlayerSnapshot(
  previous: AppliedSnapshotMeta | null,
  snapshot: Pick<PlayerStateData, "server_instance_id" | "revision" | "snapshot_at_ms">,
): boolean {
  if (!previous || snapshot.server_instance_id !== previous.serverInstanceId) {
    return true;
  }
  if (snapshot.revision > previous.revision) {
    return true;
  }
  if (snapshot.revision < previous.revision) {
    return false;
  }
  return snapshot.snapshot_at_ms > previous.snapshotAtMs;
}

export function snapshotMeta(snapshot: Pick<PlayerStateData, "server_instance_id" | "revision" | "snapshot_at_ms">): AppliedSnapshotMeta {
  return {
    serverInstanceId: snapshot.server_instance_id,
    revision: snapshot.revision,
    snapshotAtMs: snapshot.snapshot_at_ms,
  };
}
