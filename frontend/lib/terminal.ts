// Pure transforms that turn the real BB API payloads into the shapes the
// terminal components expect. Server-safe (no React, no browser APIs).
import type {
  YieldRow, OmoOutstandingRow, CallMoneyResult, FxAuctionRow,
} from "@/lib/api";
import type { StackCat } from "@/components/terminal/charts";
import type { ComboRow } from "@/components/terminal/charts";

// ---- OMO instrument categories (colour + description) ----
export const OMO_CATS: StackCat[] = [
  { key: "AR", label: "AR", color: "#34d399", desc: "Assured Repo" },
  { key: "IBLF", label: "IBLF", color: "#a78bfa", desc: "Islamic Banks Liquidity Facility" },
  { key: "CB_REPO", label: "CB_REPO", color: "#60a5fa", desc: "Repo (7/14/28D)" },
  { key: "SLF", label: "SLF", color: "#fbbf24", desc: "Standing Lending Facility" },
  { key: "SDF", label: "SDF", color: "#f87171", desc: "Standing Deposit Facility" },
];

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

/** Pivot OMO outstanding rows into per-date stacked series in ৳ thousand-crore. */
export function pivotOmo(rows: OmoOutstandingRow[]): Record<string, number | string>[] {
  const byDate = new Map<string, Record<string, number>>();
  for (const r of rows) {
    const slot = byDate.get(r.date) || {};
    const key = OMO_CATS.some((c) => c.key === r.instrument) ? r.instrument : null;
    if (key) slot[key] = (slot[key] || 0) + r.outstanding_bdt_crore / 1000; // → k-crore
    byDate.set(r.date, slot);
  }
  return [...byDate.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([date, vals]) => {
      const row: Record<string, number | string> = { label: shortDay(date), date };
      for (const c of OMO_CATS) row[c.key] = +(vals[c.key] || 0).toFixed(2);
      return row;
    });
}

/** Net OMO injection per day (k-crore) for a sparkline / KPI. */
export function omoNetSeries(pivot: Record<string, number | string>[]): number[] {
  return pivot.map((d) => OMO_CATS.reduce((s, c) => s + (Number(d[c.key]) || 0), 0));
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
  const pick = (tenor: string, whenMs: number): number | null => {
    const rows = history.filter((h) => h.tenor_label === tenor);
    if (!rows.length) return null;
    let best = rows[0], bd = Infinity;
    for (const r of rows) {
      const diff = Math.abs(new Date(r.auction_date).getTime() - whenMs);
      if (diff < bd) { bd = diff; best = r; }
    }
    return best.cutoff_yield_pct;
  };
  const wk = tenors.map((t) => pick(t, target(7)));
  const mo = tenors.map((t) => pick(t, target(30)));
  return {
    tenors, today, latestDate,
    weekAgo: wk.every((v) => v != null) ? (wk as number[]) : null,
    monthAgo: mo.every((v) => v != null) ? (mo as number[]) : null,
  };
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
