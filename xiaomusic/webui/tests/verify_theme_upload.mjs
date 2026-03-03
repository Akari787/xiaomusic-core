import fs from "node:fs";
import path from "node:path";
import { chromium } from "playwright";

const baseUrl = process.env.WEBUI_URL || "http://127.0.0.1:58090/webui/";
const themeName = process.env.THEME_NAME || "SoundScape Classic";
const themeFile = process.env.THEME_FILE || path.resolve("tests/fixtures/soundscape-original.xmtheme");
const themeLayout = process.env.THEME_LAYOUT || "classic";
const screenshotFile = path.resolve("tests/artifacts/theme-upload-soundscape.png");

if (!fs.existsSync(themeFile)) {
  console.error(`[FAIL] Theme package not found: ${themeFile}`);
  process.exit(1);
}

fs.mkdirSync(path.dirname(screenshotFile), { recursive: true });

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
const page = await context.newPage();

async function openSettingsPanel() {
  if (await page.locator("#settings-component").isVisible().catch(() => false)) {
    return;
  }
  const classicEntry = page.locator('div.icon-item:has-text("设置")').first();
  if (await classicEntry.count()) {
    await classicEntry.click({ timeout: 15000 });
  } else {
    await page.locator('button.soundscape-settings-entry:has-text("设置")').first().click({ timeout: 15000 });
  }
  await page.locator("#settings-component").waitFor({ state: "visible", timeout: 15000 });
}

try {
  await page.goto(baseUrl, { waitUntil: "networkidle", timeout: 60000 });

  await openSettingsPanel();

  const fileInput = page.locator('#settings-component input[type="file"]');
  await fileInput.setInputFiles(themeFile);

  await page.waitForFunction(
    (name) => {
      const select = document.querySelector("#theme-mode-select");
      if (!(select instanceof HTMLSelectElement)) return false;
      return Array.from(select.options).some((opt) => (opt.textContent || "").trim() === name);
    },
    themeName,
    { timeout: 15000 },
  );

  const initialResult = await page.evaluate(({ name, expectedLayout }) => {
    const select = document.querySelector("#theme-mode-select");
    const styleEl = document.getElementById("custom-theme-style");
    const player = document.querySelector(".player");
    const soundscapeRoot = document.querySelector(".soundscape-app");
    const soundscapeSidebarItems = document.querySelectorAll(".soundscape-playlist-item").length;
    const soundscapeTable = document.querySelector(".soundscape-table");
    const validationHint = Array.from(document.querySelectorAll("#settings-component .oauth-hint"))
      .map((el) => (el.textContent || "").trim())
      .find((txt) => txt.includes("主题") && (txt.includes("失败") || txt.includes("错误") || txt.includes("仅支持") || txt.includes("缺少")));

    if (!(select instanceof HTMLSelectElement)) {
      return { ok: false, reason: "theme-select-not-found" };
    }

    const selectedText = (select.options[select.selectedIndex]?.textContent || "").trim();
    const hasThemeOption = Array.from(select.options).some((opt) => (opt.textContent || "").trim() === name);
    const hasInjectedCss = Boolean(styleEl && (styleEl.textContent || "").trim().length > 0);
    const playerStyle = player ? getComputedStyle(player) : null;
    const gridTemplateColumns = playerStyle?.gridTemplateColumns || "";
    const layoutGrid = playerStyle?.display === "grid" && /\d/.test(gridTemplateColumns) && gridTemplateColumns.includes(" ");
    const layoutOk = expectedLayout === "soundscape"
      ? Boolean(soundscapeRoot && soundscapeTable && soundscapeSidebarItems > 0)
      : layoutGrid;

    return {
      ok: hasThemeOption && selectedText === name && hasInjectedCss && !validationHint && layoutOk,
      hasThemeOption,
      selectedText,
      hasInjectedCss,
      layoutGrid,
      layoutOk,
      expectedLayout,
      soundscapeSidebarItems,
      gridTemplateColumns,
      validationHint: validationHint || "",
      optionCount: select.options.length,
      activeThemeId: select.value,
    };
  }, { name: themeName, expectedLayout: themeLayout });

  await page.screenshot({ path: screenshotFile, fullPage: true });

  if (!initialResult.ok) {
    console.error("[FAIL] Third-party theme upload validation failed");
    console.error(JSON.stringify(initialResult, null, 2));
    process.exit(2);
  }

  await page.reload({ waitUntil: "networkidle", timeout: 60000 });
  await openSettingsPanel();

  const persistedResult = await page.evaluate(({ name, expectedLayout }) => {
    const select = document.querySelector("#theme-mode-select");
    const styleEl = document.getElementById("custom-theme-style");
    const player = document.querySelector(".player");
    const soundscapeRoot = document.querySelector(".soundscape-app");
    const soundscapeSidebarItems = document.querySelectorAll(".soundscape-playlist-item").length;
    const soundscapeTable = document.querySelector(".soundscape-table");
    if (!(select instanceof HTMLSelectElement)) {
      return { ok: false, reason: "theme-select-not-found-after-reload" };
    }
    const selectedText = (select.options[select.selectedIndex]?.textContent || "").trim();
    const hasThemeOption = Array.from(select.options).some((opt) => (opt.textContent || "").trim() === name);
    const hasInjectedCss = Boolean(styleEl && (styleEl.textContent || "").trim().length > 0);
    const playerStyle = player ? getComputedStyle(player) : null;
    const gridTemplateColumns = playerStyle?.gridTemplateColumns || "";
    const layoutGrid = playerStyle?.display === "grid" && /\d/.test(gridTemplateColumns) && gridTemplateColumns.includes(" ");
    const layoutOk = expectedLayout === "soundscape"
      ? Boolean(soundscapeRoot && soundscapeTable && soundscapeSidebarItems > 0)
      : layoutGrid;
    return {
      ok: hasThemeOption && selectedText === name && hasInjectedCss && layoutOk,
      hasThemeOption,
      selectedText,
      hasInjectedCss,
      layoutGrid,
      layoutOk,
      expectedLayout,
      soundscapeSidebarItems,
      gridTemplateColumns,
      optionCount: select.options.length,
      activeThemeId: select.value,
    };
  }, { name: themeName, expectedLayout: themeLayout });

  const result = {
    upload: initialResult,
    reload: persistedResult,
    ok: initialResult.ok && persistedResult.ok,
  };

  if (!result.ok) {
    console.error("[FAIL] Third-party theme persistence failed after reload");
    console.error(JSON.stringify(result, null, 2));
    process.exit(3);
  }

  console.log("[PASS] Third-party theme upload works");
  console.log(JSON.stringify(result, null, 2));
  console.log(`[INFO] Screenshot: ${screenshotFile}`);
} finally {
  await context.close();
  await browser.close();
}
