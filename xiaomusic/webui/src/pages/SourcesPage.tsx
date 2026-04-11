import { useEffect, useMemo, useState } from "react";

import { apiErrorText, isApiOk } from "../services/v1Api";
import {
  disableSource,
  enableSource,
  fetchSources,
  reloadSources,
  type SourceItem,
} from "../services/sources";

function statusColor(status: SourceItem["status"]): string {
  switch (status) {
    case "active":
      return "#1b7f3b";
    case "disabled":
      return "#a15c00";
    case "failed":
    default:
      return "#b00020";
  }
}

export function SourcesPage() {
  const [registryVersion, setRegistryVersion] = useState<number>(0);
  const [sources, setSources] = useState<SourceItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [reloading, setReloading] = useState(false);
  const [error, setError] = useState("");
  const [actionMessage, setActionMessage] = useState("");
  const [busyName, setBusyName] = useState("");

  async function loadSources(options?: { silent?: boolean }) {
    if (!options?.silent) {
      setLoading(true);
    }
    const out = await fetchSources();
    if (isApiOk(out)) {
      setRegistryVersion(Number(out.data?.registry_version || 0));
      setSources(Array.isArray(out.data?.sources) ? out.data.sources : []);
      setError("");
      return true;
    }
    setError(apiErrorText(out));
    return false;
  }

  useEffect(() => {
    void loadSources();
  }, []);

  async function onReload() {
    setReloading(true);
    setActionMessage("");
    try {
      const out = await reloadSources();
      if (!isApiOk(out)) {
        setError(apiErrorText(out));
        return;
      }
      await loadSources({ silent: true });
      setActionMessage(
        `reload 完成：registry_version=${Number(out.data?.registry_version || 0)}，loaded=${Number(out.data?.loaded_count || 0)}，failed=${Number(out.data?.failed_count || 0)}`,
      );
      setError("");
    } finally {
      setReloading(false);
      setLoading(false);
    }
  }

  async function onToggle(item: SourceItem) {
    setBusyName(item.name);
    setActionMessage("");
    try {
      const out = item.status === "disabled"
        ? await enableSource(item.name)
        : await disableSource(item.name);
      if (!isApiOk(out)) {
        setError(apiErrorText(out));
        return;
      }
      await loadSources({ silent: true });
      setError("");
      setActionMessage(`${item.name} 已${item.status === "disabled" ? "启用" : "禁用"}`);
    } finally {
      setBusyName("");
      setLoading(false);
    }
  }

  const summary = useMemo(() => {
    const active = sources.filter((item) => item.status === "active").length;
    const disabled = sources.filter((item) => item.status === "disabled").length;
    const failed = sources.filter((item) => item.status === "failed").length;
    return { active, disabled, failed };
  }, [sources]);

  return (
    <main style={{ maxWidth: 1080, margin: "24px auto", padding: "0 16px", lineHeight: 1.5 }}>
      <div style={{ marginBottom: 8 }}>
        <a href="/webui/" style={{ color: "#666", fontSize: 13 }}>
          返回首页
        </a>
      </div>

      <h1>Sources 管理页</h1>
      <p>只展示 source plugin 状态，并提供 reload / enable / disable 最小操作入口。</p>

      <section style={{ border: "1px solid #ddd", borderRadius: 8, padding: 16, marginBottom: 16 }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 16, alignItems: "center", flexWrap: "wrap" }}>
          <div>
            <p style={{ margin: 0 }}><strong>registry_version：</strong> {registryVersion || 0}</p>
            <p style={{ margin: "6px 0 0" }}>
              <strong>sources：</strong> active {summary.active} / disabled {summary.disabled} / failed {summary.failed}
            </p>
          </div>
          <button
            type="button"
            onClick={() => void onReload()}
            disabled={reloading}
            style={{ padding: "8px 14px", borderRadius: 6, border: "1px solid #ccc", cursor: reloading ? "not-allowed" : "pointer" }}
          >
            {reloading ? "Reloading..." : "Reload"}
          </button>
        </div>
        {loading ? <p style={{ marginTop: 12 }}>加载中...</p> : null}
        {error ? <p style={{ color: "#b00020", fontWeight: 700, marginTop: 12 }}>操作失败：{error}</p> : null}
        {actionMessage ? <p style={{ color: "#1b7f3b", fontWeight: 700, marginTop: 12 }}>{actionMessage}</p> : null}
      </section>

      <section style={{ border: "1px solid #ddd", borderRadius: 8, overflow: "hidden" }}>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ background: "#f7f7f7", textAlign: "left" }}>
              <th style={{ padding: 12, borderBottom: "1px solid #ddd" }}>Name</th>
              <th style={{ padding: 12, borderBottom: "1px solid #ddd" }}>Origin</th>
              <th style={{ padding: 12, borderBottom: "1px solid #ddd" }}>Status</th>
              <th style={{ padding: 12, borderBottom: "1px solid #ddd" }}>Version</th>
              <th style={{ padding: 12, borderBottom: "1px solid #ddd" }}>Error</th>
              <th style={{ padding: 12, borderBottom: "1px solid #ddd" }}>Action</th>
            </tr>
          </thead>
          <tbody>
            {sources.length === 0 && !loading ? (
              <tr>
                <td colSpan={6} style={{ padding: 16, color: "#666" }}>暂无 source plugin</td>
              </tr>
            ) : null}
            {sources.map((item) => {
              const isBusy = busyName === item.name;
              const isDisabled = item.status === "disabled";
              return (
                <tr key={item.name}>
                  <td style={{ padding: 12, borderBottom: "1px solid #eee", fontWeight: 700 }}>{item.name}</td>
                  <td style={{ padding: 12, borderBottom: "1px solid #eee" }}>{item.origin}</td>
                  <td style={{ padding: 12, borderBottom: "1px solid #eee", color: statusColor(item.status), fontWeight: 700 }}>
                    {item.status}
                  </td>
                  <td style={{ padding: 12, borderBottom: "1px solid #eee" }}>{item.version || "-"}</td>
                  <td style={{ padding: 12, borderBottom: "1px solid #eee", color: item.error ? "#b00020" : "#666" }}>
                    {item.error || "-"}
                  </td>
                  <td style={{ padding: 12, borderBottom: "1px solid #eee" }}>
                    <button
                      type="button"
                      onClick={() => void onToggle(item)}
                      disabled={isBusy}
                      style={{ padding: "6px 12px", borderRadius: 6, border: "1px solid #ccc", cursor: isBusy ? "not-allowed" : "pointer" }}
                    >
                      {isBusy ? "处理中..." : isDisabled ? "Enable" : "Disable"}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>
    </main>
  );
}
