import { api } from "@/lib/api";
import SeriesLineChart from "@/components/SeriesLineChart";
import DownloadButton from "@/components/DownloadButton";
import InfoTip from "@/components/InfoTip";
import { fmtMonth, fmtDate, fmtPct } from "@/lib/format";

export const revalidate = 300;

export default async function MonetaryPage() {
  const data = await api.monetary();
  const rows = data.monthly;
  const latest = data.latest;
  const rr = data.reserve_requirements.current;

  const isDraft = rows.some(r => (r.note ?? "").includes("DRAFT")) || (rr?.note ?? "").includes("DRAFT");
  const noData = rows.length === 0;

  // chart-ready data (formatted month label)
  const cd = rows.map(r => ({ ...r, m: fmtMonth(r.month) }));

  const spread =
    latest?.wavg_lending != null && latest?.wavg_deposit != null
      ? latest.wavg_lending - latest.wavg_deposit : null;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-white">Monetary &amp; Prices</h1>
        <p className="text-sm text-gray-400">
          Inflation, monetary aggregates, bank rates &amp; reserve requirements · BBS / Bangladesh Bank
          {latest && <span className="ml-2 text-gray-500">Latest: {fmtMonth(latest.month)}</span>}
        </p>
      </div>

      {isDraft && (
        <div className="rounded-lg border border-amber-800/60 bg-amber-950/30 p-3 text-xs text-amber-300">
          ⚠ This page is showing <strong>draft placeholder values</strong>. Replace them with official BBS/BB
          figures in <code className="text-amber-200">api/seeds/monetary.yaml</code> and redeploy.
        </div>
      )}

      {noData && (
        <div className="rounded-xl border border-gray-800 bg-gray-900 p-8 text-center text-sm text-gray-400">
          No data yet. Add monthly rows to <code>api/seeds/monetary.yaml</code> and redeploy the backend.
        </div>
      )}

      {/* KPI strip */}
      {latest && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <div className="rounded-lg border border-gray-800 bg-gray-900 p-3">
            <div className="text-xs text-gray-400 mb-1 flex items-center">CPI Inflation (p2p)<InfoTip term="CPI (point-to-point)" /></div>
            <div className="text-2xl font-mono text-orange-400">{fmtPct(latest.cpi_p2p)}</div>
            <div className="text-xs text-gray-500 mt-1">12-mo avg {fmtPct(latest.cpi_12mo_avg)}</div>
          </div>
          <div className="rounded-lg border border-gray-800 bg-gray-900 p-3">
            <div className="text-xs text-gray-400 mb-1 flex items-center">Private Credit Growth<InfoTip term="Private-sector credit" /></div>
            <div className="text-2xl font-mono text-sky-400">{fmtPct(latest.private_credit_growth)}</div>
            <div className="text-xs text-gray-500 mt-1">M2 {fmtPct(latest.m2_growth)}</div>
          </div>
          <div className="rounded-lg border border-gray-800 bg-gray-900 p-3">
            <div className="text-xs text-gray-400 mb-1 flex items-center">Lending−Deposit Spread<InfoTip term="Lending-deposit spread" /></div>
            <div className="text-2xl font-mono text-violet-400">{fmtPct(spread)}</div>
            <div className="text-xs text-gray-500 mt-1">L {fmtPct(latest.wavg_lending)} · D {fmtPct(latest.wavg_deposit)}</div>
          </div>
          <div className="rounded-lg border border-gray-800 bg-gray-900 p-3">
            <div className="text-xs text-gray-400 mb-1 flex items-center">CRR / SLR<InfoTip term="CRR" /><InfoTip term="SLR" /></div>
            <div className="text-xl font-mono text-teal-400">{fmtPct(rr?.crr)} <span className="text-gray-600">/</span> {fmtPct(rr?.slr)}</div>
            <div className="text-xs text-gray-500 mt-1">{rr ? `eff. ${fmtDate(rr.effective_date)}` : "—"}</div>
          </div>
        </div>
      )}

      {/* Charts */}
      {!noData && (
        <>
          <div className="grid md:grid-cols-2 gap-4">
            <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
              <h2 className="text-sm font-medium text-gray-300 mb-4 flex items-center">CPI Inflation<InfoTip term="CPI (point-to-point)" /></h2>
              <SeriesLineChart data={cd} xKey="m" unit="%" series={[
                { key: "cpi_p2p", label: "Point-to-point", color: "#fb923c" },
                { key: "cpi_12mo_avg", label: "12-mo average", color: "#f59e0b", dashed: true },
              ]} />
            </div>
            <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
              <h2 className="text-sm font-medium text-gray-300 mb-4">Monetary Aggregates — YoY growth</h2>
              <SeriesLineChart data={cd} xKey="m" unit="%" series={[
                { key: "m2_growth", label: "Broad money (M2)", color: "#38bdf8" },
                { key: "reserve_money_growth", label: "Reserve money", color: "#a78bfa" },
                { key: "private_credit_growth", label: "Private credit", color: "#34d399" },
              ]} />
            </div>
          </div>

          <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
            <h2 className="text-sm font-medium text-gray-300 mb-4">Banking-System Rates — weighted average</h2>
            <SeriesLineChart data={cd} xKey="m" unit="%" series={[
              { key: "wavg_lending", label: "Lending rate", color: "#f87171" },
              { key: "wavg_deposit", label: "Deposit rate", color: "#4ade80" },
            ]} />
          </div>
        </>
      )}

      {/* History table */}
      {!noData && (
        <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-medium text-gray-300">Monthly History ({rows.length} months)</h2>
            <DownloadButton data={rows} filename="monetary_indicators" />
          </div>
          <div className="overflow-x-auto max-h-96 overflow-y-auto">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-gray-900">
                <tr className="text-left text-xs text-gray-400 border-b border-gray-800">
                  <th className="pb-2 pr-4">Month</th>
                  <th className="pb-2 pr-4 text-right">CPI p2p</th>
                  <th className="pb-2 pr-4 text-right">CPI 12m</th>
                  <th className="pb-2 pr-4 text-right">M2</th>
                  <th className="pb-2 pr-4 text-right">Pvt credit</th>
                  <th className="pb-2 pr-4 text-right">Deposit</th>
                  <th className="pb-2 text-right">Lending</th>
                </tr>
              </thead>
              <tbody>
                {[...rows].reverse().map((r, i) => (
                  <tr key={i} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                    <td className="py-1.5 pr-4 text-gray-300">{fmtMonth(r.month)}</td>
                    <td className="py-1.5 pr-4 text-right font-mono text-orange-400">{fmtPct(r.cpi_p2p)}</td>
                    <td className="py-1.5 pr-4 text-right font-mono text-amber-400">{fmtPct(r.cpi_12mo_avg)}</td>
                    <td className="py-1.5 pr-4 text-right font-mono text-sky-400">{fmtPct(r.m2_growth)}</td>
                    <td className="py-1.5 pr-4 text-right font-mono text-green-400">{fmtPct(r.private_credit_growth)}</td>
                    <td className="py-1.5 pr-4 text-right font-mono text-gray-300">{fmtPct(r.wavg_deposit)}</td>
                    <td className="py-1.5 text-right font-mono text-gray-300">{fmtPct(r.wavg_lending)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
