import fs from "node:fs";
import path from "node:path";
import { chromium } from "playwright";

const baseUrl = process.env.WEBUI_URL || "http://127.0.0.1:58090/webui/";
const themeFile = process.env.THEME_FILE || path.resolve("tests/fixtures/soundscape-1to1.xmtheme");
const themeName = process.env.THEME_NAME || "SoundScape 1:1";
const artifactDir = path.resolve("tests/artifacts");

if (!fs.existsSync(themeFile)) {
  console.error(`[FAIL] theme file not found: ${themeFile}`);
  process.exit(1);
}

fs.mkdirSync(artifactDir, { recursive: true });

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
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

async function closeSettingsPanel() {
  await page.mouse.click(8, 8);
  if (await page.locator("#settings-component").isVisible().catch(() => false)) {
    await page.keyboard.press("Escape").catch(() => undefined);
  }
  if (await page.locator("#settings-component").isVisible().catch(() => false)) {
    await page.mouse.click(12, 12);
  }
  await page.locator("#settings-component").waitFor({ state: "hidden", timeout: 15000 });
}

async function selectThemeByLabel(label) {
  await openSettingsPanel();
  const select = page.locator("#theme-mode-select");
  await select.selectOption({ label });
  await page.waitForTimeout(300);
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

  await selectThemeByLabel(themeName);
  await closeSettingsPanel();
  await page.screenshot({ path: path.join(artifactDir, "acceptance-soundscape.png"), fullPage: true });

  await selectThemeByLabel("Default");
  await closeSettingsPanel();
  await page.screenshot({ path: path.join(artifactDir, "acceptance-default.png"), fullPage: true });

  await selectThemeByLabel("Dark");
  await closeSettingsPanel();
  await page.screenshot({ path: path.join(artifactDir, "acceptance-dark.png"), fullPage: true });

  console.log("[PASS] Captured acceptance screenshots");
  console.log(path.join(artifactDir, "acceptance-soundscape.png"));
  console.log(path.join(artifactDir, "acceptance-default.png"));
  console.log(path.join(artifactDir, "acceptance-dark.png"));
} finally {
  await context.close();
  await browser.close();
}
