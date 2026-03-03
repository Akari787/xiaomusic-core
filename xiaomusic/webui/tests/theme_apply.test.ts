// @vitest-environment jsdom
import { describe, expect, it } from "vitest";

import { applyCustomCss, removeCustomCss } from "../src/theme/theme";

describe("custom theme style tag", () => {
  it("creates and updates style#custom-theme-style", () => {
    applyCustomCss("body{color:red;}");
    let el = document.getElementById("custom-theme-style") as HTMLStyleElement | null;
    expect(el).not.toBeNull();
    expect(el?.textContent).toContain("color:red");

    applyCustomCss("body{color:blue;}");
    el = document.getElementById("custom-theme-style") as HTMLStyleElement | null;
    expect(el).not.toBeNull();
    expect(el?.textContent).toContain("color:blue");
  });

  it("removes injected custom style", () => {
    applyCustomCss("body{color:green;}");
    removeCustomCss();
    const el = document.getElementById("custom-theme-style");
    expect(el).toBeNull();
  });
});
