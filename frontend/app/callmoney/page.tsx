import { api } from "@/lib/api";
import CallMoneyChart from "@/components/CallMoneyChart";
import PeriodSelector from "@/components/PeriodSelector";
import Freshness from "@/components/Freshness";
import InfoTip from "@/components/InfoTip";
import { fmtDate, fmtPct } from "@/lib/format";
import DownloadButton from "@/components/DownloadButton";

export const revalidate = 300;

const PERIODS = [
  { label: "30d",  days: 30  },
  { label: "90d",  days: 90  },
  { label: "180d", days: 180 },
  { label: "1y",   days: 365 },
  { label: "All",  days: 0   },
];

export default async function CallMoneyPage({
  searchParams,
}: {
  searchParams: Promise<{ days?: string }>;
}) {
  const sp   = await searchParams;
  const days = parseInt(sp.days ?? "90", 10);

  const [data, fresh, corridor] = await Promise.all([api.callmoney(days), api.freshness(), api.policy()]);
  const summary = data.daily_summary;
  const cur = corridor.current;
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
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold text-white">Call Money Market</h1>
          <p className="text-sm text-gray-400">
            Interbank overnight &amp; short-term lending rates · Bangladesh Bank
            {latestDate && <span className="ml-2 text-gray-500">Latest: {fmtDate(latestDate)}</span>}
          </p>
          <div className="mt-1"><Freshness updated={fresh.callmoney} /></div>
        </div>
        <PeriodSelector periods={PERIODS} current={days} />
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
          <div className="text-xs text-gray-500 mt-1">{
            lastRow?.overnight_high != null && lastRow?.overnight_low != null
              ? `spread: ${(lastRow.overnight_high - lastRow.overnight_low).toFixed(2)}pp` : "—"
          }</div>
        </div>
        <div className="rounded-lg border border-gray-800 bg-gray-900 p-3">
          <div className="text-xs text-gray-400 mb-1">Overnight Volume</div>
          <div className="text-lg font-mono text-blue-400">
            {lastRow?.overnight_volume_crore != null ? `${lastRow.overnight_volume_crore.toLocaleString()} cr` : "—"}
          </div>
          <div className="text-xs text-gray-500 mt-1">{lastRow?.overnight_deals ?? "—"} deals</div>
        </div>
        <div className="rounded-lg border border-gray-800 bg-gray-900 p-3">
          <div className="text-xs text-gray-400 mb-1">Total Volume (all tenors)</div>
          <div className="text-lg font-mono text-purple-400">
            {lastRow?.total_volume_crore != null ? `${lastRow.total_volume_crore.toLocaleString()} cr` : "—"}
          </div>
          <div className="text-xs text-gray-500 mt-1">{lastRow?.total_deals ?? "—"} total deals</div>
        </div>
      </div>

      {/* Policy corridor strip */}
      {cur && (
        <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
          <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
            <h2 className="text-sm font-medium text-gray-300 flex items-center">
              Policy Rate Corridor<InfoTip term="SLF" /><InfoTip term="SDF" />
            </h2>
            <span className="text-xs text-gray-500">
              Effective {fmtDate(cur.effective_date)}
              {rateNow != null && cur.slf != null && cur.sdf != null && (
                <span className="ml-2">
                  · O/N {rateNow >= cur.slf ? "at/above ceiling (tight)"
                    : rateNow <= cur.sdf ? "at/below floor (flush)"
                    : "inside corridor"}
                </span>
              )}
            </span>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <div className="rounded-lg border border-red-900/50 bg-red-950/20 p-3">
              <div className="text-xs text-gray-400 mb-1">SLF — Ceiling</div>
              <div className="text-xl font-mono text-red-400">{fmtPct(cur.slf)}</div>
            </div>
            <div className="rounded-lg border border-teal-900/50 bg-teal-950/20 p-3">
              <div className="text-xs text-gray-400 mb-1">Repo — Policy</div>
              <div className="text-xl font-mono text-teal-400">{fmtPct(cur.repo)}</div>
            </div>
            <div className="rounded-lg border border-green-900/50 bg-green-950/20 p-3">
              <div className="text-xs text-gray-400 mb-1">SDF — Floor</div>
              <div className="text-xl font-mono text-green-400">{fmtPct(cur.sdf)}</div>
            </div>
            <div className="rounded-lg border border-gray-800 bg-gray-900 p-3">
              <div className="text-xs text-gray-400 mb-1">Bank Rate</div>
              <div className="text-xl font-mono text-gray-300">{fmtPct(cur.bank_rate)}</div>
            </div>
          </div>
          {cur.note && cur.note.includes("DRAFT") && (
            <p className="text-xs text-amber-500/80 mt-2">⚠ Draft values — pending verification against BB circulars.</p>
          )}
        </div>
      )}

      {/* Chart */}
      <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
        <h2 className="text-sm font-medium text-gray-300 mb-4">Overnight Call Rate — trend{cur && <span className="text-xs text-gray-500 font-normal ml-2">vs policy corridor</span>}</h2>
        <CallMoneyChart data={summary} corridor={cur ? { repo: cur.repo, sdf: cur.sdf, slf: cur.slf } : undefined} />
      </div>

      {/* Latest day breakdown */}
      {latest.length > 0 && (
        <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
          <h2 className="text-sm font-medium text-gray-300 mb-4">Latest Day Breakdown — {fmtDate(latestDate)}</h2>
          <div className="grid md:grid-cols-3 gap-4">
            {products.map(product => {
              const rows = latest.filter(r => r.product === product)
                .sort((a, b) => (a.maturity_days ?? 0) - (b.maturity_days ?? 0));
              const totalVol   = rows.reduce((s, r) => s + (r.amount_crore ?? 0), 0);
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
                          <td className="py-1 pr-2 text-gray-300">{r.maturity_days != null ? `${r.maturity_days}D` : "—"}</td>
                          <td className="py-1 pr-2 text-right font-mono text-amber-400">{r.average_rate_pct?.toFixed(2) ?? "—"}%</td>
                          <td className="py-1 pr-2 text-right font-mono text-red-400">{r.highest_rate_pct?.toFixed(2) ?? "—"}%</td>
                          <td className="py-1 pr-2 text-right font-mono text-green-400">{r.lowest_rate_pct?.toFixed(2) ?? "—"}%</td>
                          <td className="py-1 text-right font-mono text-gray-300">{r.amount_crore?.toLocaleString() ?? "—"}</td>
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

      {/* History table */}
      <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-medium text-gray-300">Daily Summary ({summary.length} days)</h2>
          <DownloadButton data={summary} filename="callmoney_daily" />
        </div>
        <div className="overflow-x-auto max-h-96 overflow-y-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-gray-900">
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
                  <td className="py-1.5 pr-4 text-gray-300">{fmtDate(r.trade_date)}</td>
                  <td className="py-1.5 pr-4 text-right font-mono text-amber-400">{r.overnight_wavg_rate?.toFixed(2) ?? "—"}%</td>
                  <td className="py-1.5 pr-4 text-right font-mono text-red-400">{r.overnight_high?.toFixed(2) ?? "—"}%</td>
                  <td className="py-1.5 pr-4 text-right font-mono text-green-400">{r.overnight_low?.toFixed(2) ?? "—"}%</td>
                  <td className="py-1.5 pr-4 text-right font-mono text-gray-300">{r.overnight_volume_crore?.toLocaleString() ?? "—"}</td>
                  <td className="py-1.5 pr-4 text-right font-mono text-gray-400">{r.overnight_deals ?? "—"}</td>
                  <td className="py-1.5 text-right font-mono text-gray-300">{r.total_volume_crore?.toLocaleString() ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
