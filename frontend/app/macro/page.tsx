import { api } from "@/lib/api";
import { fmtMonth } from "@/lib/format";
import { Panel } from "@/components/terminal/ui";
import ReservesChart from "@/components/ReservesChart";
import RemittanceChart from "@/components/RemittanceChart";
import DownloadButton from "@/components/DownloadButton";
import InfoTip from "@/components/InfoTip";

export const revalidate = 300;

export default async function MacroPage() {
  const macro = await api.macro();
  const rows = macro.series;
  const latest = macro.latest;
  const prev = rows.length >= 2 ? rows[rows.length - 2] : null;
  const remDelta = latest?.remittance_usd_mn != null && prev?.remittance_usd_mn != null ? latest.remittance_usd_mn - prev.remittance_usd_mn : null;
  const remDeltaPct = remDelta != null && prev?.remittance_usd_mn ? (remDelta / prev.remittance_usd_mn) * 100 : null;

  return (
    <>
      <div className="kpi-strip" style={{ gridTemplateColumns: "repeat(4,1fr)" }}>
        <div className="kpi"><div className="kpi-top"><span className="kpi-label">Gross Reserves<InfoTip term="Gross reserves" /></span></div><div className="kpi-val"><span className="kpi-num" style={{ color: "var(--info)" }}>{latest?.gross_reserves_usd_bn?.toFixed(2) ?? "—"}</span><span className="kpi-unit">B$</span></div><div className="kpi-sub">{latest ? fmtMonth(latest.month) : ""}</div></div>
        <div className="kpi"><div className="kpi-top"><span className="kpi-label">Net (BPM6)<InfoTip term="Net reserves (BPM6)" /></span></div><div className="kpi-val"><span className="kpi-num" style={{ color: "var(--accent)" }}>{latest?.net_reserves_bpm6_usd_bn?.toFixed(2) ?? "—"}</span><span className="kpi-unit">B$</span></div><div className="kpi-sub">IMF basis</div></div>
        <div className="kpi"><div className="kpi-top"><span className="kpi-label">Remittance<InfoTip term="Remittance" /></span></div><div className="kpi-val"><span className="kpi-num" style={{ color: "#a78bfa" }}>{latest?.remittance_usd_mn?.toLocaleString() ?? "—"}</span><span className="kpi-unit">M$</span></div><div className="kpi-sub">{latest ? fmtMonth(latest.month) : ""}</div></div>
        <div className="kpi"><div className="kpi-top"><span className="kpi-label">Remittance MoM</span></div><div className="kpi-val"><span className={"kpi-num " + (remDeltaPct != null ? (remDeltaPct >= 0 ? "pos" : "neg") : "")}>{remDeltaPct != null ? `${remDeltaPct >= 0 ? "+" : ""}${remDeltaPct.toFixed(1)}` : "—"}</span><span className="kpi-unit">%</span></div><div className="kpi-sub">vs {prev ? fmtMonth(prev.month) : "—"}</div></div>
      </div>

      <div className="grid12">
        <Panel title="FX Reserves" sub="gross & net (BPM6) · USD bn" span={6}><ReservesChart data={rows} /></Panel>
        <Panel title="Wage-Earner Remittance" sub="monthly inflow · USD mn" span={6}><RemittanceChart data={rows} /></Panel>
        <Panel title="Monthly History" sub={`${rows.length} months`} span={12} pad={false}
          right={<DownloadButton data={rows} filename="reserves_remittances" />}>
          <div className="table-wrap" style={{ maxHeight: 420, overflowY: "auto" }}>
            <table className="dt">
              <thead><tr><th>Month</th><th className="r">Gross ($bn)</th><th className="r">Net BPM6 ($bn)</th><th className="r">Remittance ($mn)</th></tr></thead>
              <tbody>
                {[...rows].reverse().map((r, i) => (
                  <tr key={i}>
                    <td>{fmtMonth(r.month)}</td>
                    <td className="r mono" style={{ color: "var(--info)" }}>{r.gross_reserves_usd_bn?.toFixed(2) ?? "—"}</td>
                    <td className="r mono" style={{ color: "var(--accent)" }}>{r.net_reserves_bpm6_usd_bn?.toFixed(2) ?? "—"}</td>
                    <td className="r mono" style={{ color: "#a78bfa" }}>{r.remittance_usd_mn?.toLocaleString() ?? "—"}</td>
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
