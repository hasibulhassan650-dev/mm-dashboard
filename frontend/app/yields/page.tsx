import { api } from "@/lib/api";
import YieldCurveChart from "@/components/YieldCurveChart";
import YieldHistoryChart from "@/components/YieldHistoryChart";

export const revalidate = 300;

export default async function YieldsPage() {
  const [curve, history] = await Promise.all([
    api.yieldCurve(),
    api.yields(12),
  ]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-white">Primary Market Yields</h1>
        <p className="text-sm text-gray-400">BB Treasury auction results · T-Bills, T-Bonds, FRTB</p>
      </div>

      <div className="grid md:grid-cols-2 gap-6">
        <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
          <h2 className="text-sm font-medium text-gray-300 mb-4">Current Yield Curve</h2>
          <YieldCurveChart data={curve} />
          <div className="mt-4 grid grid-cols-2 gap-2">
            {curve.map(r => (
              <div key={r.tenor_label} className="flex justify-between text-xs py-1 border-b border-gray-800">
                <span className="text-gray-400">{r.tenor_label}</span>
                <span className="text-white font-mono">{r.cutoff_yield_pct.toFixed(4)}%</span>
              </div>
            ))}
          </div>
        </div>

        <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
          <h2 className="text-sm font-medium text-gray-300 mb-4">Yield Trends (12 months)</h2>
          <YieldHistoryChart data={history} />
        </div>
      </div>

      <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
        <h2 className="text-sm font-medium text-gray-300 mb-4">Auction History</h2>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
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
              {history.slice(0, 50).map((r, i) => (
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
