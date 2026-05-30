import { api } from "@/lib/api";
import FxChart from "@/components/FxChart";
import PeriodSelector from "@/components/PeriodSelector";
import Freshness from "@/components/Freshness";
import { fmtDate } from "@/lib/format";
import DownloadButton from "@/components/DownloadButton";

export const revalidate = 300;

const PERIODS = [
  { label: "90d",  days: 90  },
  { label: "180d", days: 180 },
  { label: "1y",   days: 365 },
  { label: "2y",   days: 730 },
  { label: "All",  days: 0   },
];

export default async function FxPage({
  searchParams,
}: {
  searchParams: Promise<{ days?: string }>;
}) {
  const sp   = await searchParams;
  const days = parseInt(sp.days ?? "365", 10);
  const [rows, fresh] = await Promise.all([api.fx(days), api.freshness()]);

  const latest   = rows[0] ?? null;
  const prev     = rows[1] ?? null;
  const rateDelta =
    latest?.weighted_avg_rate != null && prev?.weighted_avg_rate != null
      ? latest.weighted_avg_rate - prev.weighted_avg_rate
      : null;

  const totalAccepted = rows.reduce((s, r) => s + (r.accepted_amount_usd_mill ?? 0), 0);
  const totalBid      = rows.reduce((s, r) => s + (r.bid_amount_usd_mill ?? 0), 0);
  const hitRate       = totalBid > 0 ? (totalAccepted / totalBid) * 100 : null;

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold text-white">FX Auction Results</h1>
          <p className="text-sm text-gray-400">
            Bangladesh Bank USD/BDT intervention auctions
            {latest && <span className="ml-2 text-gray-500">Latest: {fmtDate(latest.auction_date)}</span>}
          </p>
          <div className="mt-1"><Freshness updated={fresh.fx} /></div>
        </div>
        <PeriodSelector periods={PERIODS} current={days} />
      </div>

      {/* KPI strip */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="rounded-lg border border-gray-800 bg-gray-900 p-3">
          <div className="text-xs text-gray-400 mb-1">Wtd Avg Rate (latest)</div>
          <div className="text-2xl font-mono text-sky-400">
            {latest?.weighted_avg_rate?.toFixed(4) ?? "—"}
          </div>
          {rateDelta !== null && (
            <div className={`text-xs mt-1 ${rateDelta > 0 ? "text-red-400" : "text-green-400"}`}>
              {rateDelta > 0 ? "▲" : "▼"} {Math.abs(rateDelta).toFixed(4)} vs prev
            </div>
          )}
        </div>
        <div className="rounded-lg border border-gray-800 bg-gray-900 p-3">
          <div className="text-xs text-gray-400 mb-1">Cutoff Rate (latest)</div>
          <div className="text-xl font-mono text-orange-400">
            {latest?.cutoff_rate?.toFixed(4) ?? "—"}
          </div>
          <div className="text-xs text-gray-500 mt-1">
            {latest?.auction_type ?? "—"}
          </div>
        </div>
        <div className="rounded-lg border border-gray-800 bg-gray-900 p-3">
          <div className="text-xs text-gray-400 mb-1">Accepted (latest)</div>
          <div className="text-xl font-mono text-blue-400">
            ${latest?.accepted_amount_usd_mill?.toFixed(1) ?? "—"}m
          </div>
          <div className="text-xs text-gray-500 mt-1">
            {latest?.num_accepted ?? "—"} of {latest?.num_bids ?? "—"} bids
          </div>
        </div>
        <div className="rounded-lg border border-gray-800 bg-gray-900 p-3">
          <div className="text-xs text-gray-400 mb-1">Bid-Hit Ratio (12 mo)</div>
          <div className="text-xl font-mono text-purple-400">
            {hitRate !== null ? `${hitRate.toFixed(1)}%` : "—"}
          </div>
          <div className="text-xs text-gray-500 mt-1">
            ${totalAccepted.toFixed(0)}m of ${totalBid.toFixed(0)}m bid
          </div>
        </div>
      </div>

      {/* Rate chart */}
      <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-medium text-gray-300">BDT/USD Rate — 12-month history</h2>
          <div className="flex gap-4 text-xs text-gray-500">
            <span><span className="text-sky-400">——</span> Wtd Avg</span>
            <span><span className="text-orange-400">- -</span> Cutoff</span>
            <span><span className="text-blue-900">▬</span> Accepted ($m, right)</span>
          </div>
        </div>
        <FxChart data={rows} />
      </div>

      {/* Auction history table */}
      <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-medium text-gray-300">Auction History ({rows.length} auctions)</h2>
          <DownloadButton data={rows} filename="fx_auction_history" />
        </div>
        <div className="overflow-x-auto max-h-96 overflow-y-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-gray-900">
              <tr className="text-left text-xs text-gray-400 border-b border-gray-800">
                <th className="pb-2 pr-4">Auction Date</th>
                <th className="pb-2 pr-4">Settlement</th>
                <th className="pb-2 pr-4">Type</th>
                <th className="pb-2 pr-4 text-right">Bids</th>
                <th className="pb-2 pr-4 text-right">Bid Amt ($m)</th>
                <th className="pb-2 pr-4">Bid Range</th>
                <th className="pb-2 pr-4 text-right">Accepted</th>
                <th className="pb-2 pr-4 text-right">Acc Amt ($m)</th>
                <th className="pb-2 pr-4 text-right">Cutoff</th>
                <th className="pb-2 text-right">Wtd Avg</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                  <td className="py-1.5 pr-4 text-gray-300">{fmtDate(r.auction_date)}</td>
                  <td className="py-1.5 pr-4 text-gray-400">{fmtDate(r.settlement_date)}</td>
                  <td className="py-1.5 pr-4 text-gray-400">{r.auction_type}</td>
                  <td className="py-1.5 pr-4 text-right font-mono text-gray-300">{r.num_bids ?? "—"}</td>
                  <td className="py-1.5 pr-4 text-right font-mono text-gray-300">
                    {r.bid_amount_usd_mill?.toFixed(1) ?? "—"}
                  </td>
                  <td className="py-1.5 pr-4 text-gray-400 text-xs">{r.bid_range ?? "—"}</td>
                  <td className="py-1.5 pr-4 text-right font-mono text-gray-300">{r.num_accepted ?? "—"}</td>
                  <td className="py-1.5 pr-4 text-right font-mono text-blue-400">
                    {r.accepted_amount_usd_mill?.toFixed(1) ?? "—"}
                  </td>
                  <td className="py-1.5 pr-4 text-right font-mono text-orange-400">
                    {r.cutoff_rate?.toFixed(4) ?? "—"}
                  </td>
                  <td className="py-1.5 text-right font-mono text-sky-400">
                    {r.weighted_avg_rate?.toFixed(4) ?? "—"}
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
