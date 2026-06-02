import { api } from "@/lib/api";
import { fmtMonth, fmtDate, fmtPct } from "@/lib/format";
import { Panel } from "@/components/terminal/ui";
import SeriesLineChart from "@/components/SeriesLineChart";
import DownloadButton from "@/components/DownloadButton";
import InfoTip from "@/components/InfoTip";

export const revalidate = 300;

export default async function MonetaryPage() {
  const data = await api.monetary();
  const rows = data.monthly;
  const latest = data.latest;
  const rr = data.reserve_requirements.current;
  const cd = rows.map((r) => ({ ...r, m: fmtMonth(r.month) }));
  const spread = latest?.wavg_lending != null && latest?.wavg_deposit != null ? latest.wavg_lending - latest.wavg_deposit : null;

  return (
    <>
      <div className="kpi-strip" style={{ gridTemplateColumns: "repeat(4,1fr)" }}>
        <div className="kpi"><div className="kpi-top"><span className="kpi-label">CPI p2p<InfoTip term="CPI (point-to-point)" /></span></div><div className="kpi-val"><span className="kpi-num" style={{ color: "var(--warn)" }}>{latest?.cpi_p2p != null ? latest.cpi_p2p.toFixed(2) : "—"}</span><span className="kpi-unit">%</span></div><div className="kpi-sub">12-mo avg {fmtPct(latest?.cpi_12mo_avg)}</div></div>
        <div className="kpi"><div className="kpi-top"><span className="kpi-label">Pvt Credit Growth<InfoTip term="Private-sector credit" /></span></div><div className="kpi-val"><span className="kpi-num" style={{ color: "var(--info)" }}>{latest?.private_credit_growth != null ? latest.private_credit_growth.toFixed(2) : "—"}</span><span className="kpi-unit">%</span></div><div className="kpi-sub">M2 {fmtPct(latest?.m2_growth)}</div></div>
        <div className="kpi"><div className="kpi-top"><span className="kpi-label">Lending−Deposit Spread<InfoTip term="Lending-deposit spread" /></span></div><div className="kpi-val"><span className="kpi-num" style={{ color: "#a78bfa" }}>{spread != null ? spread.toFixed(2) : "—"}</span><span className="kpi-unit">%</span></div><div className="kpi-sub">L {fmtPct(latest?.wavg_lending)} · D {fmtPct(latest?.wavg_deposit)}</div></div>
        <div className="kpi"><div className="kpi-top"><span className="kpi-label">CRR / SLR<InfoTip term="CRR" /><InfoTip term="SLR" /></span></div><div className="kpi-val"><span className="kpi-num" style={{ color: "var(--accent)" }}>{fmtPct(rr?.crr)}<span style={{ color: "var(--fg-mute)" }}>/</span>{fmtPct(rr?.slr)}</span></div><div className="kpi-sub">{rr ? `eff. ${fmtDate(rr.effective_date)}` : "—"}</div></div>
      </div>

      <div className="grid12">
        <Panel title="CPI Inflation" sub="point-to-point vs 12-month average" span={6}>
          <SeriesLineChart data={cd} xKey="m" unit="%" series={[
            { key: "cpi_p2p", label: "Point-to-point", color: "#fb923c" },
            { key: "cpi_12mo_avg", label: "12-mo average", color: "#f59e0b", dashed: true },
          ]} />
        </Panel>
        <Panel title="Monetary Aggregates" sub="YoY growth" span={6}>
          <SeriesLineChart data={cd} xKey="m" unit="%" series={[
            { key: "m2_growth", label: "Broad money (M2)", color: "#38bdf8" },
            { key: "reserve_money_growth", label: "Reserve money", color: "#a78bfa" },
            { key: "private_credit_growth", label: "Private credit", color: "#34d399" },
          ]} />
        </Panel>
        <Panel title="Banking-System Rates" sub="weighted-average lending vs deposit" span={12}>
          <SeriesLineChart data={cd} xKey="m" unit="%" series={[
            { key: "wavg_lending", label: "Lending rate", color: "#f87171" },
            { key: "wavg_deposit", label: "Deposit rate", color: "#4ade80" },
          ]} />
        </Panel>
        <Panel title="Monthly History" sub={`${rows.length} months`} span={12} pad={false}
          right={<DownloadButton data={rows} filename="monetary_indicators" />}>
          <div className="table-wrap" style={{ maxHeight: 420, overflowY: "auto" }}>
            <table className="dt">
              <thead><tr><th>Month</th><th className="r">CPI p2p</th><th className="r">CPI 12m</th><th className="r">M2</th><th className="r">Pvt credit</th><th className="r">Deposit</th><th className="r">Lending</th></tr></thead>
              <tbody>
                {[...rows].reverse().map((r, i) => (
                  <tr key={i}>
                    <td>{fmtMonth(r.month)}</td>
                    <td className="r mono" style={{ color: "var(--warn)" }}>{fmtPct(r.cpi_p2p)}</td>
                    <td className="r mono">{fmtPct(r.cpi_12mo_avg)}</td>
                    <td className="r mono" style={{ color: "var(--info)" }}>{fmtPct(r.m2_growth)}</td>
                    <td className="r mono pos">{fmtPct(r.private_credit_growth)}</td>
                    <td className="r mono">{fmtPct(r.wavg_deposit)}</td>
                    <td className="r mono">{fmtPct(r.wavg_lending)}</td>
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
