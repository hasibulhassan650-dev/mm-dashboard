import { api } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { Panel } from "@/components/terminal/ui";
import RefRateChart from "@/components/RefRateChart";
import PeriodSelector from "@/components/PeriodSelector";
import DownloadButton from "@/components/DownloadButton";
import Freshness from "@/components/Freshness";
import InfoTip from "@/components/InfoTip";
import type { RefRateRow } from "@/lib/api";

export const revalidate = 300;

const PRODUCTS = ["Overnight", "1W", "1M", "3M"];
const PERIODS = [
  { label: "30d", days: 30 }, { label: "90d", days: 90 }, { label: "180d", days: 180 },
  { label: "1y", days: 365 }, { label: "All", days: 0 },
];

export default async function RefRatePage({ searchParams }: { searchParams: Promise<{ days?: string }> }) {
  const days = parseInt((await searchParams).days ?? "90", 10);
  const [rows, fresh, corridor] = await Promise.all([api.refrate(days).catch(() => [] as RefRateRow[]), api.freshness(), api.policy()]);
  const repoRate = corridor.current?.repo ?? null;

  const dates = [...new Set(rows.map((r) => r.trade_date))].sort().reverse();
  const latestDate = dates[0] ?? null, prevDate = dates[1] ?? null;
  const at = (type: string, prod: string, date: string | null) => rows.find((r) => r.rate_type === type && r.product === prod && r.trade_date === date)?.rate_pct ?? null;
  const delta = (type: string) => { const a = at(type, "Overnight", latestDate), b = at(type, "Overnight", prevDate); return a != null && b != null ? a - b : null; };

  const miniList = (type: string, color: string) => (
    <div style={{ fontSize: 11.5, fontFamily: "var(--mono)" }}>
      {["1W", "1M", "3M"].map((p) => (
        <div key={p} style={{ display: "flex", justifyContent: "space-between", padding: "2px 0" }}>
          <span style={{ color: "var(--fg-mute)" }}>{p}</span>
          <span style={{ color }}>{at(type, p, latestDate)?.toFixed(2) ?? "—"}%</span>
        </div>
      ))}
    </div>
  );

  const dom = delta("DOMMR"), bof = delta("BOFR");

  return (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, marginBottom: "var(--gap)", flexWrap: "wrap" }}>
        <Freshness updated={fresh.refrate} />
        <PeriodSelector periods={PERIODS} current={days} />
      </div>

      {rows.length === 0 && (
        <div className="panel" style={{ padding: 40, textAlign: "center", color: "var(--fg-mute)" }}>No reference-rate data for this period. Try a wider window.</div>
      )}

      <div className="kpi-strip" style={{ gridTemplateColumns: "repeat(4,1fr)" }}>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">DOMMR · O/N<InfoTip term="DOMMR" /></span>{dom != null && <span className={"delta " + (dom <= 0 ? "pos" : "neg")}><svg viewBox="0 0 12 12" width="9" height="9" style={{ transform: dom >= 0 ? "none" : "rotate(180deg)" }}><path d="M6 2 L10 8 L2 8 Z" fill="currentColor" /></svg>{Math.abs(dom).toFixed(2)}</span>}</div>
          <div className="kpi-val"><span className="kpi-num" style={{ color: "var(--info)" }}>{at("DOMMR", "Overnight", latestDate)?.toFixed(2) ?? "—"}</span><span className="kpi-unit">%</span></div>
          <div className="kpi-sub">domestic money market</div>
        </div>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">BOFR · O/N<InfoTip term="BOFR" /></span>{bof != null && <span className={"delta " + (bof <= 0 ? "pos" : "neg")}><svg viewBox="0 0 12 12" width="9" height="9" style={{ transform: bof >= 0 ? "none" : "rotate(180deg)" }}><path d="M6 2 L10 8 L2 8 Z" fill="currentColor" /></svg>{Math.abs(bof).toFixed(2)}</span>}</div>
          <div className="kpi-val"><span className="kpi-num" style={{ color: "#a78bfa" }}>{at("BOFR", "Overnight", latestDate)?.toFixed(2) ?? "—"}</span><span className="kpi-unit">%</span></div>
          <div className="kpi-sub">bank overnight</div>
        </div>
        <div className="kpi"><div className="kpi-top"><span className="kpi-label">DOMMR — 1W/1M/3M</span></div>{miniList("DOMMR", "var(--info)")}</div>
        <div className="kpi"><div className="kpi-top"><span className="kpi-label">BOFR — 1W/1M/3M</span></div>{miniList("BOFR", "#a78bfa")}</div>
      </div>

      <div className="grid12">
        <Panel title="DOMMR — rate by product" sub="vs policy repo" span={6}><RefRateChart rows={rows} rateType="DOMMR" repoRate={repoRate} /></Panel>
        <Panel title="BOFR — rate by product" sub="bank overnight financing" span={6}><RefRateChart rows={rows} rateType="BOFR" repoRate={repoRate} /></Panel>

        {latestDate && (["DOMMR", "BOFR"] as const).map((rtype) => {
          const latest = rows.filter((r) => r.trade_date === latestDate && r.rate_type === rtype).sort((a, b) => PRODUCTS.indexOf(a.product) - PRODUCTS.indexOf(b.product));
          return (
            <Panel key={rtype} title={`${rtype} — ${fmtDate(latestDate)}`} sub="latest day breakdown" span={6} pad={false}>
              <div className="table-wrap">
                <table className="dt">
                  <thead><tr><th>Product</th><th className="r">Rate</th><th className="r">Amount (cr)</th><th className="r">Deals</th></tr></thead>
                  <tbody>
                    {latest.map((r, i) => (
                      <tr key={i}><td>{r.product}</td><td className="r mono" style={{ color: rtype === "DOMMR" ? "var(--info)" : "#a78bfa" }}>{r.rate_pct?.toFixed(2) ?? "—"}%</td><td className="r mono">{r.amount_crore?.toLocaleString() ?? "—"}</td><td className="r mono">{r.num_deals ?? "—"}</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Panel>
          );
        })}

        {rows.length > 0 && (
          <Panel title="Full History" sub={`${rows.length} observations`} span={12} pad={false}
            right={<DownloadButton data={rows} filename="reference_rates" />}>
            <div className="table-wrap" style={{ maxHeight: 420, overflowY: "auto" }}>
              <table className="dt">
                <thead><tr><th>Date</th><th>Type</th><th>Product</th><th className="r">Rate</th><th className="r">Amount (cr)</th><th className="r">Deals</th></tr></thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr key={i}>
                      <td>{fmtDate(r.trade_date)}</td>
                      <td><span style={{ color: r.rate_type === "DOMMR" ? "var(--info)" : "#a78bfa", fontWeight: 600, fontSize: 11 }}>{r.rate_type}</span></td>
                      <td>{r.product}</td>
                      <td className="r mono">{r.rate_pct?.toFixed(2) ?? "—"}%</td>
                      <td className="r mono">{r.amount_crore?.toLocaleString() ?? "—"}</td>
                      <td className="r mono">{r.num_deals ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
        )}
      </div>
    </>
  );
}
