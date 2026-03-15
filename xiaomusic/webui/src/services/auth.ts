import { apiGet, apiPost } from "./apiClient";

export interface AuthStatus {
  success?: boolean;
  token_file?: string;
  auth_token_file?: string;
  token_exists?: boolean;
  token_valid?: boolean;
  cloud_available?: boolean;
  runtime_auth_ready?: boolean;
  login_in_progress?: boolean;
  last_error?: string;
  auth_mode?: string;
  auth_locked?: boolean;
  auth_lock_until?: number | null;
  auth_lock_reason?: string;
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
