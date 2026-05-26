import { api } from "@/lib/api";
import CallMoneyChart from "@/components/CallMoneyChart";

export const revalidate = 300;

export default async function CallMoneyPage() {
  const data = await api.callmoney(30);
  const summary = data.daily_summary;
  const latest  = data.latest_breakdown;
  const latestDate = data.latest_date;

  const lastRow  = summary.at(-1);
  const prevRow  = summary.at(-2);
  const rateNow  = lastRow?.overnight_wavg_rate ?? null;
  const ratePrev = prevRow?.overnight_wavg_rate ?? null;
  const rateDelta = rateNow !== null && ratePrev !== null ? rateNow - ratePrev : null;

  const products = [...new Set(latest.map(r => r.product))].sort();

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-white">Call Money Market</h1>
        <p className="text-sm text-gray-400">
          Interbank overnight &amp; short-term lending rates · Bangladesh Bank · last 30 days
          {latestDate && <span className="ml-2 text-gray-500">Latest: {latestDate}</span>}
        </p>
      </div>

      {/* KPI strip */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="rounded-lg border border-gray-800 bg-gray-900 p-3">
          <div className="text-xs text-gray-400 mb-1">Overnight Avg Rate</div>
          <div className="text-2xl font-mono text-amber-400">
            {rateNow !== null ? `${rateNow.toFixed(2)}%` : "—"}
          </div>
          {rateDelta !== null && (
            <div className={`text-xs mt-1 ${rateDelta > 0 ? "text-red-400" : "text-green-400"}`}>
              {rateDelta > 0 ? "▲" : "▼"} {Math.abs(rateDelta).toFixed(2)}pp vs prev day
            </div>
          )}
        </div>
        <div className="rounded-lg border border-gray-800 bg-gray-900 p-3">
          <div className="text-xs text-gray-400 mb-1">Overnight High / Low</div>
          <div className="text-sm font-mono">
            <span className="text-red-400">{lastRow?.overnight_high?.toFixed(2) ?? "—"}%</span>
            <span className="text-gray-500 mx-1">/</span>
            <span className="text-green-400">{lastRow?.overnight_low?.toFixed(2) ?? "—"}%</span>
          </div>
          <div className="text-xs text-gray-500 mt-1">spread: {
            lastRow?.overnight_high != null && lastRow?.overnight_low != null
              ? `${(lastRow.overnight_high - lastRow.overnight_low).toFixed(2)}pp`
              : "—"
          }</div>
        </div>
        <div className="rounded-lg border border-gray-800 bg-gray-900 p-3">
          <div className="text-xs text-gray-400 mb-1">Overnight Volume</div>
          <div className="text-lg font-mono text-blue-400">
            {lastRow?.overnight_volume_crore != null
              ? `${lastRow.overnight_volume_crore.toLocaleString()} cr`
              : "—"}
          </div>
          <div className="text-xs text-gray-500 mt-1">
            {lastRow?.overnight_deals ?? "—"} deals
          </div>
        </div>
        <div className="rounded-lg border border-gray-800 bg-gray-900 p-3">
          <div className="text-xs text-gray-400 mb-1">Total Volume (all tenors)</div>
          <div className="text-lg font-mono text-purple-400">
            {lastRow?.total_volume_crore != null
              ? `${lastRow.total_volume_crore.toLocaleString()} cr`
              : "—"}
          </div>
          <div className="text-xs text-gray-500 mt-1">
            {lastRow?.total_deals ?? "—"} total deals
          </div>
        </div>
      </div>

      {/* 30-day chart */}
      <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-medium text-gray-300">Overnight Call Rate — 30-day trend</h2>
          <div className="flex gap-4 text-xs text-gray-500">
            <span><span className="text-amber-400">——</span> Avg</span>
            <span><span className="text-red-400">- -</span> High</span>
            <span><span className="text-green-400">- -</span> Low</span>
            <span><span className="text-gray-600">▬</span> Volume (right)</span>
          </div>
        </div>
        <CallMoneyChart data={summary} />
      </div>

      {/* Latest day breakdown */}
      {latest.length > 0 && (
        <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
          <h2 className="text-sm font-medium text-gray-300 mb-4">
            Latest Day Breakdown — {latestDate}
          </h2>
          <div className="grid md:grid-cols-3 gap-4">
            {products.map(product => {
              const rows = latest.filter(r => r.product === product)
                .sort((a, b) => (a.maturity_days ?? 0) - (b.maturity_days ?? 0));
              const totalVol = rows.reduce((s, r) => s + (r.amount_crore ?? 0), 0);
              const totalDeals = rows.reduce((s, r) => s + (r.num_deals ?? 0), 0);
              return (
                <div key={product} className="rounded-lg border border-gray-800 p-3">
                  <div className="flex items-center justify-between mb-2">
                    <h3 className="text-sm font-medium text-white">{product}</h3>
                    <span className="text-xs text-gray-400">{totalVol.toLocaleString()} cr · {totalDeals} deals</span>
                  </div>
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-gray-500 border-b border-gray-800">
                        <th className="pb-1 pr-2 text-left">Tenor</th>
                        <th className="pb-1 pr-2 text-right">Avg %</th>
                        <th className="pb-1 pr-2 text-right">High</th>
                        <th className="pb-1 pr-2 text-right">Low</th>
                        <th className="pb-1 text-right">Vol (cr)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((r, i) => (
                        <tr key={i} className="border-b border-gray-800/40">
                          <td className="py-1 pr-2 text-gray-300">
                            {r.maturity_days != null ? `${r.maturity_days}D` : "—"}
                          </td>
                          <td className="py-1 pr-2 text-right font-mono text-amber-400">
                            {r.average_rate_pct?.toFixed(2) ?? "—"}%
                          </td>
                          <td className="py-1 pr-2 text-right font-mono text-red-400">
                            {r.highest_rate_pct?.toFixed(2) ?? "—"}%
                          </td>
                          <td className="py-1 pr-2 text-right font-mono text-green-400">
                            {r.lowest_rate_pct?.toFixed(2) ?? "—"}%
                          </td>
                          <td className="py-1 text-right font-mono text-gray-300">
                            {r.amount_crore?.toLocaleString() ?? "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* 30-day history table */}
      <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
        <h2 className="text-sm font-medium text-gray-300 mb-4">30-day Daily Summary</h2>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-gray-400 border-b border-gray-800">
                <th className="pb-2 pr-4">Date</th>
                <th className="pb-2 pr-4 text-right">O/N Avg Rate</th>
                <th className="pb-2 pr-4 text-right">O/N High</th>
                <th className="pb-2 pr-4 text-right">O/N Low</th>
                <th className="pb-2 pr-4 text-right">O/N Volume (cr)</th>
                <th className="pb-2 pr-4 text-right">O/N Deals</th>
                <th className="pb-2 text-right">Total Volume (cr)</th>
              </tr>
            </thead>
            <tbody>
              {[...summary].reverse().map((r, i) => (
                <tr key={i} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                  <td className="py-1.5 pr-4 text-gray-300">{r.trade_date}</td>
                  <td className="py-1.5 pr-4 text-right font-mono text-amber-400">
                    {r.overnight_wavg_rate?.toFixed(2) ?? "—"}%
                  </td>
                  <td className="py-1.5 pr-4 text-right font-mono text-red-400">
                    {r.overnight_high?.toFixed(2) ?? "—"}%
                  </td>
                  <td className="py-1.5 pr-4 text-right font-mono text-green-400">
                    {r.overnight_low?.toFixed(2) ?? "—"}%
                  </td>
                  <td className="py-1.5 pr-4 text-right font-mono text-gray-300">
                    {r.overnight_volume_crore?.toLocaleString() ?? "—"}
                  </td>
                  <td className="py-1.5 pr-4 text-right font-mono text-gray-400">
                    {r.overnight_deals ?? "—"}
                  </td>
                  <td className="py-1.5 text-right font-mono text-gray-300">
                    {r.total_volume_crore?.toLocaleString() ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
