import { useEffect, useMemo, useState } from "react";

import { fetchDiagnostics, type DiagnosticsArea, type DiagnosticsStatus, type DiagnosticsView } from "../services/diagnostics";

const AREA_ORDER = [
  "startup",
  "auth",
  "sources",
  "devices",
  "playback_readiness",
] as const;

const AREA_LABELS: Record<(typeof AREA_ORDER)[number], string> = {
  startup: "Startup",
  auth: "Auth",
  sources: "Sources",
  devices: "Devices",
  playback_readiness: "Playback Readiness",
};

function statusColor(status: DiagnosticsStatus | undefined): string {
  switch (status) {
    case "ok":
      return "#1b7f3b";
    case "degraded":
      return "#a15c00";
    case "failed":
      return "#b00020";
    case "unknown":
    default:
      return "#666";
  }
}

function areaSummary(area: DiagnosticsArea | undefined) {
  return {
    status: String(area?.status || "unknown"),
    summary: String(area?.summary || ""),
    lastFailure: String(area?.last_failure || ""),
  };
}

export function DiagnosticsPage() {
  const [diagnostics, setDiagnostics] = useState<DiagnosticsView | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      try {
        const data = await fetchDiagnostics();
        if (!cancelled) {
          setDiagnostics(data);
          setError("");
        }
      } catch (e) {
        if (!cancelled) {
          setDiagnostics(null);
          setError(e instanceof Error ? e.message : "加载失败");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  const generatedAtText = useMemo(() => {
    const ts = Number(diagnostics?.generated_at_ms || 0);
    if (!ts) {
      return "";
    }
    try {
      return new Date(ts).toLocaleString();
    } catch {
      return "";
    }
  }, [diagnostics?.generated_at_ms]);

  return (
    <main style={{ maxWidth: 980, margin: "24px auto", padding: "0 16px", lineHeight: 1.5 }}>
      <div style={{ marginBottom: 8 }}>
        <a href="/webui/" style={{ color: "#666", fontSize: 13 }}>
          返回首页
        </a>
      </div>

      <h1>Startup / Self-check 诊断页</h1>
      <p>只展示统一诊断视图摘要，不展示 debug drill-down 细节。</p>

      <section style={{ border: "1px solid #ddd", borderRadius: 8, padding: 16, marginBottom: 16 }}>
        <h2>总体状态</h2>
        {loading ? <p>加载中...</p> : null}
        {error ? (
          <p style={{ color: "#b00020", fontWeight: 700 }}>加载失败：{error}</p>
        ) : null}
        {!loading && !error ? (
          <>
            <p>
              <strong>overall_status：</strong>
              <span style={{ color: statusColor(diagnostics?.overall_status), fontWeight: 700, marginLeft: 6 }}>
                {String(diagnostics?.overall_status || "unknown")}
              </span>
            </p>
            <p>
              <strong>summary：</strong> {String(diagnostics?.summary || "") || "-"}
            </p>
            <p>
              <strong>generated_at：</strong> {generatedAtText || "-"}
            </p>
          </>
        ) : null}
      </section>

      {AREA_ORDER.map((areaKey) => {
        const area = diagnostics?.areas?.[areaKey];
        const info = areaSummary(area);
        return (
          <details
            key={areaKey}
            open
            style={{ border: "1px solid #ddd", borderRadius: 8, padding: 16, marginBottom: 16 }}
          >
            <summary style={{ cursor: "pointer", fontWeight: 700 }}>
              {AREA_LABELS[areaKey]}
              <span style={{ color: statusColor(area?.status), marginLeft: 8 }}>
                [{info.status}]
              </span>
            </summary>
            <div style={{ marginTop: 12 }}>
              <p>
                <strong>status：</strong>
                <span style={{ color: statusColor(area?.status), fontWeight: 700, marginLeft: 6 }}>{info.status}</span>
              </p>
              <p>
                <strong>summary：</strong> {info.summary || "-"}
              </p>
              <p>
                <strong>last_failure：</strong> {info.lastFailure || "-"}
              </p>
            </div>
          </details>
        );
      })}
    </main>
  );
}
