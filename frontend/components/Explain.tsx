"use client";
import * as React from "react";
import Link from "next/link";
import { lookup, slug } from "@/lib/glossary";

/**
 * Two reading levels for the same page.
 *
 * "desk"   — the normal register, for someone who works the money market.
 * "simple" — the same ideas with no jargon and no assumed statistics, for
 *            someone learning. Every glossary term carries both, so switching
 *            mode swaps definitions AND the surrounding narrative rather than
 *            just hiding words.
 *
 * The choice is remembered in localStorage: a reader who needs plain language
 * needs it on every visit, and should not have to re-select it each time.
 */
export type Mode = "desk" | "simple";

const Ctx = React.createContext<{ mode: Mode; setMode: (m: Mode) => void }>({
  mode: "desk", setMode: () => {},
});

export function useExplain() {
  return React.useContext(Ctx);
}

const STORAGE_KEY = "mm.explainMode";

export function ExplainProvider({ children }: { children: React.ReactNode }) {
  const [mode, setModeState] = React.useState<Mode>("desk");

  // Read after mount, not during render — the server has no localStorage, and
  // reading it during render would desync the first client paint from the HTML.
  React.useEffect(() => {
    try {
      const saved = window.localStorage.getItem(STORAGE_KEY);
      if (saved === "simple" || saved === "desk") setModeState(saved);
    } catch { /* private mode / storage disabled — stay on the default */ }
  }, []);

  const setMode = React.useCallback((m: Mode) => {
    setModeState(m);
    try { window.localStorage.setItem(STORAGE_KEY, m); } catch { /* ignore */ }
  }, []);

  return <Ctx.Provider value={{ mode, setMode }}>{children}</Ctx.Provider>;
}

/** Segmented control that switches the whole page's reading level. */
export function ExplainToggle() {
  const { mode, setMode } = useExplain();
  return (
    <div className="seg" role="group" aria-label="Explanation level">
      <button className={"seg-b" + (mode === "desk" ? " on" : "")}
        onClick={() => setMode("desk")} aria-pressed={mode === "desk"}>
        Desk
      </button>
      <button className={"seg-b" + (mode === "simple" ? " on" : "")}
        onClick={() => setMode("simple")} aria-pressed={mode === "simple"}
        title="Plain language — no jargon, no statistics assumed">
        Explain simply
      </button>
    </div>
  );
}

/**
 * An inline jargon term: hover for the definition, click through to the full
 * glossary entry. The definition shown follows the current reading level.
 */
export function Term({ t, children }: { t: string; children?: React.ReactNode }) {
  const { mode } = useExplain();
  const [open, setOpen] = React.useState(false);
  const entry = lookup(t);
  const label = children ?? t;

  if (!entry) {
    // Unknown term — render plain text rather than a dead link, so a typo
    // degrades to normal copy instead of a broken affordance.
    return <>{label}</>;
  }
  const body = mode === "simple" ? (entry.eli10 ?? entry.def) : entry.def;

  return (
    <span style={{ position: "relative", display: "inline-block" }}
      onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <Link href={`/glossary#${slug(entry.term)}`}
        onClick={() => setOpen(false)}
        style={{
          color: "inherit", textDecoration: "none",
          borderBottom: "1px dotted var(--accent)", cursor: "help",
        }}>
        {label}
      </Link>
      {open && (
        <span role="tooltip" style={{
          position: "absolute", left: 0, top: "1.5em", zIndex: 60, width: 300,
          background: "var(--panel-2)", border: "1px solid var(--border)",
          borderRadius: "var(--radius-sm)", padding: "9px 11px",
          fontSize: 11.5, lineHeight: 1.6, color: "var(--fg-dim)",
          boxShadow: "0 10px 30px rgba(0,0,0,.45)", fontWeight: 400,
          whiteSpace: "normal", textTransform: "none", letterSpacing: 0,
        }}>
          <span style={{ display: "block", fontWeight: 600, color: "var(--accent)", marginBottom: 3 }}>
            {entry.term}{entry.full ? ` — ${entry.full}` : ""}
          </span>
          {body}
          <span style={{ display: "block", marginTop: 5, fontSize: 10.5, color: "var(--fg-mute)" }}>
            click for the full glossary entry
          </span>
        </span>
      )}
    </span>
  );
}

/** Renders one of two texts depending on the reading level. */
export function Say({ desk, simple }: { desk: React.ReactNode; simple: React.ReactNode }) {
  const { mode } = useExplain();
  return <>{mode === "simple" ? simple : desk}</>;
}
