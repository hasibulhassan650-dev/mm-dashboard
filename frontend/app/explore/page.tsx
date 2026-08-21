import { api } from "@/lib/api";
import ExploreTool, { type SeriesDef } from "@/components/ExploreTool";
import RelatedLinks from "@/components/RelatedLinks";
import Freshness from "@/components/Freshness";
import type { MacroRow } from "@/lib/api";

export const revalidate = 300;

export default async function ExplorePage() {
  const [omoOut, cm, yh, fx, macro, rr, flows, fresh] = await Promise.all([
    api.omoOutstanding(365).catch(() => []),
    api.callmoney(730).catch(() => ({ daily_summary: [], latest_breakdown: [], latest_date: null })),
    api.yields(36).catch(() => []),
    api.fx(1095).catch(() => []),
    api.macro(),
    api.refrate(730).catch(() => []),
    api.flows(24).catch(() => []),
    api.freshness(),
  ]);
  const today = new Date().toISOString().slice(0, 10);

  // build a date->value map, dropping nulls/NaN
  const mk = (entries: [string | null | undefined, number | null | undefined][]): Record<string, number> => {
    const o: Record<string, number> = {};
    for (const [d, v] of entries) {
      if (d && v != null && Number.isFinite(v)) o[d.slice(0, 10)] = v as number;
    }
    return o;
  };

  // OMO net liquidity outstanding = injection − absorption, per day
  const omoAgg: Record<string, { inj: number; abs: number }> = {};
  for (const r of omoOut) {
    const d = r.date.slice(0, 10);
    (omoAgg[d] ??= { inj: 0, abs: 0 });
    if ((r.direction || "").toUpperCase() === "ABSORPTION") omoAgg[d].abs += r.outstanding_bdt_crore || 0;
    else omoAgg[d].inj += r.outstanding_bdt_crore || 0;
  }
  const omoNet: Record<string, number> = {};
  for (const [d, v] of Object.entries(omoAgg)) omoNet[d] = v.inj - v.abs;

  const yTenor = (t: string) => mk(yh.filter((r) => r.tenor_label === t).map((r) => [r.auction_date, r.cutoff_yield_pct]));

  // Curve spreads. Tenors auction on different days, so a spread only exists if
  // both legs are carried forward from their own last print — taking only dates
  // where both auctioned on the SAME day would leave almost nothing, since
  // bonds print monthly and bills weekly.
  const spread = (a: Record<string, number>, b: Record<string, number>): Record<string, number> => {
    const dates = [...new Set([...Object.keys(a), ...Object.keys(b)])].sort();
    const out: Record<string, number> = {};
    let la: number | null = null, lb: number | null = null;
    for (const d of dates) {
      if (a[d] != null) la = a[d];
      if (b[d] != null) lb = b[d];
      if (la != null && lb != null) out[d] = +(la - lb).toFixed(4);
    }
    return out;
  };

  // Auction demand. Cover = bids received / amount accepted, aggregated across
  // every tenor auctioning that day. NOT the guide's offered/announced ratio —
  // announced amount is not stored — so it measures cover on what BB took.
  const coverOf = (isBill: boolean): Record<string, number> => {
    const agg: Record<string, { off: number; acc: number }> = {};
    for (const r of yh) {
      if ((r.security_type === "T_BILL") !== isBill) continue;
      const off = r.offered_bdt_crore, acc = r.accepted_bdt_crore;
      if (off == null || acc == null || acc <= 0) continue;
      const d = r.auction_date.slice(0, 10);
      (agg[d] ??= { off: 0, acc: 0 });
      agg[d].off += off; agg[d].acc += acc;
    }
    const out: Record<string, number> = {};
    for (const [d, v] of Object.entries(agg)) if (v.acc > 0) out[d] = +(v.off / v.acc).toFixed(4);
    return out;
  };

  // Issuance actually taken that day, across all G-sec tenors.
  const issuance = (isBill: boolean): Record<string, number> => {
    const agg: Record<string, number> = {};
    for (const r of yh) {
      if ((r.security_type === "T_BILL") !== isBill) continue;
      if (r.accepted_bdt_crore == null) continue;
      const d = r.auction_date.slice(0, 10);
      agg[d] = (agg[d] ?? 0) + r.accepted_bdt_crore;
    }
    return agg;
  };

  // Realised G-sec cash returning to the market. Restricted to dates already
  // past: beyond today these rows are projections, and correlating against a
  // projection measures the projection, not the market.
  const pastFlow = (k: "coupon_inflow_bdt_mill" | "principal_inflow_bdt_mill") =>
    mk(flows.filter((f) => f.flow_date <= today).map((f) => [f.flow_date, f[k]]));
  const rrOn = (type: string) => mk(rr.filter((r) => r.rate_type === type && r.product === "Overnight").map((r) => [r.trade_date, r.rate_pct]));
  const macroPts = (k: keyof MacroRow) => mk(macro.series.map((m) => [m.month + "-15", m[k] as number]));

  const variables: SeriesDef[] = [
    { key: "omo_net", label: "OMO Net Liquidity", group: "Liquidity", unit: "cr", points: omoNet },
    { key: "net_borrow", label: "Net Borrowing", group: "Liquidity", unit: "mn", points: mk(flows.filter((f) => f.flow_date <= today).map((f) => [f.flow_date, f.net_borrowing_bdt_mill])) },
    { key: "call_war", label: "Call O/N WAR", group: "Rates", unit: "%", points: mk(cm.daily_summary.map((r) => [r.trade_date, r.overnight_wavg_rate])) },
    { key: "call_vol", label: "Call Volume", group: "Rates", unit: "cr", points: mk(cm.daily_summary.map((r) => [r.trade_date, r.total_volume_crore])) },
    { key: "dommr", label: "DOMMR O/N", group: "Rates", unit: "%", points: rrOn("DOMMR") },
    { key: "bofr", label: "BOFR O/N", group: "Rates", unit: "%", points: rrOn("BOFR") },
    // ── G-sec: every auctioned tenor, not a sample of four ──────────────
    { key: "y91", label: "91D T-Bill", group: "G-Sec Yields", unit: "%", points: yTenor("91D") },
    { key: "y182", label: "182D T-Bill", group: "G-Sec Yields", unit: "%", points: yTenor("182D") },
    { key: "y364", label: "364D T-Bill", group: "G-Sec Yields", unit: "%", points: yTenor("364D") },
    { key: "y2y", label: "2Y T-Bond", group: "G-Sec Yields", unit: "%", points: yTenor("2Y") },
    { key: "y5y", label: "5Y T-Bond", group: "G-Sec Yields", unit: "%", points: yTenor("5Y") },
    { key: "y10y", label: "10Y T-Bond", group: "G-Sec Yields", unit: "%", points: yTenor("10Y") },
    { key: "y15y", label: "15Y T-Bond", group: "G-Sec Yields", unit: "%", points: yTenor("15Y") },
    { key: "y20y", label: "20Y T-Bond", group: "G-Sec Yields", unit: "%", points: yTenor("20Y") },
    { key: "yfrtb", label: "3Y FRTB", group: "G-Sec Yields", unit: "%", points: yTenor("3Y_FRTB") },

    // ── G-sec curve shape ───────────────────────────────────────────────
    { key: "s_2s10s", label: "2s10s (10Y−2Y)", group: "G-Sec Curve", unit: "pp", points: spread(yTenor("10Y"), yTenor("2Y")) },
    { key: "s_91_10y", label: "91D→10Y slope", group: "G-Sec Curve", unit: "pp", points: spread(yTenor("10Y"), yTenor("91D")) },
    { key: "s_bill", label: "Bill slope (364D−91D)", group: "G-Sec Curve", unit: "pp", points: spread(yTenor("364D"), yTenor("91D")) },
    { key: "s_long", label: "Long end (20Y−10Y)", group: "G-Sec Curve", unit: "pp", points: spread(yTenor("20Y"), yTenor("10Y")) },

    // ── G-sec auction demand & supply ───────────────────────────────────
    { key: "cover_bill", label: "Bill cover ratio", group: "G-Sec Auction", unit: "x", points: coverOf(true) },
    { key: "cover_bond", label: "Bond cover ratio", group: "G-Sec Auction", unit: "x", points: coverOf(false) },
    { key: "iss_bill", label: "Bill issuance", group: "G-Sec Auction", unit: "cr", points: issuance(true) },
    { key: "iss_bond", label: "Bond issuance", group: "G-Sec Auction", unit: "cr", points: issuance(false) },

    // ── G-sec cash returning to the market ──────────────────────────────
    { key: "cf_coupon", label: "Coupon inflow", group: "G-Sec Cashflows", unit: "mn", points: pastFlow("coupon_inflow_bdt_mill") },
    { key: "cf_principal", label: "Maturity inflow", group: "G-Sec Cashflows", unit: "mn", points: pastFlow("principal_inflow_bdt_mill") },
    { key: "fx", label: "USD / BDT", group: "FX & Macro", unit: "৳", points: mk(fx.map((r) => [r.auction_date, r.weighted_avg_rate])) },
    { key: "reserves", label: "FX Reserves", group: "FX & Macro", unit: "$bn", points: macroPts("gross_reserves_usd_bn") },
    { key: "remit", label: "Remittance", group: "FX & Macro", unit: "$mn", points: macroPts("remittance_usd_mn") },
  ].filter((v) => Object.keys(v.points).length > 1);

  const allDates = variables.flatMap((v) => Object.keys(v.points)).sort();
  const min = allDates[0] ?? "2024-01-01";
  const max = allDates[allDates.length - 1] ?? today;

  return (
    <>
      <div style={{ marginBottom: "var(--gap)" }}><Freshness updated={fresh.callmoney} /></div>
      <ExploreTool variables={variables} min={min} max={max} />
      <RelatedLinks items={[
        { href: "/omo", label: "OMO Operations", why: "the liquidity side" },
        { href: "/callmoney", label: "Call Money", why: "the rate side" },
        { href: "/yields", label: "Yields", why: "the term structure" },
      ]} />
    </>
  );
}
