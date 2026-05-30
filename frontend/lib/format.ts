// Central formatting helpers — single source of truth for units, dates, numbers.
// All functions tolerate null/undefined and return an em-dash placeholder.

const DASH = "—";
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function nz(n: number | null | undefined): n is number {
  return n != null && !Number.isNaN(n);
}

/** Group with thousands separators, optional decimals. */
export function fmtNum(n: number | null | undefined, dp = 0): string {
  if (!nz(n)) return DASH;
  return n.toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp });
}

/** BDT crore — e.g. "12,775 cr". */
export function fmtCrore(n: number | null | undefined, dp = 0): string {
  if (!nz(n)) return DASH;
  return `${fmtNum(n, dp)} cr`;
}

/** BDT million — e.g. "1,200 mn". */
export function fmtBDTmn(n: number | null | undefined, dp = 0): string {
  if (!nz(n)) return DASH;
  return `${fmtNum(n, dp)} mn`;
}

/** USD million — e.g. "$120.5m". */
export function fmtUSDmn(n: number | null | undefined, dp = 1): string {
  if (!nz(n)) return DASH;
  return `$${fmtNum(n, dp)}m`;
}

/** Percentage — e.g. "9.85%". Pass the value already in percent units. */
export function fmtPct(n: number | null | undefined, dp = 2): string {
  if (!nz(n)) return DASH;
  return `${n.toFixed(dp)}%`;
}

/** Exchange rate — e.g. "121.5000". */
export function fmtRate(n: number | null | undefined, dp = 4): string {
  if (!nz(n)) return DASH;
  return n.toFixed(dp);
}

/** Basis points from a percentage-point delta — e.g. 0.25 → "+25 bps". */
export function bps(deltaPct: number | null | undefined): string {
  if (!nz(deltaPct)) return DASH;
  const b = Math.round(deltaPct * 100);
  return `${b > 0 ? "+" : ""}${b} bps`;
}

/** ISO/date string → "DD MMM YY" (BD convention). */
export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const yy = String(d.getFullYear()).slice(2);
  return `${String(d.getDate()).padStart(2, "0")} ${MONTHS[d.getMonth()]} ${yy}`;
}

/** Short axis label → "DD MMM". */
export function fmtDateShort(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${String(d.getDate()).padStart(2, "0")} ${MONTHS[d.getMonth()]}`;
}

/** ISO → "DD MMM YY, HH:mm". */
export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${fmtDate(iso)}, ${hh}:${mm}`;
}

/** Relative age — e.g. "3m ago". */
export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return DASH;
  const s = Math.floor((Date.now() - t) / 1000);
  if (s < 60)  return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60)  return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24)  return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}
