import { apiGet, apiPost } from "./apiClient";

// Internal API only: auth/session endpoints are not part of the public v1 contract.

export type AuthStatusReason =
  | "healthy"
  | "persistent_auth_missing"
  | "short_session_missing"
  | "short_session_rebuild_failed"
  | "runtime_not_ready"
  | "manual_login_required";

export interface AuthStatus {
  success?: boolean;
  token_file?: string;
  auth_token_file?: string;
  token_exists?: boolean;
  token_valid?: boolean;
  cloud_available?: boolean;
  runtime_auth_ready?: boolean;
  persistent_auth_available?: boolean;
  short_session_available?: boolean;
  login_in_progress?: boolean;
  last_error?: string;
  auth_mode?: string;
  auth_locked?: boolean;
  auth_lock_until?: number | null;
  auth_lock_reason?: string;
  status_reason?: AuthStatusReason;
  status_reason_detail?: string;
  rebuild_failed?: boolean;
  rebuild_error_code?: string;
  rebuild_failed_reason?: string;
}

export async function fetchAuthStatus(): Promise<AuthStatus> {
  return await apiGet<AuthStatus>("/api/auth/status");
}

export async function reloadAuthRuntime(): Promise<Record<string, unknown>> {
  return await apiPost<Record<string, unknown>>("/api/auth/refresh", {});
}

export async function logoutAuth(): Promise<Record<string, unknown>> {
  return await apiPost<Record<string, unknown>>("/api/auth/logout", {});
}
