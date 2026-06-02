"use client";
import * as React from "react";

export type Theme = "dark" | "light";
export type Density = "compact" | "regular" | "comfy";

interface Tweaks {
  theme: Theme;
  density: Density;
  accent: string;
  grid: boolean;
  tickerSpeed: number;
}

interface Ctx extends Tweaks {
  toggleTheme: () => void;
  set: <K extends keyof Tweaks>(key: K, value: Tweaks[K]) => void;
}

export const ACCENTS = ["#1f9e6e", "#2f7df6", "#c98a16", "#8b5cf6"];

const DEFAULTS: Tweaks = { theme: "dark", density: "regular", accent: "#1f9e6e", grid: true, tickerSpeed: 48 };

const ThemeCtx = React.createContext<Ctx | null>(null);

export function useTheme(): Ctx {
  const ctx = React.useContext(ThemeCtx);
  if (!ctx) throw new Error("useTheme must be used within ThemeProvider");
  return ctx;
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [t, setT] = React.useState<Tweaks>(DEFAULTS);

  // hydrate from localStorage on mount (avoids SSR mismatch — server renders defaults)
  React.useEffect(() => {
    try {
      const raw = localStorage.getItem("bb_tweaks");
      if (raw) setT((prev) => ({ ...prev, ...JSON.parse(raw) }));
    } catch { /* ignore */ }
  }, []);

  // reflect onto <html> + persist
  React.useEffect(() => {
    const html = document.documentElement;
    html.setAttribute("data-theme", t.theme);
    html.setAttribute("data-density", t.density);
    html.style.setProperty("--accent", t.accent);
    try { localStorage.setItem("bb_tweaks", JSON.stringify(t)); } catch { /* ignore */ }
  }, [t]);

  const value: Ctx = {
    ...t,
    toggleTheme: () => setT((p) => ({ ...p, theme: p.theme === "dark" ? "light" : "dark" })),
    set: (key, val) => setT((p) => ({ ...p, [key]: val })),
  };

  return <ThemeCtx.Provider value={value}>{children}</ThemeCtx.Provider>;
}
