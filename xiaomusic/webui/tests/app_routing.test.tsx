// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

vi.mock("../src/pages/HomePage", () => ({
  HomePage: () => <div data-testid="home-marker">HOME_PAGE</div>,
}));

vi.mock("../src/pages/ApiV1DebugPage", () => ({
  ApiV1DebugPage: () => <div data-testid="debug-marker">DEBUG_PAGE</div>,
}));

import { App } from "../src/App";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("App routing", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    window.location.hash = "";
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    window.location.hash = "";
  });

  it("renders home by default and shows low-priority debug entry", async () => {
    await act(async () => {
      root.render(<App />);
    });
    expect(container.querySelector('[data-testid="home-marker"]')).not.toBeNull();
    const link = container.querySelector('a[href="/webui/#/debug/api-v1"]') as HTMLAnchorElement | null;
    expect(link).not.toBeNull();
  });

  it("renders debug page on hash route", async () => {
    window.location.hash = "#/debug/api-v1";
    await act(async () => {
      root.render(<App />);
    });
    expect(container.querySelector('[data-testid="debug-marker"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="home-marker"]')).toBeNull();
  });
});
