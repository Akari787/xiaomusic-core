import { OAuthStatusCard } from "../components/OAuthStatusCard";

export function HomePage() {
  return (
    <main style={{ maxWidth: 800, margin: "40px auto", fontFamily: "sans-serif" }}>
      <h1>XiaoMusic WebUI（重构骨架）</h1>
      <p>当前为 Vite + React + TypeScript 前后端分离骨架。</p>
      <OAuthStatusCard />
    </main>
  );
}
