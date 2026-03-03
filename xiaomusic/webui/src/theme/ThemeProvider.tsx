import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react";

import {
  type CustomThemePack,
  type ThemeId,
  type ThemeLayoutId,
  type ThemeSnapshot,
  applyBuiltInTheme,
  applyCustomCss,
  isBuiltInTheme,
  loadThemeFromStorage,
  persistThemeToStorage,
  removeCustomCss,
  validateThemePackageText,
} from "./theme";

type ThemeContextValue = {
  selectedThemeId: ThemeId;
  activeLayout: ThemeLayoutId;
  customThemes: CustomThemePack[];
  setTheme: (id: ThemeId) => void;
  uploadThemePackage: (file: File | null) => Promise<{ ok: boolean; error?: string; name?: string }>;
  validationError: string;
};

const ThemeContext = createContext<ThemeContextValue | null>(null);

function applySnapshot(snapshot: ThemeSnapshot): void {
  if (isBuiltInTheme(snapshot.selectedId)) {
    removeCustomCss();
    applyBuiltInTheme(snapshot.selectedId);
    document.documentElement.dataset.themeLayout = "classic";
    return;
  }
  const custom = snapshot.customThemes.find((t) => t.id === snapshot.selectedId);
  if (!custom) {
    removeCustomCss();
    applyBuiltInTheme("default");
    document.documentElement.dataset.themeLayout = "classic";
    return;
  }
  applyBuiltInTheme("default");
  applyCustomCss(custom.css);
  document.documentElement.dataset.themeLayout = custom.layout || "classic";
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [selectedThemeId, setSelectedThemeId] = useState<ThemeId>("default");
  const [activeLayout, setActiveLayout] = useState<ThemeLayoutId>("classic");
  const [customThemes, setCustomThemes] = useState<CustomThemePack[]>([]);
  const [validationError, setValidationError] = useState<string>("");

  const committedRef = useRef<ThemeSnapshot>({ selectedId: "default", customThemes: [] });

  useEffect(() => {
    const saved = loadThemeFromStorage();
    committedRef.current = saved;
    setSelectedThemeId(saved.selectedId);
    setCustomThemes(saved.customThemes);
    if (isBuiltInTheme(saved.selectedId)) {
      setActiveLayout("classic");
    } else {
      setActiveLayout(saved.customThemes.find((t) => t.id === saved.selectedId)?.layout || "classic");
    }
    applySnapshot(saved);
  }, []);

  const api = useMemo<ThemeContextValue>(() => {
    const setTheme = (id: ThemeId) => {
      setValidationError("");

      const nextSnapshot: ThemeSnapshot = { selectedId: id, customThemes };
      applySnapshot(nextSnapshot);
      setSelectedThemeId(id);
      if (isBuiltInTheme(id)) {
        setActiveLayout("classic");
      } else {
        setActiveLayout(customThemes.find((t) => t.id === id)?.layout || "classic");
      }
      committedRef.current = nextSnapshot;
      persistThemeToStorage(nextSnapshot);
    };

    const uploadThemePackage = async (file: File | null) => {
      if (!file) {
        return { ok: false, error: "未选择文件" };
      }
      const lower = file.name.toLowerCase();
      if (!lower.endsWith(".json") && !lower.endsWith(".xmtheme")) {
        const err = "仅支持上传 .json 或 .xmtheme 主题包";
        setValidationError(err);
        return { ok: false, error: err };
      }
      const text = await file.text();
      const parsed = validateThemePackageText(text);
      if (!parsed.ok) {
        const err = parsed.error || "主题包校验失败";
        setValidationError(err);
        return { ok: false, error: err };
      }
      setValidationError("");
      const id = `custom-${Date.now()}`;
      const pending: CustomThemePack = {
        id,
        name: parsed.name || "Uploaded Theme",
        css: parsed.css || "",
        layout: parsed.layout,
      };
      const stagedThemes = [...customThemes, pending];
      const stagedSnapshot: ThemeSnapshot = { selectedId: id, customThemes: stagedThemes };
      setCustomThemes(stagedThemes);
      setSelectedThemeId(id);
      setActiveLayout(pending.layout || "classic");
      applySnapshot(stagedSnapshot);
      committedRef.current = stagedSnapshot;
      persistThemeToStorage(stagedSnapshot);
      return { ok: true, name: pending.name };
    };

    return {
      selectedThemeId,
      activeLayout,
      customThemes,
      setTheme,
      uploadThemePackage,
      validationError,
    };
  }, [activeLayout, customThemes, selectedThemeId, validationError]);

  return <ThemeContext.Provider value={api}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) {
    throw new Error("useTheme must be used within ThemeProvider");
  }
  return ctx;
}
