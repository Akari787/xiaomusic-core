import { apiGet, apiPost } from "./apiClient";

export interface OAuthStatus {
  success?: boolean;
  token_file?: string;
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

export async function fetchOAuthStatus(): Promise<OAuthStatus> {
  return await apiGet<OAuthStatus>("/api/oauth2/status");
}

export async function refreshOAuthRuntime(): Promise<Record<string, unknown>> {
  return await apiPost<Record<string, unknown>>("/api/oauth2/refresh", {});
}

// Backward-compatible alias.
export const refreshOAuthToken = refreshOAuthRuntime;

export async function logoutOAuth(): Promise<Record<string, unknown>> {
  return await apiPost<Record<string, unknown>>("/api/oauth2/logout", {});
}
