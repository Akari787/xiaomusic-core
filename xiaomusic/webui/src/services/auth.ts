import { apiGet } from "./apiClient";

export interface OAuthStatus {
  success?: boolean;
  token_exists?: boolean;
  token_valid?: boolean;
  login_in_progress?: boolean;
  last_error?: string;
}

export async function fetchOAuthStatus(): Promise<OAuthStatus> {
  return await apiGet<OAuthStatus>("/api/oauth2/status");
}
