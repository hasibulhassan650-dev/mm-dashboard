// Pure transforms that turn the real BB API payloads into the shapes the
// terminal components expect. Server-safe (no React, no browser APIs).
import type {
  YieldRow, OmoOutstandingRow, CallMoneyResult, FxAuctionRow,
} from "@/lib/api";
import type { StackCat } from "@/components/terminal/charts";
import type { ComboRow } from "@/components/terminal/charts";

// ---- OMO instrument registry — the SINGLE source of truth ----
// Every OMO facility BB uses, with its market role. Charts, KPIs, legends and
// the liquidity-support breakdown all derive from THIS list. Directions are
// confirmed against the parser (fetchers/omo.py _INSTRUMENTS): 8 inject, only
// SDF absorbs. An instrument present in the data but absent here still appears
// (never dropped) via omoCatsFromData's fallback — so a new BB facility can
// never vanish silently. #1 rule: never lose a product.
export type OmoDirection = "INJECTION" | "ABSORPTION";
export interface OmoInstrument extends StackCat {
  full: string;
  direction: OmoDirection;   // INJECTION adds market liquidity; ABSORPTION mops it up
}
export const OMO_INSTRUMENTS: OmoInstrument[] = [
  { key: "CB_REPO", label: "Repo",         full: "Central Bank Repo",                color: "#60a5fa", direction: "INJECTION",  desc: "BB lends cash to banks against securities (injection)" },
  { key: "AR",      label: "Assured Repo", full: "Assured Repo",                     color: "#34d399", direction: "INJECTION",  desc: "Term liquidity-support repo, usually longer-dated (injection)" },
  { key: "IBLF",    label: "IBLF",         full: "Islamic Banks Liquidity Facility", color: "#a78bfa", direction: "INJECTION",  desc: "Short-term funds for Shariah-compliant banks (injection)" },
  { key: "SLF",     label: "SLF",          full: "Standing Lending Facility",        color: "#fbbf24", direction: "INJECTION",  desc: "Overnight borrowing from BB at the corridor ceiling (injection)" },
  { key: "MLS",     label: "MLS",          full: "Mudaraba Liquidity Support",       color: "#f472b6", direction: "INJECTION",  desc: "Shariah (Mudaraba) liquidity support for Islamic banks (injection)" },
  { key: "SLS",     label: "SLS",          full: "Special Liquidity Support",        color: "#22d3ee", direction: "INJECTION",  desc: "Ad-hoc special liquidity support beyond standing facilities (injection)" },
  { key: "SRF",     label: "SRF",          full: "Special Repo Facility",            color: "#fb923c", direction: "INJECTION",  desc: "Special repo outside the regular CB repo line (injection)" },
  { key: "CM_REPO", label: "CM Repo",      full: "Capital Market Repo",              color: "#a3e635", direction: "INJECTION",  desc: "Repo supporting banks' capital-market liquidity (injection)" },
  { key: "SDF",     label: "SDF",          full: "Standing Deposit Facility",        color: "#f87171", direction: "ABSORPTION", desc: "Banks park surplus at BB at the corridor floor (mop-up)" },
];
export const OMO_ABSORPTION_KEYS = new Set(OMO_INSTRUMENTS.filter((i) => i.direction === "ABSORPTION").map((i) => i.key));
const OMO_META = new Map(OMO_INSTRUMENTS.map((i) => [i.key, i]));
const OMO_FALLBACK_COLORS = ["#818cf8", "#2dd4bf", "#facc15", "#c084fc", "#fb7185", "#38bdf8"];

// Back-compat: the full category list (all instruments), StackCat-shaped.
export const OMO_CATS: StackCat[] = OMO_INSTRUMENTS.map(({ key, label, color, desc }) => ({ key, label, color, desc }));

/** Chart categories for exactly the instruments PRESENT in these rows — registry
 *  order first, then any unknown instrument appended with a fallback colour so a
 *  new BB facility is shown, never dropped. */
