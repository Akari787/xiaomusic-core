import { apiGet } from "./apiClient";

export type DiagnosticsStatus = "ok" | "degraded" | "failed" | "unknown";

export interface DiagnosticsAreaData {
  [key: string]: unknown;
}

export interface DiagnosticsArea {
  status?: DiagnosticsStatus;
  summary?: string;
  last_failure?: string;
  data?: DiagnosticsAreaData;
}

export interface DiagnosticsView {
  generated_at_ms?: number;
  overall_status?: DiagnosticsStatus;
  summary?: string;
  areas?: {
    startup?: DiagnosticsArea;
    auth?: DiagnosticsArea;
    sources?: DiagnosticsArea;
    devices?: DiagnosticsArea;
    playback_readiness?: DiagnosticsArea;
    [key: string]: DiagnosticsArea | undefined;
  };
}

export async function fetchDiagnostics(): Promise<DiagnosticsView> {
  return await apiGet<DiagnosticsView>("/diagnostics") as DiagnosticsView;
}
