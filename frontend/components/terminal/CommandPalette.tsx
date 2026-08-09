"use client";
import * as React from "react";
import { useRouter } from "next/navigation";
import { NAV } from "./nav";
import { Icon } from "./Icon";

/**
 * Command palette — the terminal's jump-anywhere control.
 * Opens on Ctrl/⌘-K or "/" (when not typing in a field), and from the
 * top-bar search box. Filters destinations, arrow-keys to move, Enter to go.
 *
 * Destinations are the nav pages plus deep links a fund manager reaches for
 * (a metric jumps straight to the page that explains it).
 */
export interface Dest { label: string; href: string; icon: string; hint?: string; keywords?: string }

const DEEP: Dest[] = [
  { label: "OMO — Net Liquidity Stance", href: "/omo", icon: "layers", hint: "injection vs absorption", keywords: "repo sdf slf iblf assured absorption injection" },
  { label: "Liquidity Forecast — next 4 weeks", href: "/forecast", icon: "pulse", hint: "known dated flows", keywords: "auction maturity coupon settlement drain tight lend borrow" },
  { label: "Call Money — corridor position", href: "/callmoney", icon: "pulse", hint: "WAR vs SDF–SLF", keywords: "overnight war weighted rate corridor" },
  { label: "Yield Curve", href: "/yields", icon: "curve", hint: "primary cut-offs", keywords: "tbill tbond tenor 91d 364d 10y cutoff" },
  { label: "FX Auctions — USD/BDT", href: "/fx", icon: "swap", hint: "BB interventions", keywords: "dollar taka exchange rate reserve" },
  { label: "Securities master", href: "/securities", icon: "doc", hint: "all ISINs", keywords: "isin bond bill maturity coupon outstanding" },
  { label: "Portfolio tool", href: "/portfolio", icon: "briefcase", hint: "your holdings", keywords: "holdings valuation mtm" },
  { label: "Glossary", href: "/glossary", icon: "book", hint: "term definitions", keywords: "definition meaning explain" },
];

function buildDests(): Dest[] {
  const seen = new Set<string>();
  const out: Dest[] = [];
  for (const n of NAV) { out.push({ label: n.label, href: n.href, icon: n.icon }); seen.add(n.label.toLowerCase()); }
  for (const d of DEEP) if (!seen.has(d.label.toLowerCase())) out.push(d);
  return out;
}

function score(d: Dest, q: string): number {
  if (!q) return 1;
  const hay = (d.label + " " + (d.hint || "") + " " + (d.keywords || "")).toLowerCase();
  const label = d.label.toLowerCase();
  if (label.startsWith(q)) return 100;
  if (label.includes(q)) return 60;
  if (hay.includes(q)) return 30;
  // subsequence (fuzzy)
  let i = 0; for (const ch of hay) { if (ch === q[i]) i++; if (i === q.length) return 10; }
  return 0;
}

export default function CommandPalette() {
  const router = useRouter();
  const [open, setOpen] = React.useState(false);
  const [q, setQ] = React.useState("");
  const [sel, setSel] = React.useState(0);
  const inputRef = React.useRef<HTMLInputElement>(null);

  const dests = React.useMemo(buildDests, []);
  const results = React.useMemo(() => {
    const ql = q.trim().toLowerCase();
    return dests.map((d) => ({ d, s: score(d, ql) })).filter((x) => x.s > 0)
      .sort((a, b) => b.s - a.s).map((x) => x.d);
  }, [q, dests]);

  const close = React.useCallback(() => { setOpen(false); setQ(""); setSel(0); }, []);
  const go = React.useCallback((href: string) => { close(); router.push(href); }, [close, router]);

  // global open shortcut: Ctrl/⌘-K, or "/" when not in a field
  React.useEffect(() => {
    const isTyping = () => {
      const el = document.activeElement as HTMLElement | null;
      return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable);
    };
    const onKey = (e: KeyboardEvent) => {
      if ((e.key === "k" || e.key === "K") && (e.metaKey || e.ctrlKey)) { e.preventDefault(); setOpen(true); }
      else if (e.key === "/" && !isTyping() && !open) { e.preventDefault(); setOpen(true); }
      else if (e.key === "Escape" && open) { close(); }
    };
    // let the top-bar search box request the palette
    const onOpen = () => setOpen(true);
    window.addEventListener("keydown", onKey);
    window.addEventListener("bb:command", onOpen as EventListener);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("bb:command", onOpen as EventListener); };
  }, [open, close]);

  React.useEffect(() => { if (open) setTimeout(() => inputRef.current?.focus(), 0); }, [open]);
  React.useEffect(() => { setSel(0); }, [q]);

  if (!open) return null;

  const onListKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(s + 1, results.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(s - 1, 0)); }
    else if (e.key === "Enter") { e.preventDefault(); const r = results[sel]; if (r) go(r.href); }
  };

  return (
    <div className="cmdk-scrim" onClick={close}>
      <div className="cmdk" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Command palette">
        <div className="cmdk-input">
          <Icon name="search" size={16} />
          <input
            ref={inputRef}
            value={q}
            placeholder="Jump to a page or metric…"
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={onListKey}
          />
          <kbd>esc</kbd>
        </div>
        <div className="cmdk-list">
          {results.length === 0 && <div className="cmdk-empty">No matches</div>}
          {results.map((d, i) => (
            <button
              key={d.href + d.label}
              className={"cmdk-item" + (i === sel ? " on" : "")}
              onMouseEnter={() => setSel(i)}
              onClick={() => go(d.href)}
            >
              <span className="cmdk-ic"><Icon name={d.icon} size={16} /></span>
              <span className="cmdk-lb">{d.label}</span>
              {d.hint && <span className="cmdk-hint">{d.hint}</span>}
              <span className="cmdk-go"><Icon name="chevron" size={13} /></span>
            </button>
          ))}
        </div>
        <div className="cmdk-foot">
          <span><kbd>↑</kbd><kbd>↓</kbd> move</span>
          <span><kbd>↵</kbd> open</span>
          <span><kbd>⌘K</kbd> / <kbd>/</kbd> anytime</span>
        </div>
      </div>
    </div>
  );
}
