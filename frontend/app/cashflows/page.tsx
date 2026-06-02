import { api } from "@/lib/api";
import Link from "next/link";
import { fmtDate } from "@/lib/format";
import { Panel } from "@/components/terminal/ui";
import CashFlowChart from "@/components/CashFlowChart";
import DownloadButton from "@/components/DownloadButton";
import Freshness from "@/components/Freshness";

export const revalidate = 300;

export default async function CashFlowsPage() {
  const [flows, fresh] = await Promise.all([api.flows(6).catch(() => []), api.freshness()]);
  const today = new Date().toISOString().slice(0, 10);
  const todayRow = [...flows].reverse().find((r) => r.flow_date <= today) ?? flows[flows.length - 1];
  const upcoming = flows.filter((r) => r.flow_date >= today).slice(0, 30);
  const totalInflow = flows.reduce((s, r) => s + (r.total_inflow_bdt_mill ?? 0), 0);
  const totalOutflow = flows.reduce((s, r) => s + (r.auction_outflow_confirmed_mill ?? r.auction_outflow_planned_mill ?? 0), 0);
  const k = (mn: number) => (mn / 1000).toFixed(1);

  return (
    <>
      <div style={{ marginBottom: "var(--gap)" }}><Freshness updated={fresh.flows} /></div>

      {todayRow && (
        <div className="kpi-strip" style={{ gridTemplateColumns: "repeat(4,1fr)" }}>
          <div className="kpi"><div className="kpi-top"><span className="kpi-label">Today Inflow</span></div><div className="kpi-val"><span className="kpi-num pos">{k(todayRow.total_inflow_bdt_mill)}k</span><span className="kpi-unit">mn</span></div><div className="kpi-sub">{fmtDate(todayRow.flow_date)}</div></div>
          <div className="kpi"><div className="kpi-top"><span className="kpi-label">Today Outflow</span></div><div className="kpi-val"><span className="kpi-num neg">{k(todayRow.auction_outflow_confirmed_mill || todayRow.auction_outflow_planned_mill)}k</span><span className="kpi-unit">mn</span></div><div className="kpi-sub">auction settlements</div></div>
          <div className="kpi"><div className="kpi-top"><span className="kpi-label">Net Borrowing</span></div><div className="kpi-val"><span className="kpi-num" style={{ color: todayRow.net_borrowing_bdt_mill > 0 ? "var(--warn)" : "var(--info)" }}>{k(todayRow.net_borrowing_bdt_mill)}k</span><span className="kpi-unit">mn</span></div><div className="kpi-sub">{todayRow.net_borrowing_bdt_mill > 0 ? "net borrower" : "net repayer"}</div></div>
          <div className="kpi"><div className="kpi-top"><span className="kpi-label">6M Total Inflow</span></div><div className="kpi-val"><span className="kpi-num">{(totalInflow / 1000).toFixed(0)}k</span><span className="kpi-unit">mn</span></div><div className="kpi-sub">vs {(totalOutflow / 1000).toFixed(0)}k outflow</div></div>
        </div>
      )}

      <div className="grid12">
        <Panel title="Cash Flow Timeline" sub="6 months history + 4 months ahead" span={12}>
          <CashFlowChart data={flows} />
        </Panel>
        <Panel title="Upcoming Events" sub="next 30 days · click a date to drill down" span={12} pad={false}
          right={<DownloadButton data={flows} filename="cash_flows" />}>
          <div className="table-wrap" style={{ maxHeight: 460, overflowY: "auto" }}>
            <table className="dt">
              <thead><tr><th>Date</th><th className="r">Maturity (mn)</th><th className="r">Coupon (mn)</th><th className="r">Inflow (mn)</th><th className="r">Auction Out (mn)</th><th className="r">Net (mn)</th><th>Status</th></tr></thead>
              <tbody>
                {upcoming.map((r, i) => {
                  const outflow = r.auction_outflow_confirmed_mill || r.auction_outflow_planned_mill;
                  const isToday = r.flow_date === today;
                  return (
                    <tr key={i} style={isToday ? { background: "var(--accent-soft)" } : undefined}>
                      <td><Link href={`/drilldown?date=${r.flow_date}`} style={{ color: "var(--accent)" }}>{fmtDate(r.flow_date)}{isToday && " ◀ today"}</Link></td>
                      <td className="r mono">{r.principal_inflow_bdt_mill.toLocaleString()}</td>
                      <td className="r mono">{r.coupon_inflow_bdt_mill.toLocaleString()}</td>
                      <td className="r mono">{r.total_inflow_bdt_mill.toLocaleString()}</td>
                      <td className="r mono neg">{outflow.toLocaleString()}</td>
                      <td className="r mono"><span className={r.net_borrowing_bdt_mill > 0 ? "neg" : "pos"}>{r.net_borrowing_bdt_mill > 0 ? "▲" : "▼"} {Math.abs(r.net_borrowing_bdt_mill).toLocaleString()}</span></td>
                      <td>{r.data_complete ? "✓" : <span style={{ color: "var(--warn)" }}>partial</span>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Panel>
      </div>
    </>
  );
}
