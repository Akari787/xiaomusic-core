export type PlayerStateFailure = "device_missing" | "auth_required" | "transient";

export function classifyPlayerStateFailure(status: number, envelope?: unknown): PlayerStateFailure {
  if (status === 404 || hasDeviceMissingCode(envelope)) {
    return "device_missing";
  }
  if (status === 401 || status === 403) {
    return "auth_required";
  }
  return "transient";
}

function hasDeviceMissingCode(envelope: unknown): boolean {
  if (!envelope || typeof envelope !== "object") {
    return false;
  }
  const value = envelope as { code?: unknown; error_code?: unknown; data?: { error_code?: unknown } };
  return Number(value.code) === 40004 || String(value.error_code || value.data?.error_code || "") === "E_DEVICE_NOT_FOUND";
}
