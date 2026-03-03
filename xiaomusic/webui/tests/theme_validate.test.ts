import { describe, expect, it } from "vitest";

import { validateCustomCss, validateThemePackageText } from "../src/theme/theme";

describe("validateCustomCss", () => {
  it("rejects @import", () => {
    const out = validateCustomCss('@import url("https://evil.test/a.css"); body{color:red;}');
    expect(out.ok).toBe(false);
  });

  it("rejects overly long css", () => {
    const longCss = `body{color:#000;}\n${"a".repeat(50001)}`;
    const out = validateCustomCss(longCss);
    expect(out.ok).toBe(false);
  });

  it("accepts normal css", () => {
    const out = validateCustomCss("body { color: #111; } .player { border-radius: 12px; }");
    expect(out.ok).toBe(true);
  });
});

describe("validateThemePackageText", () => {
  it("rejects invalid json package", () => {
    const out = validateThemePackageText("body{color:red}");
    expect(out.ok).toBe(false);
  });

  it("rejects package with missing name", () => {
    const out = validateThemePackageText(JSON.stringify({ css: "body{color:red}" }));
    expect(out.ok).toBe(false);
  });

  it("accepts valid theme package", () => {
    const out = validateThemePackageText(JSON.stringify({ name: "Neon", css: "body{color:#0ff}" }));
    expect(out.ok).toBe(true);
    expect(out.name).toBe("Neon");
  });

  it("accepts soundscape layout package", () => {
    const out = validateThemePackageText(
      JSON.stringify({ name: "SoundScape 1:1", layout: "soundscape", css: "body{background:#fff}" }),
    );
    expect(out.ok).toBe(true);
    expect(out.layout).toBe("soundscape");
  });

  it("rejects unsupported layout", () => {
    const out = validateThemePackageText(
      JSON.stringify({ name: "Broken", layout: "unknown-layout", css: "body{background:#fff}" }),
    );
    expect(out.ok).toBe(false);
  });
});
