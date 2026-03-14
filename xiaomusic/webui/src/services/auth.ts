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

export interface AuthStatus extends OAuthStatus {}

export async function fetchAuthStatus(): Promise<AuthStatus> {
  return await apiGet<AuthStatus>("/api/auth/status");
}

export async function refreshAuthRuntime(): Promise<Record<string, unknown>> {
  return await apiPost<Record<string, unknown>>("/api/auth/refresh", {});
}

// Backward-compatible aliases.
export const fetchOAuthStatus = fetchAuthStatus;
export const refreshOAuthRuntime = refreshAuthRuntime;
export const refreshOAuthToken = refreshAuthRuntime;

export async function logoutAuth(): Promise<Record<string, unknown>> {
  return await apiPost<Record<string, unknown>>("/api/auth/logout", {});
}

export const logoutOAuth = logoutAuth;
