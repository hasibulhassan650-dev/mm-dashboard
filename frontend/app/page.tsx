import { api } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { OMO_CATS, pivotOmo, omoNetSeries, buildCurve, tenorSeries, fxRateSeries } from "@/lib/terminal";
import OverviewView, { type OverviewData, type OmoAuctionRow, type SecAuctionRow } from "@/components/terminal/views/OverviewView";
import type { Kpi } from "@/components/terminal/ui";
import Freshness from "@/components/Freshness";

export const revalidate = 300;

const INST_LABEL: Record<string, string> = {
  CB_REPO: "Repo", AR: "Assured Repo", IBLF: "IBLF", SLF: "SLF", SDF: "SDF",
};

function delta(series: number[]): number | null {
  return series.length >= 2 ? +(series[series.length - 1] - series[series.length - 2]).toFixed(2) : null;
}

export default async function Home() {
  const [summary, curve, history, outstanding, txns, cm, fx, policy, macro, fresh] = await Promise.all([
    api.omoSummary().catch(() => []),
    api.yieldCurve().catch(() => []),
    api.yields(6).catch(() => []),
    api.omoOutstanding(60).catch(() => []),
    api.omoTransactions(20).catch(() => []),
    api.callmoney(45).catch(() => ({ daily_summary: [], latest_breakdown: [], latest_date: null })),
    api.fx(365).catch(() => []),
    api.policy(),
    api.macro(),
    api.freshness(),
  ]);

  const omoSeries = pivotOmo(outstanding);
  const netSeries = omoNetSeries(omoSeries);
  const cb = buildCurve(curve, history);

  // ---- KPIs ----
  const totalInjection = summary.filter((r) => r.direction === "INJECTION").reduce((s, r) => s + r.outstanding_bdt_crore, 0);
  const warSeries = [...cm.daily_summary].sort((a, b) => a.trade_date.localeCompare(b.trade_date)).map((d) => d.overnight_wavg_rate).filter((v): v is number => v != null);
  const tb91 = curve.find((r) => r.tenor_label === "91D");
  const tb10y = curve.find((r) => r.tenor_label === "10Y");
  const s91 = tenorSeries(history, "91D");
  const s10y = tenorSeries(history, "10Y");
  const fxS = fxRateSeries(fx);
  const resSeries = macro.series.map((m) => m.gross_reserves_usd_bn).filter((v): v is number => v != null).slice(-24);
  const lastFx = fxS.length ? fxS[fxS.length - 1] : null;
  const lastRes = macro.latest?.gross_reserves_usd_bn ?? null;

  const kpis: Kpi[] = [
    { id: "omo", label: "OMO Net Injection", value: `৳${(totalInjection / 1000).toFixed(1)}k`, unit: "cr", sub: "outstanding today", dir: "up", series: netSeries.slice(-30), tone: "accent" },
    { id: "war", label: "Call Money WAR", value: warSeries.length ? warSeries[warSeries.length - 1].toFixed(2) : "—", unit: "%", sub: "weighted avg rate", delta: delta(warSeries), dir: "up", series: warSeries.slice(-30) },
    { id: "tb91", label: "91D T-Bill", value: tb91 ? tb91.cutoff_yield_pct.toFixed(2) : "—", unit: "%", sub: tb91 ? `cut-off · ${fmtDate(tb91.auction_date)}` : "", delta: delta(s91), dir: "up", series: s91 },
    { id: "tb10y", label: "10Y T-Bond", value: tb10y ? tb10y.cutoff_yield_pct.toFixed(2) : "—", unit: "%", sub: tb10y ? `cut-off · ${fmtDate(tb10y.auction_date)}` : "", delta: delta(s10y), dir: "up", series: s10y },
    { id: "fx", label: "USD / BDT", value: lastFx != null ? lastFx.toFixed(2) : "—", sub: "auction wtd-avg", delta: delta(fxS), dir: "down", series: fxS },
    { id: "res", label: "FX Reserve", value: lastRes != null ? lastRes.toFixed(2) : "—", unit: "B$", sub: "gross · BB", delta: delta(resSeries), dir: "up", series: resSeries },
  ];

  // ---- corridor ----
  const pc = policy.current;
  const corridor = pc && pc.sdf != null && pc.repo != null && pc.slf != null
    ? { sdf: pc.sdf, repo: pc.repo, slf: pc.slf, war: warSeries.length ? warSeries[warSeries.length - 1] : pc.repo }
    : null;

  // ---- tables ----
  const omoAuctions: OmoAuctionRow[] = txns.slice(0, 8).map((t) => ({
    date: fmtDate(t.transaction_date), inst: INST_LABEL[t.instrument] || t.instrument,
    tenor: t.tenor_label, accepted: Math.round(t.accepted_bdt_crore), rate: t.rate_pct, direction: t.direction,
  }));
  const secAuctions: SecAuctionRow[] = [...curve]
    .sort((a, b) => a.tenor_years - b.tenor_years)
    .filter((r) => (r.accepted_bdt_crore ?? 0) > 0)
    .slice(0, 6)
    .map((r) => ({
      sec: `${r.tenor_label} ${r.security_type === "T_BILL" ? "T-Bill" : "T-Bond"}`,
      accepted: Math.round(r.accepted_bdt_crore ?? 0),
      cutoff: r.cutoff_yield_pct,
      cover: r.offered_bdt_crore && r.accepted_bdt_crore ? +(r.offered_bdt_crore / r.accepted_bdt_crore).toFixed(2) : null,
    }));

  const data: OverviewData = {
    kpis, tenors: cb.tenors, today: cb.today, weekAgo: cb.weekAgo, monthAgo: cb.monthAgo,
    omoSeries, omoCats: OMO_CATS, corridor, omoAuctions, secAuctions,
  };
  return (
    <>
      <div style={{ marginBottom: "var(--gap)" }}><Freshness updated={fresh.omo} /></div>
      <OverviewView d={data} />
    </>
  );
}
