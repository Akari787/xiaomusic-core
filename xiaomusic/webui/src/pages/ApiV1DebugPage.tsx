import { useEffect, useMemo, useState } from "react";

import { AuthStatusCard } from "../components/AuthStatusCard";
import {
  SOURCE_HINT_OPTIONS,
  apiErrorText,
  getDevices,
  getSystemStatus,
  isApiOk,
  mapPluginName,
  pause,
  play,
  probe,
  resolve,
  resume,
  setVolume,
  stop,
  tts,
  type ApiEnvelope,
  type ControlData,
  type DeviceRow,
  type PlayData,
  type ResolveData,
  type SourceHint,
  type SystemStatusData,
} from "../services/v1Api";

function detailsText(title: string, out: ApiEnvelope<unknown>): string {
  const rid = out.request_id ? `request_id=${out.request_id}` : "request_id=unknown";
  if (out.code === 0) {
    return `${title}成功（${rid}）`;
  }
  return `${title}失败：${apiErrorText(out)}（${rid}）`;
}

export function ApiV1DebugPage() {
  const [devices, setDevices] = useState<DeviceRow[]>([]);
  const [activeDeviceId, setActiveDeviceId] = useState("");
  const [systemStatus, setSystemStatus] = useState<SystemStatusData | null>(null);

  const [query, setQuery] = useState("");
  const [sourceHint, setSourceHint] = useState<SourceHint>("auto");
  const [ttsText, setTtsText] = useState("XiaoMusic API v1 测试");
  const [volume, setVolumeValue] = useState(30);

  const [resolveResult, setResolveResult] = useState<ApiEnvelope<ResolveData> | null>(null);
  const [playResult, setPlayResult] = useState<ApiEnvelope<PlayData> | null>(null);
  const [controlResult, setControlResult] = useState<ApiEnvelope<ControlData> | null>(null);
  const [message, setMessage] = useState("");

  const activeDevice = useMemo(() => devices.find((d) => d.device_id === activeDeviceId) ?? null, [devices, activeDeviceId]);

  useEffect(() => {
    void (async () => {
      const [devicesOut, statusOut] = await Promise.all([getDevices(), getSystemStatus()]);
      setSystemStatus(statusOut.code === 0 ? statusOut.data : null);
      if (devicesOut.code === 0) {
        const rows = devicesOut.data.devices || [];
        setDevices(rows);
        if (rows.length > 0) {
          setActiveDeviceId(rows[0].device_id);
        }
      } else {
        setMessage(detailsText("加载设备", devicesOut));
      }
    })();
  }, []);

  async function runResolveOnly() {
    if (!query.trim()) {
      setMessage("请输入 query");
      return;
    }
    const out = await resolve({ query: query.trim(), source_hint: sourceHint, options: {} });
    setResolveResult(out);
    setMessage(detailsText("仅解析", out));
  }

  async function runResolveAndPlay() {
    if (!activeDeviceId) {
      setMessage("请先选择设备");
      return;
    }
    if (!query.trim()) {
      setMessage("请输入 query");
      return;
    }
    const out = await play({
      device_id: activeDeviceId,
      query: query.trim(),
      source_hint: sourceHint,
      options: {},
    });
    setPlayResult(out);
    setMessage(detailsText("解析并播放", out));
  }

  async function runControl(action: "stop" | "pause" | "resume" | "probe") {
    if (!activeDeviceId) {
      setMessage("请先选择设备");
      return;
    }
    const out =
      action === "stop"
        ? await stop(activeDeviceId)
        : action === "pause"
          ? await pause(activeDeviceId)
          : action === "resume"
            ? await resume(activeDeviceId)
            : await probe(activeDeviceId);
    setControlResult(out);
    setMessage(detailsText(`控制-${action}`, out));
  }

  async function runTts() {
    if (!activeDeviceId) {
      setMessage("请先选择设备");
      return;
    }
    const out = await tts(activeDeviceId, ttsText);
    setControlResult(out);
    setMessage(detailsText("TTS", out));
  }

  async function runSetVolume() {
    if (!activeDeviceId) {
      setMessage("请先选择设备");
      return;
    }
    const out = await setVolume(activeDeviceId, volume);
    setControlResult(out);
    setMessage(detailsText("设置音量", out));
  }

  return (
    <main style={{ maxWidth: 980, margin: "24px auto", padding: "0 16px", lineHeight: 1.5 }}>
      <div style={{ marginBottom: 8 }}>
        <a href="/webui/" style={{ color: "#666", fontSize: 13 }}>
          返回首页
        </a>
      </div>
      <h1>XiaoMusic Runtime / API v1 调试页</h1>
      <p>统一链路：先 resolve（可选），再 play。WebUI 仅调用正式 /api/v1 白名单接口。</p>

      <section style={{ border: "1px solid #ddd", borderRadius: 8, padding: 16, marginBottom: 16 }}>
        <h2>认证状态</h2>
        <AuthStatusCard />
      </section>

      <section style={{ border: "1px solid #ddd", borderRadius: 8, padding: 16, marginBottom: 16 }}>
        <h2>系统与设备</h2>
        <div>
          <strong>系统状态：</strong>
          {systemStatus ? `${systemStatus.status} / version=${systemStatus.version} / devices=${systemStatus.devices_count}` : "加载中..."}
        </div>
        <label htmlFor="device-select">设备选择：</label>
        <select
          id="device-select"
          data-testid="device-select"
          value={activeDeviceId}
          onChange={(e) => setActiveDeviceId(e.target.value)}
          style={{ marginLeft: 8, minWidth: 320 }}
        >
          {devices.map((d) => (
            <option key={d.device_id} value={d.device_id}>
              {d.name || d.device_id} ({d.model || "unknown"})
            </option>
          ))}
        </select>
        <div style={{ marginTop: 8 }}>
          当前设备：{activeDevice?.device_id || "-"} / 在线：{String(Boolean(activeDevice?.online))}
        </div>
      </section>

      <section style={{ border: "1px solid #ddd", borderRadius: 8, padding: 16, marginBottom: 16 }}>
        <h2>测试播放入口（统一）</h2>

        <div style={{ display: "grid", gridTemplateColumns: "120px 1fr", gap: 8, alignItems: "center" }}>
          <label htmlFor="query-input">query：</label>
          <input
            id="query-input"
            data-testid="query-input"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="输入直链 URL、YouTube/Bilibili 链接、Jellyfin 输入或本地媒体库关键字"
          />

          <label htmlFor="source-hint">来源模式：</label>
          <select
            id="source-hint"
            data-testid="source-hint-select"
            value={sourceHint}
            onChange={(e) => setSourceHint(e.target.value as SourceHint)}
          >
            {SOURCE_HINT_OPTIONS.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </div>

        <div style={{ marginTop: 12, display: "flex", gap: 8 }}>
          <button data-testid="resolve-button" onClick={() => void runResolveOnly()}>
            仅解析
          </button>
          <button data-testid="play-button" onClick={() => void runResolveAndPlay()}>
            解析并播放
          </button>
        </div>
      </section>

      <section style={{ border: "1px solid #ddd", borderRadius: 8, padding: 16, marginBottom: 16 }}>
        <h2>控制动作</h2>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button data-testid="stop-button" onClick={() => void runControl("stop")}>
            stop
          </button>
          <button data-testid="pause-button" onClick={() => void runControl("pause")}>
            pause
          </button>
          <button data-testid="resume-button" onClick={() => void runControl("resume")}>
            resume
          </button>
          <button data-testid="probe-button" onClick={() => void runControl("probe")}>
            probe
          </button>
        </div>

        <div style={{ marginTop: 12, display: "grid", gap: 8 }}>
          <div>
            <label htmlFor="tts-input">TTS 文本：</label>
            <input
              id="tts-input"
              data-testid="tts-input"
              value={ttsText}
              onChange={(e) => setTtsText(e.target.value)}
              style={{ marginLeft: 8, minWidth: 320 }}
            />
            <button data-testid="tts-button" style={{ marginLeft: 8 }} onClick={() => void runTts()}>
              tts
            </button>
          </div>
          <div>
            <label htmlFor="volume-input">音量：</label>
            <input
              id="volume-input"
              data-testid="volume-input"
              type="number"
              min={0}
              max={100}
              value={volume}
              onChange={(e) => setVolumeValue(Number(e.target.value || 0))}
              style={{ marginLeft: 8, width: 80 }}
            />
            <button data-testid="volume-button" style={{ marginLeft: 8 }} onClick={() => void runSetVolume()}>
              set volume
            </button>
          </div>
        </div>
      </section>

      <section style={{ border: "1px solid #ddd", borderRadius: 8, padding: 16, marginBottom: 16 }}>
        <h2>统一消息</h2>
        <div data-testid="global-message">{message || "-"}</div>
      </section>

      <section style={{ border: "1px solid #ddd", borderRadius: 8, padding: 16, marginBottom: 16 }}>
        <h2>仅解析结果</h2>
        <div data-testid="resolve-request-id">request_id: {resolveResult?.request_id || "-"}</div>
        <div data-testid="resolve-source-plugin">source_plugin: {mapPluginName(resolveResult?.data?.source_plugin)}</div>
        <div data-testid="resolve-success">resolved: {String(Boolean(resolveResult?.data?.resolved && isApiOk(resolveResult)))}</div>
        <div data-testid="resolve-title">title: {resolveResult?.data?.media?.title || "-"}</div>
        <div data-testid="resolve-duration">duration: {String(resolveResult?.data?.media?.duration_seconds ?? "-")}</div>
        <div data-testid="resolve-error">error: {resolveResult && resolveResult.code !== 0 ? apiErrorText(resolveResult) : "-"}</div>
      </section>

      <section style={{ border: "1px solid #ddd", borderRadius: 8, padding: 16 }}>
        <h2>解析并播放结果</h2>
        <div data-testid="play-request-id">request_id: {playResult?.request_id || "-"}</div>
        <div data-testid="play-source-plugin">source_plugin: {mapPluginName(playResult?.data?.source_plugin)}</div>
        <div data-testid="play-transport">transport: {playResult?.data?.transport || "-"}</div>
        <div data-testid="play-device-id">device_id: {playResult?.data?.device_id || "-"}</div>
        <div data-testid="play-status">status: {playResult?.data?.status || "-"}</div>
        <div data-testid="play-message">message: {playResult ? (playResult.code === 0 ? "ok" : apiErrorText(playResult)) : "-"}</div>
        <div data-testid="play-stage">stage: {playResult?.data?.stage || "-"}</div>
      </section>

      <section style={{ marginTop: 16, color: "#444" }}>
        <small>
          来源模式：自动识别 / 直链媒体 / 网站媒体 / Jellyfin / 本地媒体库。
          调试插件名仅在结果区展示：DirectUrlSourcePlugin / SiteMediaSourcePlugin / JellyfinSourcePlugin /
          LocalLibrarySourcePlugin。
        </small>
      </section>
    </main>
  );
}
