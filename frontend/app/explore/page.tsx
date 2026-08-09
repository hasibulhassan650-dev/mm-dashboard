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
  const rrOn = (type: string) => mk(rr.filter((r) => r.rate_type === type && r.product === "Overnight").map((r) => [r.trade_date, r.rate_pct]));
  const macroPts = (k: keyof MacroRow) => mk(macro.series.map((m) => [m.month + "-15", m[k] as number]));

  const variables: SeriesDef[] = [
    { key: "omo_net", label: "OMO Net Liquidity", group: "Liquidity", unit: "cr", points: omoNet },
    { key: "net_borrow", label: "Net Borrowing", group: "Liquidity", unit: "mn", points: mk(flows.filter((f) => f.flow_date <= today).map((f) => [f.flow_date, f.net_borrowing_bdt_mill])) },
    { key: "call_war", label: "Call O/N WAR", group: "Rates", unit: "%", points: mk(cm.daily_summary.map((r) => [r.trade_date, r.overnight_wavg_rate])) },
    { key: "call_vol", label: "Call Volume", group: "Rates", unit: "cr", points: mk(cm.daily_summary.map((r) => [r.trade_date, r.total_volume_crore])) },
    { key: "dommr", label: "DOMMR O/N", group: "Rates", unit: "%", points: rrOn("DOMMR") },
    { key: "bofr", label: "BOFR O/N", group: "Rates", unit: "%", points: rrOn("BOFR") },
    { key: "y91", label: "91D T-Bill", group: "Yields", unit: "%", points: yTenor("91D") },
    { key: "y364", label: "364D T-Bill", group: "Yields", unit: "%", points: yTenor("364D") },
    { key: "y2y", label: "2Y T-Bond", group: "Yields", unit: "%", points: yTenor("2Y") },
    { key: "y10y", label: "10Y T-Bond", group: "Yields", unit: "%", points: yTenor("10Y") },
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
