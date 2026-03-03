export type BuiltInThemeId = "default" | "dark";
export type ThemeId = BuiltInThemeId | string;
export type ThemeLayoutId = "classic" | "soundscape";

export type CustomThemePack = {
  id: string;
  name: string;
  css: string;
  layout?: ThemeLayoutId;
};

export type ThemeSnapshot = {
  selectedId: ThemeId;
  customThemes: CustomThemePack[];
};

export const THEME_SELECTED_KEY = "xm_theme_selected";
export const THEME_CUSTOM_THEMES_KEY = "xm_theme_custom_themes";
export const THEME_PREV_KEY = "xm_theme_prev";
const CUSTOM_THEME_STYLE_ID = "custom-theme-style";
const MAX_CUSTOM_CSS_LENGTH = 50_000;
const MAX_THEME_NAME_LENGTH = 40;

function sanitizeLayout(raw: unknown): ThemeLayoutId | undefined {
  const value = String(raw || "").trim().toLowerCase();
  if (!value || value === "classic") {
    return undefined;
  }
  if (value === "soundscape") {
    return "soundscape";
  }
  return undefined;
}

export function isBuiltInTheme(id: ThemeId): id is BuiltInThemeId {
  return id === "default" || id === "dark";
}

export function applyBuiltInTheme(mode: BuiltInThemeId): void {
  if (mode === "default") {
    delete document.documentElement.dataset.theme;
    return;
  }
  document.documentElement.dataset.theme = mode;
}

export function applyCustomCss(css: string): void {
  let styleEl = document.getElementById(CUSTOM_THEME_STYLE_ID) as HTMLStyleElement | null;
  if (!styleEl) {
    styleEl = document.createElement("style");
    styleEl.id = CUSTOM_THEME_STYLE_ID;
    document.head.appendChild(styleEl);
  }
  styleEl.textContent = css;
}

export function removeCustomCss(): void {
  const styleEl = document.getElementById(CUSTOM_THEME_STYLE_ID);
  if (styleEl?.parentNode) {
    styleEl.parentNode.removeChild(styleEl);
  }
}

export function validateCustomCss(css: string): { ok: boolean; error?: string } {
  if (!css.trim()) {
    return { ok: false, error: "主题文件内容为空，请上传有效主题" };
  }
  if (css.length > MAX_CUSTOM_CSS_LENGTH) {
    return { ok: false, error: `CSS 长度超过限制（最大 ${MAX_CUSTOM_CSS_LENGTH} 字符）` };
  }
  if (/@import/i.test(css)) {
    return { ok: false, error: "禁止使用 @import" };
  }
  if (/<\s*\/?\s*style/i.test(css)) {
    return { ok: false, error: "主题包内 css 不允许包含 <style> 标签" };
  }
  if (/url\s*\(\s*["']?\s*https?:/i.test(css)) {
    return { ok: false, error: "禁止在 CSS 中引用远程 http/https 资源" };
  }
  return { ok: true };
}

export function validateThemePackageText(raw: string): {
  ok: boolean;
  error?: string;
  name?: string;
  css?: string;
  layout?: ThemeLayoutId;
} {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { ok: false, error: "主题包格式错误：请上传 JSON 主题包" };
  }
  const obj = parsed as Record<string, unknown>;
  const name = String(obj.name || "").trim();
  const css = String(obj.css || "");
  const layoutRaw = String(obj.layout || "").trim().toLowerCase();
  if (!name) {
    return { ok: false, error: "主题包缺少 name" };
  }
  if (name.length > MAX_THEME_NAME_LENGTH) {
    return { ok: false, error: `主题名过长（最多 ${MAX_THEME_NAME_LENGTH} 字符）` };
  }
  const cssCheck = validateCustomCss(css);
  if (!cssCheck.ok) {
    return cssCheck;
  }
  if (layoutRaw && layoutRaw !== "classic" && layoutRaw !== "soundscape") {
    return { ok: false, error: "layout 仅支持 classic 或 soundscape" };
  }
  return { ok: true, name, css, layout: sanitizeLayout(layoutRaw) };
}

function normalizeBuiltIn(raw: unknown): BuiltInThemeId {
  return raw === "dark" ? "dark" : "default";
}

function sanitizeCustomThemes(raw: unknown): CustomThemePack[] {
  if (!Array.isArray(raw)) {
    return [];
  }
  const out: CustomThemePack[] = [];
  for (const item of raw) {
    const obj = item as Record<string, unknown>;
    const id = String(obj.id || "").trim();
    const name = String(obj.name || "").trim();
    const css = String(obj.css || "");
    const layout = sanitizeLayout(obj.layout);
    if (!id || !name) {
      continue;
    }
    const check = validateCustomCss(css);
    if (!check.ok) {
      continue;
    }
    out.push({ id, name, css, layout });
  }
  return out;
}

export function loadThemeFromStorage(): ThemeSnapshot {
  const customThemes = sanitizeCustomThemes(
    JSON.parse(localStorage.getItem(THEME_CUSTOM_THEMES_KEY) || "[]"),
  );
  const selectedRaw = String(localStorage.getItem(THEME_SELECTED_KEY) || "").trim();
  if (selectedRaw && !isBuiltInTheme(selectedRaw) && customThemes.some((t) => t.id === selectedRaw)) {
    return { selectedId: selectedRaw, customThemes };
  }
  return { selectedId: normalizeBuiltIn(selectedRaw), customThemes };
}

export function persistThemeToStorage(snapshot: ThemeSnapshot): void {
  localStorage.setItem(THEME_SELECTED_KEY, snapshot.selectedId);
  localStorage.setItem(THEME_CUSTOM_THEMES_KEY, JSON.stringify(snapshot.customThemes));
}

export function savePreviousThemeSnapshot(snapshot: ThemeSnapshot): void {
  localStorage.setItem(THEME_PREV_KEY, JSON.stringify(snapshot));
}

export function clearPreviousThemeSnapshot(): void {
  localStorage.removeItem(THEME_PREV_KEY);
}
