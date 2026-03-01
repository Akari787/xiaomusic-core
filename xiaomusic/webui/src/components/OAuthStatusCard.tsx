import { useEffect, useState } from "react";

import { fetchOAuthStatus, type OAuthStatus } from "../services/auth";

export function OAuthStatusCard() {
  const [status, setStatus] = useState<OAuthStatus | null>(null);
  const [error, setError] = useState<string>("");

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const data = await fetchOAuthStatus();
        if (!cancelled) {
          setStatus(data);
          setError("");
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "加载失败");
        }
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <section className="oauth-card">
      <h2>OAuth2 状态</h2>
      {error ? <p className="oauth-error">请求失败：{error}</p> : null}
      {status ? (
        <ul>
          <li>token_exists: {String(status.token_exists)}</li>
          <li>token_valid: {String(status.token_valid)}</li>
          <li>login_in_progress: {String(status.login_in_progress)}</li>
          <li>last_error: {status.last_error || ""}</li>
        </ul>
      ) : (
        <p className="oauth-loading">加载中...</p>
      )}
    </section>
  );
}
