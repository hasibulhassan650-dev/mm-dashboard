import { api } from "@/lib/api";
import Link from "next/link";
import { fmtDate, fmtCrore, fmtRate } from "@/lib/format";
import { Panel } from "@/components/terminal/ui";
import LiquidityLadderChart from "@/components/LiquidityLadderChart";
import DownloadButton from "@/components/DownloadButton";
import Freshness from "@/components/Freshness";

export const revalidate = 300;

export default async function ForecastPage() {
  const [forecast, cm, policy, fresh] = await Promise.all([
    api.flowsForecast(28),
    api.callmoney(14).catch(() => null),
    api.policy(),
    api.freshness(),
  ]);
  const days = forecast.days;

  const next7 = days.slice(0, 7);
  const net7 = next7.reduce((s, d) => s + d.net_crore, 0);
  const active = days.filter(d =>
    d.net_crore !== 0 || d.omo_return_crore > 0 || d.omo_repay_crore > 0 ||
    d.govt_inflow_crore > 0 || d.auction_out_crore > 0);
  const biggestDrain = [...days].sort((a, b) => a.net_crore - b.net_crore)[0];
  const biggestInject = [...days].sort((a, b) => b.net_crore - a.net_crore)[0];

  const war = cm?.daily_summary?.at(-1)?.overnight_wavg_rate ?? null;
  const cor = policy.current;
  const corridorPos = war != null && cor?.sdf != null && cor?.slf != null
    ? Math.round(((war - cor.sdf) / (cor.slf - cor.sdf)) * 100) : null;

  const pressure = net7 < 0
    ? { label: "DRAINING — upward pressure on call rates", color: "var(--warn)" }
    : { label: "FLUSH — downward pressure on call rates", color: "var(--info)" };

  // Auction visibility: beyond the last ingested auction event, auction
  // outflows are UNKNOWN — say so, never show them as zero.
  const windowEnd = days.at(-1)?.date ?? null;
  const horizon = forecast.auction_horizon ?? null;
  const auctionsBlind = windowEnd != null && (horizon == null || horizon < windowEnd);

  return (
    <>
      <div style={{ marginBottom: "var(--gap)" }}><Freshness updated={fresh.flows} /></div>

      {auctionsBlind && (
        <div style={{
          border: "1px solid var(--warn)", borderRadius: "var(--radius-sm)",
          padding: "8px 12px", marginBottom: "var(--gap)", fontSize: 12.5,
          color: "var(--warn)", background: "color-mix(in oklab, var(--warn) 10%, transparent)",
        }}>
          ⚠ AUCTION DATA INCOMPLETE — last known auction settlement is {horizon ? fmtDate(horizon) : "none on record"}.
          Days after that show NO auction outflow because the calendar isn&apos;t ingested yet, not because none is scheduled.
          Net figures on those days overstate liquidity. BB auctions T-bills nearly every week.
        </div>
      )}

      <div className="kpi-strip" style={{ gridTemplateColumns: "repeat(4,1fr)" }}>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">Call O/N WAR</span></div>
          <div className="kpi-val"><span className="kpi-num">{war != null ? fmtRate(war, 2) : "—"}</span><span className="kpi-unit">%</span></div>
          <div className="kpi-sub">{corridorPos != null ? `${corridorPos}% up the ${cor?.sdf}–${cor?.slf} corridor` : "corridor n/a"}</div>
        </div>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">Next 7d Known Net</span></div>
          <div className="kpi-val"><span className="kpi-num" style={{ color: net7 < 0 ? "var(--warn)" : "var(--info)" }}>{fmtCrore(net7)}</span><span className="kpi-unit">cr</span></div>
          <div className="kpi-sub" style={{ color: pressure.color }}>{pressure.label}</div>
        </div>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">Tightest Day</span></div>
          <div className="kpi-val"><span className="kpi-num neg">{biggestDrain ? fmtCrore(biggestDrain.net_crore) : "—"}</span><span className="kpi-unit">cr</span></div>
          <div className="kpi-sub">{biggestDrain ? `${fmtDate(biggestDrain.date)} — lend into it` : ""}</div>
        </div>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">Flushest Day</span></div>
          <div className="kpi-val"><span className="kpi-num pos">{biggestInject ? fmtCrore(biggestInject.net_crore) : "—"}</span><span className="kpi-unit">cr</span></div>
          <div className="kpi-sub">{biggestInject ? `${fmtDate(biggestInject.date)} — cheap borrowing` : ""}</div>
        </div>
      </div>

      <div className="grid12">
        <Panel title="Known Liquidity Ladder — Next 4 Weeks" span={12}
          sub="dated flows already contracted today (BDT crore) · excludes future BB operations, which react to this">
          <LiquidityLadderChart data={days} />
        </Panel>

        <Panel title="Day-by-Day Ladder" sub="only days with flows · click a date for the G-sec drilldown" span={12} pad={false}
          right={<DownloadButton data={days} filename="liquidity_forecast" />}>
          <div className="table-wrap" style={{ maxHeight: 480, overflowY: "auto" }}>
            <table className="dt">
              <thead><tr>
                <th>Date</th><th>Day</th>
                <th className="r">OMO Return (cr)</th><th className="r">OMO Repay (cr)</th>
                <th className="r">Govt Inflow (cr)</th><th className="r">Auction Out (cr)</th>
                <th className="r">Net (cr)</th><th className="r">Cumulative (cr)</th><th>OMO Detail</th>
              </tr></thead>
              <tbody>
                {active.map((d, i) => (
                  <tr key={i}>
                    <td><Link href={`/drilldown?date=${d.date}`} style={{ color: "var(--accent)" }}>{fmtDate(d.date)}</Link></td>
                    <td>{d.weekday}</td>
                    <td className="r mono pos">{d.omo_return_crore ? fmtCrore(d.omo_return_crore) : ""}</td>
                    <td className="r mono neg">{d.omo_repay_crore ? fmtCrore(d.omo_repay_crore) : ""}</td>
                    <td className="r mono pos">{d.govt_inflow_crore ? fmtCrore(d.govt_inflow_crore) : ""}</td>
                    <td className="r mono neg">{d.auction_out_crore ? fmtCrore(d.auction_out_crore) : ""}</td>
                    <td className="r mono" style={{ color: d.net_crore < 0 ? "var(--warn)" : "var(--info)", fontWeight: 600 }}>{fmtCrore(d.net_crore)}</td>
                    <td className="r mono">{fmtCrore(d.cum_net_crore)}</td>
                    <td style={{ fontSize: 11, color: "var(--fg-mute)" }}>
                      {d.omo_items.map(it => `${it.instrument} ${fmtCrore(it.crore)}`).join(" · ")}
                    </td>
                  </tr>
                ))}
                {active.length === 0 && (
                  <tr><td colSpan={9} style={{ textAlign: "center", color: "var(--fg-mute)", padding: 16 }}>
                    No dated flows in the window — backend /api/flows/forecast not deployed yet or window empty.
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>
        </Panel>

        <Panel title="How to Read This" span={12}>
          <div style={{ fontSize: 12.5, lineHeight: 1.7, color: "var(--fg-mute)", maxWidth: 900 }}>
            <p><b style={{ color: "var(--fg)" }}>This ladder is what the market already knows.</b> Every bar is a contracted,
            dated cash flow: OMO repos/AR/IBLF maturing (banks must repay BB — drain), SDF maturing (cash returns — inject),
            G-sec coupons and maturities (inject), and auction settlements (drain).</p>
            <p><b style={{ color: "var(--fg)" }}>Trading it:</b> a deeply negative day means the system needs cash — call rates
            get bid toward the SLF ceiling, so position as a <b>lender</b> going in. A flush day pushes rates toward the SDF
            floor — fund yourself there as a <b>borrower</b>. BB usually offsets big imbalances with new OMO the same day;
            the gap between this ladder and what BB actually does is where the rate moves.</p>
          </div>
        </Panel>
      </div>
    </>
  );
}
