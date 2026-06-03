import { api } from "@/lib/api";
import { callMoneyCombo } from "@/lib/terminal";
import { fmtDate, fmtPct } from "@/lib/format";
import { Panel } from "@/components/terminal/ui";
import { ComboChart } from "@/components/terminal/charts";
import CallMoneyChart from "@/components/CallMoneyChart";
import PeriodSelector from "@/components/PeriodSelector";
import DownloadButton from "@/components/DownloadButton";
import Freshness from "@/components/Freshness";
import InfoTip from "@/components/InfoTip";

export const revalidate = 300;

const PERIODS = [
  { label: "30d", days: 30 }, { label: "90d", days: 90 }, { label: "180d", days: 180 },
  { label: "1y", days: 365 }, { label: "All", days: 0 },
];

export default async function CallMoneyPage({ searchParams }: { searchParams: Promise<{ days?: string }> }) {
  const days = parseInt((await searchParams).days ?? "90", 10);
  const [data, fresh, corridor] = await Promise.all([api.callmoney(days), api.freshness(), api.policy()]);
  const summary = [...data.daily_summary].sort((a, b) => a.trade_date.localeCompare(b.trade_date));
  const latest = data.latest_breakdown;
  const cur = corridor.current;
  const combo = callMoneyCombo(data);

  const lastRow = summary.at(-1), prevRow = summary.at(-2);
  const rateNow = lastRow?.overnight_wavg_rate ?? null;
  const rateDelta = rateNow != null && prevRow?.overnight_wavg_rate != null ? rateNow - prevRow.overnight_wavg_rate : null;
  const products = [...new Set(latest.map((r) => r.product))].sort();

  return (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, marginBottom: "var(--gap)", flexWrap: "wrap" }}>
        <Freshness updated={fresh.callmoney} />
        <PeriodSelector periods={PERIODS} current={days} />
      </div>

      <div className="kpi-strip" style={{ gridTemplateColumns: "repeat(4,1fr)" }}>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">Overnight WAR</span>{rateDelta != null && <span className={"delta " + (rateDelta <= 0 ? "pos" : "neg")}><svg viewBox="0 0 12 12" width="9" height="9" style={{ transform: rateDelta >= 0 ? "none" : "rotate(180deg)" }}><path d="M6 2 L10 8 L2 8 Z" fill="currentColor" /></svg>{Math.abs(rateDelta).toFixed(2)}</span>}</div>
          <div className="kpi-val"><span className="kpi-num">{rateNow != null ? rateNow.toFixed(2) : "—"}</span><span className="kpi-unit">%</span></div>
          <div className="kpi-sub">weighted avg rate</div>
        </div>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">O/N High / Low</span></div>
          <div className="kpi-val"><span className="kpi-num">{lastRow?.overnight_high != null && lastRow?.overnight_low != null ? `${lastRow.overnight_high.toFixed(2)}/${lastRow.overnight_low.toFixed(2)}` : "—"}</span><span className="kpi-unit">%</span></div>
          <div className="kpi-sub">{lastRow?.overnight_high != null && lastRow?.overnight_low != null ? `spread ${(lastRow.overnight_high - lastRow.overnight_low).toFixed(2)}pp` : "intraday range"}</div>
        </div>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">O/N Volume</span></div>
          <div className="kpi-val"><span className="kpi-cur">৳</span><span className="kpi-num">{lastRow?.overnight_volume_crore != null ? Math.round(lastRow.overnight_volume_crore).toLocaleString() : "—"}</span><span className="kpi-unit">cr</span></div>
          <div className="kpi-sub">{lastRow?.overnight_deals ?? "—"} deals</div>
        </div>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">Total Volume</span></div>
          <div className="kpi-val"><span className="kpi-cur">৳</span><span className="kpi-num">{lastRow?.total_volume_crore != null ? Math.round(lastRow.total_volume_crore).toLocaleString() : "—"}</span><span className="kpi-unit">cr</span></div>
          <div className="kpi-sub">{lastRow?.total_deals ?? "—"} deals · all tenors</div>
        </div>
      </div>

      {cur && (
        <div style={{ fontSize: 11.5, color: "var(--warn)", margin: "0 2px 8px" }}>
          ⚠ Policy corridor — illustrative / pending verification against BB MPC circulars (assumed eff. {fmtDate(cur.effective_date)}).
        </div>
      )}
      {cur && (
        <div className="kpi-strip" style={{ gridTemplateColumns: "repeat(4,1fr)" }}>
          <div className="kpi"><div className="kpi-top"><span className="kpi-label">SLF — Ceiling<InfoTip term="SLF" /></span></div><div className="kpi-val"><span className="kpi-num neg">{fmtPct(cur.slf)}</span></div><div className="kpi-sub">standing lending</div></div>
          <div className="kpi"><div className="kpi-top"><span className="kpi-label">Repo — Policy</span></div><div className="kpi-val"><span className="kpi-num" style={{ color: "var(--accent)" }}>{fmtPct(cur.repo)}</span></div><div className="kpi-sub">policy anchor</div></div>
          <div className="kpi"><div className="kpi-top"><span className="kpi-label">SDF — Floor<InfoTip term="SDF" /></span></div><div className="kpi-val"><span className="kpi-num" style={{ color: "var(--info)" }}>{fmtPct(cur.sdf)}</span></div><div className="kpi-sub">standing deposit</div></div>
          <div className="kpi"><div className="kpi-top"><span className="kpi-label">Bank Rate</span></div><div className="kpi-val"><span className="kpi-num">{fmtPct(cur.bank_rate)}</span></div><div className="kpi-sub">eff. {fmtDate(cur.effective_date)}</div></div>
        </div>
      )}

      <div className="grid12">
        <Panel title="Overnight Call Rate vs Policy Corridor" sub="weighted-average rate inside the SDF–SLF band" span={12}>
          <CallMoneyChart data={summary} corridor={cur ? { repo: cur.repo, sdf: cur.sdf, slf: cur.slf } : undefined} />
        </Panel>
        <Panel title="Rate & Volume" sub="WAR (line) · turnover (bars)" span={12}>
          <ComboChart data={combo} height={300} />
        </Panel>

        {latest.length > 0 && products.map((product) => {
          const rows = latest.filter((r) => r.product === product).sort((a, b) => (a.maturity_days ?? 0) - (b.maturity_days ?? 0));
          const vol = rows.reduce((s, r) => s + (r.amount_crore ?? 0), 0);
          return (
            <Panel key={product} title={product} sub={`${Math.round(vol).toLocaleString()} cr · latest day`} span={4} pad={false}>
              <div className="table-wrap">
                <table className="dt">
                  <thead><tr><th>Tenor</th><th className="r">Avg</th><th className="r">High</th><th className="r">Low</th><th className="r">Vol</th></tr></thead>
                  <tbody>
                    {rows.map((r, i) => (
                      <tr key={i}>
                        <td className="mono">{r.maturity_days != null ? `${r.maturity_days}D` : "—"}</td>
                        <td className="r mono">{r.average_rate_pct?.toFixed(2) ?? "—"}</td>
                        <td className="r mono neg">{r.highest_rate_pct?.toFixed(2) ?? "—"}</td>
                        <td className="r mono pos">{r.lowest_rate_pct?.toFixed(2) ?? "—"}</td>
                        <td className="r mono">{r.amount_crore?.toLocaleString() ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Panel>
          );
        })}

        <Panel title="Daily Summary" sub={`${summary.length} days`} span={12} pad={false}
          right={<DownloadButton data={summary} filename="callmoney_daily" />}>
          <div className="table-wrap" style={{ maxHeight: 420, overflowY: "auto" }}>
            <table className="dt">
              <thead><tr><th>Date</th><th className="r">O/N WAR</th><th className="r">High</th><th className="r">Low</th><th className="r">O/N Vol (cr)</th><th className="r">Deals</th><th className="r">Total Vol (cr)</th></tr></thead>
              <tbody>
                {[...summary].reverse().map((r, i) => (
                  <tr key={i}>
                    <td>{fmtDate(r.trade_date)}</td>
                    <td className="r mono">{r.overnight_wavg_rate?.toFixed(2) ?? "—"}%</td>
                    <td className="r mono neg">{r.overnight_high?.toFixed(2) ?? "—"}%</td>
                    <td className="r mono pos">{r.overnight_low?.toFixed(2) ?? "—"}%</td>
                    <td className="r mono">{r.overnight_volume_crore?.toLocaleString() ?? "—"}</td>
                    <td className="r mono">{r.overnight_deals ?? "—"}</td>
                    <td className="r mono">{r.total_volume_crore?.toLocaleString() ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      </div>
    </>
  );
}
