import { useEffect, useState } from "react";

import {
  fetchAuthStatus,
  logoutAuth,
  reloadAuthRuntime,
  type AuthStatus,
} from "../services/auth";

export function AuthStatusCard() {
  const [status, setStatus] = useState<AuthStatus | null>(null);
  const [error, setError] = useState<string>("");
  const [actionMessage, setActionMessage] = useState<string>("");
  const [refreshing, setRefreshing] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);

  async function loadStatus() {
    try {
      const data = await fetchAuthStatus();
      setStatus(data);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    }
  }

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const data = await fetchAuthStatus();
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

  async function onRefreshRuntime() {
    setRefreshing(true);
    setActionMessage("");
    try {
      const ret = await reloadAuthRuntime();
      const refreshed = Boolean(ret?.runtime_auth_ready);
      const lastError = String((ret?.last_error as string | undefined) || "");
      setActionMessage(refreshed ? "运行时刷新成功" : `运行时刷新失败${lastError ? `: ${lastError}` : ""}`);
      await loadStatus();
    } catch (e) {
      setActionMessage(`运行时刷新失败: ${e instanceof Error ? e.message : "unknown error"}`);
      await loadStatus();
    } finally {
      setRefreshing(false);
    }
  }

  async function onLogout() {
    setLoggingOut(true);
    setActionMessage("");
    try {
      const ret = await logoutAuth();
      const removed = Boolean(ret?.removed);
      setActionMessage(removed ? "已退出登录" : "退出完成（未检测到 token 文件）");
      await loadStatus();
    } catch (e) {
      setActionMessage(`退出失败: ${e instanceof Error ? e.message : "unknown error"}`);
      await loadStatus();
    } finally {
      setLoggingOut(false);
    }
  }

  const authLocked = Boolean(status?.auth_locked);
  const runtimeReady = Boolean(status?.runtime_auth_ready);
  const tokenPresent = Boolean(status?.token_valid || status?.token_exists);
  const authStateText = runtimeReady
    ? "运行时已恢复"
    : tokenPresent
      ? "已登录待恢复（请点击刷新运行时）"
      : "需要重新扫码登录";

  return (
    <section className="auth-card">
      <h2>认证状态</h2>
      {error ? <p className="auth-error">请求失败：{error}</p> : null}
      {actionMessage ? <p>{actionMessage}</p> : null}
      {authLocked ? (
        <p className="auth-error" style={{ color: "#b00020", fontWeight: 700 }}>
          ⚠️ 认证已锁定：{status?.auth_lock_reason || "unknown"}
        </p>
      ) : null}
      {status ? (
        <ul>
          <li>auth_state: {authStateText}</li>
          <li>auth_mode: {String(status.auth_mode || "")}</li>
          <li>auth_locked: {String(status.auth_locked)}</li>
          <li>runtime_auth_ready: {String(status.runtime_auth_ready)}</li>
          <li>token_exists: {String(status.token_exists)}</li>
          <li>token_valid: {String(status.token_valid)}</li>
          <li>cloud_available: {String(status.cloud_available)}</li>
          <li>login_in_progress: {String(status.login_in_progress)}</li>
          <li>auth_lock_until: {String(status.auth_lock_until ?? "")}</li>
          <li>auth_lock_reason: {status.auth_lock_reason || ""}</li>
          <li>last_error: {status.last_error || ""}</li>
        </ul>
      ) : (
        <p className="auth-loading">加载中...</p>
      )}
      <div style={{ display: "flex", gap: 8 }}>
        <button type="button" onClick={onRefreshRuntime} disabled={refreshing || loggingOut}>
          {refreshing ? "刷新中..." : "刷新运行时"}
        </button>
        <button type="button" onClick={onLogout} disabled={loggingOut || refreshing}>
          {loggingOut ? "退出中..." : "退出登录"}
        </button>
      </div>
    </section>
  );
}
