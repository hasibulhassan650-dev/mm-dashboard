import { api } from "@/lib/api";
import YieldCurveChartFull from "@/components/YieldCurveChartFull";
import YieldTrendChart from "@/components/YieldTrendChart";
import DownloadButton from "@/components/DownloadButton";

export const revalidate = 300;

export default async function YieldsPage() {
  const [primary, secondary, history] = await Promise.all([
    api.yieldCurve(),
    api.yieldSecondary(),
    api.yields(12),
  ]);

  const latestAuction = primary.length
    ? primary.slice().sort((a, b) => b.auction_date.localeCompare(a.auction_date))[0].auction_date
    : null;
  const snapDate = secondary.length ? secondary[0].settlement_date : null;

  const tbills = primary.filter(r => r.security_type === "T_BILL");
  const tbonds = primary.filter(r => r.security_type === "T_BOND");

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-white">Yield Curve</h1>
        <p className="text-sm text-gray-400">Primary (BB Treasury auctions) · Secondary (GSOM MTM) · T-Bills, T-Bonds, FRTB</p>
      </div>

      {/* KPI strip */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {tbills.length > 0 && <>
          <div className="rounded-lg border border-gray-800 bg-gray-900 p-3">
            <div className="text-xs text-gray-400 mb-1">T-Bill short end</div>
            <div className="text-lg font-mono text-teal-400">{Math.min(...tbills.map(r => r.cutoff_yield_pct)).toFixed(2)}%</div>
            <div className="text-xs text-gray-500">{tbills.sort((a,b) => (a.tenor_years??0)-(b.tenor_years??0))[0]?.tenor_label}</div>
          </div>
          <div className="rounded-lg border border-gray-800 bg-gray-900 p-3">
            <div className="text-xs text-gray-400 mb-1">T-Bill long end</div>
            <div className="text-lg font-mono text-teal-400">{Math.max(...tbills.map(r => r.cutoff_yield_pct)).toFixed(2)}%</div>
            <div className="text-xs text-gray-500">{tbills.sort((a,b) => (b.tenor_years??0)-(a.tenor_years??0))[0]?.tenor_label}</div>
          </div>
        </>}
        {tbonds.length > 0 && <>
          <div className="rounded-lg border border-gray-800 bg-gray-900 p-3">
            <div className="text-xs text-gray-400 mb-1">T-Bond short end</div>
            <div className="text-lg font-mono text-blue-400">{Math.min(...tbonds.map(r => r.cutoff_yield_pct)).toFixed(2)}%</div>
            <div className="text-xs text-gray-500">{tbonds.sort((a,b) => (a.tenor_years??0)-(b.tenor_years??0))[0]?.tenor_label}</div>
          </div>
          <div className="rounded-lg border border-gray-800 bg-gray-900 p-3">
            <div className="text-xs text-gray-400 mb-1">T-Bond long end</div>
            <div className="text-lg font-mono text-blue-400">{Math.max(...tbonds.map(r => r.cutoff_yield_pct)).toFixed(2)}%</div>
            <div className="text-xs text-gray-500">{tbonds.sort((a,b) => (b.tenor_years??0)-(a.tenor_years??0))[0]?.tenor_label}</div>
          </div>
        </>}
      </div>

      {/* Combined yield curve chart */}
      <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
        <div className="flex items-start justify-between mb-1">
          <h2 className="text-sm font-medium text-gray-300">Yield Curve — Primary &amp; Secondary</h2>
          <div className="text-xs text-gray-500 text-right">
            {snapDate && <div>Secondary (GSOM): {snapDate}</div>}
            {latestAuction && <div>Primary (BB Treasury): {latestAuction}</div>}
          </div>
        </div>
        <YieldCurveChartFull primary={primary} secondary={secondary} />
      </div>

      {/* Data tables */}
      <div className="grid md:grid-cols-2 gap-6">
        {/* Secondary market table */}
        <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
          <h2 className="text-sm font-medium text-gray-300 mb-3">Secondary Market (GSOM MTM) — {secondary.length} securities</h2>
          <div className="overflow-x-auto max-h-72 overflow-y-auto">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-gray-900">
                <tr className="text-gray-400 border-b border-gray-800">
                  <th className="pb-1 pr-2 text-left">Type</th>
                  <th className="pb-1 pr-2 text-left">Security</th>
                  <th className="pb-1 pr-2 text-right">Rem (yr)</th>
                  <th className="pb-1 pr-2 text-right">Yield</th>
                  <th className="pb-1 text-right">O/S (mn)</th>
                </tr>
              </thead>
              <tbody>
                {secondary.map((r, i) => (
                  <tr key={i} className="border-b border-gray-800/40 hover:bg-gray-800/20">
                    <td className="py-1 pr-2">
                      <span className={`px-1 rounded ${r.security_type === "T_BILL" ? "bg-teal-900/50 text-teal-300" : r.security_type === "FRTB" ? "bg-amber-900/50 text-amber-300" : "bg-blue-900/50 text-blue-300"}`}>
                        {r.security_type}
                      </span>
                    </td>
                    <td className="py-1 pr-2 text-gray-300 truncate max-w-[140px]">{r.security_name_norm ?? r.isin}</td>
                    <td className="py-1 pr-2 text-right font-mono text-gray-300">{r.remaining_years.toFixed(2)}</td>
                    <td className="py-1 pr-2 text-right font-mono text-white">{r.market_yield_pct.toFixed(4)}%</td>
                    <td className="py-1 text-right font-mono text-gray-400">{r.outstanding_bdt_mill?.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* Primary market table */}
        <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
          <h2 className="text-sm font-medium text-gray-300 mb-3">Primary Market (BB Treasury) — latest per tenor</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-gray-400 border-b border-gray-800">
                  <th className="pb-1 pr-2 text-left">Type</th>
                  <th className="pb-1 pr-2 text-left">Tenor</th>
                  <th className="pb-1 pr-2 text-right">Yrs</th>
                  <th className="pb-1 pr-2 text-right">Yield</th>
                  <th className="pb-1 pr-2 text-right">Offered (cr)</th>
                  <th className="pb-1 text-right">Accepted (cr)</th>
                </tr>
              </thead>
              <tbody>
                {primary.map((r, i) => (
                  <tr key={i} className="border-b border-gray-800/40 hover:bg-gray-800/20">
                    <td className="py-1 pr-2">
                      <span className={`px-1 rounded ${r.security_type === "T_BILL" ? "bg-teal-900/50 text-teal-300" : r.security_type === "FRTB" ? "bg-amber-900/50 text-amber-300" : "bg-blue-900/50 text-blue-300"}`}>
                        {r.security_type}
                      </span>
                    </td>
                    <td className="py-1 pr-2 text-white font-medium">{r.tenor_label}</td>
                    <td className="py-1 pr-2 text-right font-mono text-gray-300">{r.tenor_years?.toFixed(2)}</td>
                    <td className="py-1 pr-2 text-right font-mono text-white">{r.cutoff_yield_pct.toFixed(4)}%</td>
                    <td className="py-1 pr-2 text-right font-mono text-gray-400">{r.offered_bdt_crore?.toLocaleString() ?? "—"}</td>
                    <td className="py-1 text-right font-mono text-gray-400">{r.accepted_bdt_crore?.toLocaleString() ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* Yield trend — history */}
      <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
        <h2 className="text-sm font-medium text-gray-300 mb-1">Yield Trend — Primary Market Auction History</h2>
        <p className="text-xs text-gray-500 mb-4">Official BB Treasury cutoff yields · {history.length} data points across {new Set(history.map(r => r.tenor_label)).size} tenors</p>
        <YieldTrendChart data={history} />
      </div>

      {/* Full auction history table */}
      <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-medium text-gray-300">Full Auction History (last 12 months)</h2>
          <DownloadButton data={history} filename="yields_auction_history" />
        </div>
        <div className="overflow-x-auto max-h-96 overflow-y-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-gray-900">
              <tr className="text-left text-xs text-gray-400 border-b border-gray-800">
                <th className="pb-2 pr-4">Auction Date</th>
                <th className="pb-2 pr-4">Type</th>
                <th className="pb-2 pr-4">Tenor</th>
                <th className="pb-2 pr-4 text-right">Yield</th>
                <th className="pb-2 pr-4 text-right">Offered (cr)</th>
                <th className="pb-2 text-right">Accepted (cr)</th>
              </tr>
            </thead>
            <tbody>
              {history.map((r, i) => (
                <tr key={i} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                  <td className="py-1.5 pr-4 text-gray-300">{r.auction_date}</td>
                  <td className="py-1.5 pr-4">
                    <span className="px-1.5 py-0.5 rounded text-xs bg-gray-700 text-gray-300">{r.security_type}</span>
                  </td>
                  <td className="py-1.5 pr-4 text-gray-300">{r.tenor_label}</td>
                  <td className="py-1.5 pr-4 text-right text-white font-mono">{r.cutoff_yield_pct.toFixed(4)}%</td>
                  <td className="py-1.5 pr-4 text-right text-gray-400 font-mono">{r.offered_bdt_crore?.toFixed(0) ?? "—"}</td>
                  <td className="py-1.5 text-right text-gray-400 font-mono">{r.accepted_bdt_crore?.toFixed(0) ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
