import { useEffect, useState } from "react";

import { HomePage } from "./pages/HomePage";
import { ApiV1DebugPage } from "./pages/ApiV1DebugPage";
import { DiagnosticsPage } from "./pages/DiagnosticsPage";
import { SourcesPage } from "./pages/SourcesPage";

type RouteKey = "home" | "debug-api-v1" | "diagnostics" | "sources";

function getRouteFromLocation(): RouteKey {
  const pathname = window.location.pathname.replace(/\/+$/, "");
  if (pathname.endsWith("/debug/api-v1")) {
    return "debug-api-v1";
  }
  if (pathname.endsWith("/diagnostics")) {
    return "diagnostics";
  }
  if (pathname.endsWith("/sources")) {
    return "sources";
  }
  const hash = window.location.hash || "";
  if (hash === "#/debug/api-v1" || hash === "#debug/api-v1") {
    return "debug-api-v1";
  }
  if (hash === "#/diagnostics" || hash === "#diagnostics") {
    return "diagnostics";
  }
  if (hash === "#/sources" || hash === "#sources") {
    return "sources";
  }
  return "home";
}

export function App() {
  const [route, setRoute] = useState<RouteKey>(() => getRouteFromLocation());

  useEffect(() => {
    const onHashChange = () => setRoute(getRouteFromLocation());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  if (route === "debug-api-v1") {
    return <ApiV1DebugPage />;
  }

  if (route === "diagnostics") {
    return <DiagnosticsPage />;
  }

  if (route === "sources") {
    return <SourcesPage />;
  }

  return (
    <>
      <HomePage />
      <div
        style={{
          position: "fixed",
          right: 12,
          bottom: 10,
          fontSize: 12,
          opacity: 0.8,
          zIndex: 20,
          display: "flex",
          gap: 12,
        }}
      >
        <a href="/webui/#/sources" style={{ color: "#666" }}>
          Sources 页
        </a>
        <a href="/webui/#/diagnostics" style={{ color: "#666" }}>
          诊断页
        </a>
        <a href="/webui/#/debug/api-v1" style={{ color: "#666" }}>
          API v1 调试页
        </a>
      </div>
    </>
  );
}
