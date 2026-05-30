import { api } from "@/lib/api";
import ReservesChart from "@/components/ReservesChart";
import RemittanceChart from "@/components/RemittanceChart";
import DownloadButton from "@/components/DownloadButton";
import InfoTip from "@/components/InfoTip";
import { fmtMonth } from "@/lib/format";

export const revalidate = 300;

export default async function MacroPage() {
  const macro = await api.macro();
  const rows = macro.series;
  const latest = macro.latest;
  const prev = rows.length >= 2 ? rows[rows.length - 2] : null;

  const remDelta =
    latest?.remittance_usd_mn != null && prev?.remittance_usd_mn != null
      ? latest.remittance_usd_mn - prev.remittance_usd_mn
      : null;
  const remDeltaPct =
    remDelta != null && prev?.remittance_usd_mn ? (remDelta / prev.remittance_usd_mn) * 100 : null;

  const isDraft = rows.some(r => (r.note ?? "").includes("DRAFT"));
  const noData = rows.length === 0;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-white">External Sector</h1>
        <p className="text-sm text-gray-400">
          FX reserves &amp; wage-earner remittances · Bangladesh Bank
          {latest && <span className="ml-2 text-gray-500">Latest: {fmtMonth(latest.month)}</span>}
        </p>
      </div>

      {isDraft && (
        <div className="rounded-lg border border-amber-800/60 bg-amber-950/30 p-3 text-xs text-amber-300">
          ⚠ This page is showing <strong>draft placeholder values</strong>. Replace them with official
          Bangladesh Bank figures in <code className="text-amber-200">api/seeds/reserves_remittances.yaml</code> and redeploy.
        </div>
      )}

      {noData && (
        <div className="rounded-xl border border-gray-800 bg-gray-900 p-8 text-center text-sm text-gray-400">
          No data yet. Add monthly rows to <code>api/seeds/reserves_remittances.yaml</code> and redeploy the backend.
        </div>
      )}

      {/* KPI strip */}
      {latest && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <div className="rounded-lg border border-gray-800 bg-gray-900 p-3">
            <div className="text-xs text-gray-400 mb-1 flex items-center">Gross Reserves<InfoTip term="Gross reserves" /></div>
            <div className="text-2xl font-mono text-sky-400">${latest.gross_reserves_usd_bn?.toFixed(1) ?? "—"}<span className="text-sm text-gray-500">bn</span></div>
          </div>
          <div className="rounded-lg border border-gray-800 bg-gray-900 p-3">
            <div className="text-xs text-gray-400 mb-1 flex items-center">Net Reserves<InfoTip term="Net reserves (BPM6)" /></div>
            <div className="text-2xl font-mono text-teal-400">${latest.net_reserves_bpm6_usd_bn?.toFixed(1) ?? "—"}<span className="text-sm text-gray-500">bn</span></div>
          </div>
          <div className="rounded-lg border border-gray-800 bg-gray-900 p-3">
            <div className="text-xs text-gray-400 mb-1 flex items-center">Remittance (mo)<InfoTip term="Remittance" /></div>
            <div className="text-2xl font-mono text-violet-400">${((latest.remittance_usd_mn ?? 0) / 1000).toFixed(2)}<span className="text-sm text-gray-500">bn</span></div>
            <div className="text-xs text-gray-500 mt-1">${latest.remittance_usd_mn?.toLocaleString() ?? "—"} mn</div>
          </div>
          <div className="rounded-lg border border-gray-800 bg-gray-900 p-3">
            <div className="text-xs text-gray-400 mb-1">Remittance MoM</div>
            {remDeltaPct != null ? (
              <div className={`text-xl font-mono ${remDeltaPct >= 0 ? "text-green-400" : "text-red-400"}`}>
                {remDeltaPct >= 0 ? "▲" : "▼"} {Math.abs(remDeltaPct).toFixed(1)}%
              </div>
            ) : <div className="text-xl font-mono text-gray-500">—</div>}
            <div className="text-xs text-gray-500 mt-1">vs {prev ? fmtMonth(prev.month) : "—"}</div>
          </div>
        </div>
      )}

      {/* Charts */}
      {!noData && (
        <div className="grid md:grid-cols-2 gap-4">
          <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
            <h2 className="text-sm font-medium text-gray-300 mb-4">FX Reserves — gross &amp; net (BPM6)</h2>
            <ReservesChart data={rows} />
          </div>
          <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
            <h2 className="text-sm font-medium text-gray-300 mb-4">Monthly Remittance Inflow</h2>
            <RemittanceChart data={rows} />
          </div>
        </div>
      )}

      {/* History table */}
      {!noData && (
        <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-medium text-gray-300">Monthly History ({rows.length} months)</h2>
            <DownloadButton data={rows} filename="reserves_remittances" />
          </div>
          <div className="overflow-x-auto max-h-96 overflow-y-auto">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-gray-900">
                <tr className="text-left text-xs text-gray-400 border-b border-gray-800">
                  <th className="pb-2 pr-4">Month</th>
                  <th className="pb-2 pr-4 text-right">Gross ($bn)</th>
                  <th className="pb-2 pr-4 text-right">Net BPM6 ($bn)</th>
                  <th className="pb-2 text-right">Remittance ($mn)</th>
                </tr>
              </thead>
              <tbody>
                {[...rows].reverse().map((r, i) => (
                  <tr key={i} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                    <td className="py-1.5 pr-4 text-gray-300">{fmtMonth(r.month)}</td>
                    <td className="py-1.5 pr-4 text-right font-mono text-sky-400">{r.gross_reserves_usd_bn?.toFixed(1) ?? "—"}</td>
                    <td className="py-1.5 pr-4 text-right font-mono text-teal-400">{r.net_reserves_bpm6_usd_bn?.toFixed(1) ?? "—"}</td>
                    <td className="py-1.5 text-right font-mono text-violet-400">{r.remittance_usd_mn?.toLocaleString() ?? "—"}</td>
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
