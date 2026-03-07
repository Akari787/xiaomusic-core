import { useEffect, useState } from "react";

import { HomePage } from "./pages/HomePage";
import { ApiV1DebugPage } from "./pages/ApiV1DebugPage";

type RouteKey = "home" | "debug-api-v1";

function getRouteFromLocation(): RouteKey {
  const pathname = window.location.pathname.replace(/\/+$/, "");
  if (pathname.endsWith("/debug/api-v1")) {
    return "debug-api-v1";
  }
  const hash = window.location.hash || "";
  if (hash === "#/debug/api-v1" || hash === "#debug/api-v1") {
    return "debug-api-v1";
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
        }}
      >
        <a href="/webui/#/debug/api-v1" style={{ color: "#666" }}>
          API v1 调试页
        </a>
      </div>
    </>
  );
}