export function omoCatsFromData(rows: OmoOutstandingRow[]): StackCat[] {
  const present = new Set(rows.map((r) => r.instrument));
  const cats: StackCat[] = [];
  for (const i of OMO_INSTRUMENTS) if (present.has(i.key)) cats.push({ key: i.key, label: i.label, color: i.color, desc: i.desc });
  let f = 0;
  for (const k of [...present].sort()) if (!OMO_META.has(k)) cats.push({ key: k, label: k, color: OMO_FALLBACK_COLORS[f++ % OMO_FALLBACK_COLORS.length], desc: "OMO facility (unclassified)" });
  return cats;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function shortDay(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${String(d.getDate()).padStart(2, "0")} ${MONTHS[d.getMonth()]}`;
}
export { shortDay };

/** Keep ~maxLabels x-axis labels, blanking the rest (for dense time series). */
export function sparseLabels(items: string[], maxLabels = 8): string[] {
  const step = Math.max(1, Math.ceil(items.length / maxLabels));
  return items.map((s, i) => (i % step === 0 ? s : ""));
}

/** "YYYY-MM" → "MMM YY". */
export function shortMonth(s: string): string {
  const m = /^(\d{4})-(\d{2})/.exec(s);
  if (m) return `${MONTHS[+m[2] - 1] ?? m[2]} ${m[1].slice(2)}`;
  return s;
}

/** Pivot OMO outstanding rows into per-date stacked series in ৳ thousand-crore.
 *  Includes EVERY instrument present in the data (via omoCatsFromData) so the
 *  stacked total always equals the true outstanding — no product is ever
 *  dropped, and a new BB facility appears automatically. */
export function pivotOmo(rows: OmoOutstandingRow[]): Record<string, number | string>[] {
  const keys = new Set(omoCatsFromData(rows).map((c) => c.key));
  const byDate = new Map<string, Record<string, number>>();
  for (const r of rows) {
    if (!keys.has(r.instrument)) continue;   // keys covers all present → nothing dropped
    const slot = byDate.get(r.date) || {};
    slot[r.instrument] = (slot[r.instrument] || 0) + r.outstanding_bdt_crore / 1000; // → k-crore
    byDate.set(r.date, slot);
  }
  return [...byDate.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([date, vals]) => {
      const row: Record<string, number | string> = { label: shortDay(date), date };
      for (const k of keys) row[k] = +(vals[k] || 0).toFixed(2);
      return row;
    });
}

/** Net OMO liquidity per day (k-crore): Σ injection − Σ absorption across ALL
 *  instruments present. Positive = BB net-injecting. */
export function omoNetSeries(pivot: Record<string, number | string>[]): number[] {
  return pivot.map((d) => Object.keys(d).reduce((s, k) => {
    if (k === "label" || k === "date") return s;
    const v = Number(d[k]) || 0;
    return s + (OMO_ABSORPTION_KEYS.has(k) ? -v : v);
  }, 0));
}

export interface CurveBuild {
  tenors: string[];
  today: number[];
  weekAgo: number[] | null;
  monthAgo: number[] | null;
  latestDate: string;
}

/** Build the sovereign curve (today) plus 1W / 1M comparison from history. */
export function buildCurve(curve: YieldRow[], history: YieldRow[]): CurveBuild {
  const sorted = [...curve].sort((a, b) => a.tenor_years - b.tenor_years);
  const tenors = sorted.map((r) => r.tenor_label);
  const today = sorted.map((r) => r.cutoff_yield_pct);
  const latestDate = sorted.reduce((mx, r) => (r.auction_date > mx ? r.auction_date : mx), sorted[0]?.auction_date || "");

  const target = (days: number) => { const d = new Date(latestDate); d.setDate(d.getDate() - days); return d.getTime(); };

  // The curve as it stood on/before `whenMs`: the most-recent auction per tenor
  // at or before the target date. Falls back to the earliest available auction
  // (tenor too new), then to today's value (tenor has no history at all) so the
  // comparison line ALWAYS renders rather than silently disappearing.
  const pickAsOf = (tenor: string, whenMs: number, todayVal: number): number => {
    const rows = history
      .filter((h) => h.tenor_label === tenor)
      .sort((a, b) => a.auction_date.localeCompare(b.auction_date));
    if (!rows.length) return todayVal;
    const onOrBefore = rows.filter((r) => new Date(r.auction_date).getTime() <= whenMs);
    if (onOrBefore.length) return onOrBefore[onOrBefore.length - 1].cutoff_yield_pct;
    return rows[0].cutoff_yield_pct;
  };

  const hasHistory = history.length > 0;
  const weekAgo = hasHistory ? tenors.map((t, i) => pickAsOf(t, target(7), today[i])) : null;
  const monthAgo = hasHistory ? tenors.map((t, i) => pickAsOf(t, target(30), today[i])) : null;
  return { tenors, today, latestDate, weekAgo, monthAgo };
}

/** Per-tenor historical yield series (most recent N), for KPI sparklines. */
export function tenorSeries(history: YieldRow[], tenor: string, n = 24): number[] {
  return history
    .filter((h) => h.tenor_label === tenor)
    .sort((a, b) => a.auction_date.localeCompare(b.auction_date))
    .slice(-n)
    .map((h) => h.cutoff_yield_pct);
}

/** Call-money daily summary → combo-chart rows (rate line + volume bars). */
export function callMoneyCombo(cm: CallMoneyResult): ComboRow[] {
  return [...cm.daily_summary]
    .sort((a, b) => a.trade_date.localeCompare(b.trade_date))
    .map((d) => {
      const war = d.overnight_wavg_rate ?? 0;
      return {
        label: shortDay(d.trade_date),
        vol: d.total_volume_crore || d.overnight_volume_crore || 0,
        war,
        hi: d.overnight_high ?? war,
        lo: d.overnight_low ?? war,
      };
    })
    .filter((r) => r.war > 0);
}

/** FX auction USD/BDT weighted-avg-rate series (most recent N). */
export function fxRateSeries(rows: FxAuctionRow[], n = 24): number[] {
  return [...rows]
    .sort((a, b) => a.auction_date.localeCompare(b.auction_date))
    .map((r) => r.weighted_avg_rate ?? r.cutoff_rate)
    .filter((v): v is number => v != null)
    .slice(-n);
}
