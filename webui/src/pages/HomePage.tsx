import { OAuthStatusCard } from "../components/OAuthStatusCard";
import { defaultTheme } from "../theme";

export function HomePage() {
  const t = defaultTheme.tokens;

  return (
    <main
      style={{
        maxWidth: 860,
        margin: "40px auto",
        fontFamily: '"Segoe UI", "PingFang SC", sans-serif',
        color: t.color.text,
        background: t.color.bg,
        border: `1px solid ${t.color.border}`,
        borderRadius: t.radius.md,
        padding: t.spacing.lg,
      }}
    >
      <h1 style={{ marginTop: 0 }}>XiaoMusic WebUI（默认主题）</h1>
      <p style={{ color: t.color.muted }}>
        当前为 Vite + React + TypeScript 前后端分离骨架。多主题暂不支持。
      </p>
      <OAuthStatusCard />
    </main>
  );
}
