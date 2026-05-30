import { OmoOutstandingRow, YieldRow } from "@/lib/api";

/** Bid-to-cover = amount bid (offered) ÷ amount accepted. >1 = oversubscribed. */
export function bidToCover(r: { offered_bdt_crore?: number; accepted_bdt_crore?: number }): number | null {
  const o = r.offered_bdt_crore, a = r.accepted_bdt_crore;
  if (o == null || a == null || a <= 0) return null;
  return o / a;
}

/** Latest auction per tenor with a valid bid-to-cover, sorted along the curve. */
export function bidCoverByTenor(rows: YieldRow[]): { tenor: string; ratio: number; years: number }[] {
  const best = new Map<string, YieldRow>();
  for (const r of rows) {
    const cur = best.get(r.tenor_label);
    if (!cur || r.auction_date > cur.auction_date) best.set(r.tenor_label, r);
  }
  return [...best.values()]
    .map(r => ({ tenor: r.tenor_label, ratio: bidToCover(r), years: r.tenor_years ?? 0 }))
    .filter((x): x is { tenor: string; ratio: number; years: number } => x.ratio !== null)
    .sort((a, b) => a.years - b.years);
}

/** Net liquidity = Σ injection outstanding − Σ absorption outstanding, per day.
 *  Positive = BB net-injecting (system short → tight). Negative = net-absorbing (flush). */
export function netLiquiditySeries(rows: OmoOutstandingRow[]): { date: string; net: number }[] {
  const byDate = new Map<string, number>();
  for (const r of rows) {
    const inject = r.direction ? r.direction.toUpperCase() === "INJECTION" : true;
    const signed = (inject ? 1 : -1) * (r.outstanding_bdt_crore ?? 0);
    byDate.set(r.date, (byDate.get(r.date) ?? 0) + signed);
  }
  return [...byDate.entries()]
    .map(([date, net]) => ({ date, net: Math.round(net * 100) / 100 }))
    .sort((a, b) => a.date.localeCompare(b.date));
}
