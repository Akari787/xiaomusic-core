import React from "react";
import ReactDOM from "react-dom/client";

import "../static/default/main.css";
import "../static/default/setting.css";
import { App } from "./App";
import { ThemeProvider } from "./theme/ThemeProvider";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ThemeProvider>
      <App />
    </ThemeProvider>
  </React.StrictMode>,
);
