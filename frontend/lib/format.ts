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

/**
 * A server timestamp as a Date. The API sends UTC with NO zone ("2026-11-18T14:49:53"), and `new Date()` reads a zone-less date-time
 * as LOCAL time: in Bangladesh (UTC+6) every age came out six hours too old ("Auto 6h ago" for a refresh that finished minutes
 * earlier), which can also trip the "refresh has stopped" banner for a healthy system. A date-time without a zone is UTC;
 * a date-only string ("2026-11-18") is already read as UTC and an explicit zone is respected.
 */
export function parseTs(iso: string): Date {
  const hasTime = /T\d{2}:\d{2}/.test(iso);
  const hasZone = /(Z|[+-]\d{2}:?\d{2})$/.test(iso);
  return new Date(hasTime && !hasZone ? iso + "Z" : iso);
}

/** Every date shown on this site is a Bangladesh date. Formatting in the viewer's zone made Vercel (UTC) and a Dhaka
 *  browser (UTC+6) render different text (a hydration mismatch) and left the zone unlabeled. */
export const DESK_TZ = "Asia/Dhaka";
export const DESK_TZ_LABEL = "UTC+6";

const DHAKA_FMT = new Intl.DateTimeFormat("en-GB", {
  timeZone: DESK_TZ, year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});

/** Calendar/clock parts of an instant as read in Dhaka. */
function dhaka(d: Date) {
  const p: Record<string, string> = {};
  for (const x of DHAKA_FMT.formatToParts(d)) p[x.type] = x.value;
  return { y: p.year, m: parseInt(p.month, 10) - 1, d: p.day, hh: p.hour, mm: p.minute };
}

/** Today's date (YYYY-MM-DD) in Dhaka. `new Date().toISOString().slice(0, 10)` is the UTC date, which is yesterday's
 *  date between 00:00 and 06:00 Dhaka. */
export function todayDhaka(): string {
  const t = dhaka(new Date());
  return `${t.y}-${String(t.m + 1).padStart(2, "0")}-${t.d}`;
}

/** ISO/date string → "DD MMM YY" (BD convention), read in Dhaka. */
export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const d = parseTs(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const t = dhaka(d);
  return `${t.d} ${MONTHS[t.m]} ${t.y.slice(2)}`;
}

/** "YYYY-MM" (or any parseable date) → "MMM YY". */
export function fmtMonth(s: string | null | undefined): string {
  if (!s) return DASH;
  const m = /^(\d{4})-(\d{2})/.exec(s);
  if (m) {
    const mi = parseInt(m[2], 10) - 1;
    return `${MONTHS[mi] ?? m[2]} ${m[1].slice(2)}`;
  }
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return s;
  return `${MONTHS[d.getMonth()]} ${String(d.getFullYear()).slice(2)}`;
}

/** Short axis label → "DD MMM". */
export function fmtDateShort(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const d = parseTs(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const t = dhaka(d);
  return `${t.d} ${MONTHS[t.m]}`;
}

/** ISO → "DD MMM YY, HH:mm UTC+6" (Dhaka time, labelled). */
export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const d = parseTs(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const t = dhaka(d);
  return `${fmtDate(iso)}, ${t.hh}:${t.mm} ${DESK_TZ_LABEL}`;
}

/** Relative age — e.g. "3m ago". */
export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const t = parseTs(iso).getTime();
  if (Number.isNaN(t)) return DASH;
  const s = Math.floor((Date.now() - t) / 1000);
  if (s < 60)  return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60)  return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24)  return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}
