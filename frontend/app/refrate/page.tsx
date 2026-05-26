import { api } from "@/lib/api";
import RefRateChart from "@/components/RefRateChart";
import PeriodSelector from "@/components/PeriodSelector";

export const revalidate = 300;

const PRODUCTS = ["Overnight", "1W", "1M", "3M"];

const PERIODS = [
  { label: "30d",  days: 30  },
  { label: "90d",  days: 90  },
  { label: "180d", days: 180 },
  { label: "1y",   days: 365 },
  { label: "All",  days: 0   },
];

export default async function RefRatePage({
  searchParams,
}: {
  searchParams: Promise<{ days?: string }>;
}) {
  const sp   = await searchParams;
  const days = parseInt(sp.days ?? "90", 10);
  const rows = await api.refrate(days);

  const dommr = rows.filter(r => r.rate_type === "DOMMR");
  const bofr  = rows.filter(r => r.rate_type === "BOFR");

  // Latest date's data for each type
  const latestDate = rows[0]?.trade_date ?? null;
  const latestDommr = dommr.filter(r => r.trade_date === latestDate);
  const latestBofr  = bofr.filter(r => r.trade_date === latestDate);

  const onNightDommr = latestDommr.find(r => r.product === "Overnight");
  const onNightBofr  = latestBofr.find(r => r.product === "Overnight");

  // Previous day delta for overnight
  const dates = [...new Set(rows.map(r => r.trade_date))].sort().reverse();
  const prevDate = dates[1] ?? null;
  const prevDommr = dommr.find(r => r.trade_date === prevDate && r.product === "Overnight");
  const prevBofr  = bofr.find(r => r.trade_date === prevDate && r.product === "Overnight");

  const dommrDelta = onNightDommr?.rate_pct != null && prevDommr?.rate_pct != null
    ? onNightDommr.rate_pct - prevDommr.rate_pct : null;
  const bofrDelta = onNightBofr?.rate_pct != null && prevBofr?.rate_pct != null
    ? onNightBofr.rate_pct - prevBofr.rate_pct : null;

  const noData = rows.length === 0;

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold text-white">Money Market Reference Rates</h1>
          <p className="text-sm text-gray-400">
            DOMMR &amp; BOFR · Bangladesh Bank
            {latestDate && <span className="ml-2 text-gray-500">Latest: {latestDate}</span>}
          </p>
        </div>
        <PeriodSelector periods={PERIODS} current={days} />
      </div>

      {noData && (
        <div className="rounded-xl border border-yellow-800 bg-yellow-950/30 p-4 text-sm text-yellow-300">
          No data yet. Save the BB reference rate page as <code>refrate_history.html</code> and run{" "}
          <code>py -3.14 load_refrate_html.py</code> to load history.
        </div>
      )}

      {/* KPI strip */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="rounded-lg border border-gray-800 bg-gray-900 p-3">
          <div className="text-xs text-gray-400 mb-1">DOMMR Overnight</div>
          <div className="text-2xl font-mono text-sky-400">
            {onNightDommr?.rate_pct?.toFixed(2) ?? "—"}%
          </div>
          {dommrDelta !== null && (
            <div className={`text-xs mt-1 ${dommrDelta > 0 ? "text-red-400" : "text-green-400"}`}>
              {dommrDelta > 0 ? "▲" : "▼"} {Math.abs(dommrDelta).toFixed(2)}pp vs prev
            </div>
          )}
        </div>
        <div className="rounded-lg border border-gray-800 bg-gray-900 p-3">
          <div className="text-xs text-gray-400 mb-1">BOFR Overnight</div>
          <div className="text-2xl font-mono text-violet-400">
            {onNightBofr?.rate_pct?.toFixed(2) ?? "—"}%
          </div>
          {bofrDelta !== null && (
            <div className={`text-xs mt-1 ${bofrDelta > 0 ? "text-red-400" : "text-green-400"}`}>
              {bofrDelta > 0 ? "▲" : "▼"} {Math.abs(bofrDelta).toFixed(2)}pp vs prev
            </div>
          )}
        </div>
        <div className="rounded-lg border border-gray-800 bg-gray-900 p-3">
          <div className="text-xs text-gray-400 mb-1">DOMMR — 1W / 1M / 3M</div>
          <div className="text-sm font-mono space-y-0.5">
            {["1W", "1M", "3M"].map(p => {
              const r = latestDommr.find(x => x.product === p);
              return (
                <div key={p} className="flex justify-between">
                  <span className="text-gray-500">{p}</span>
                  <span className="text-sky-300">{r?.rate_pct?.toFixed(2) ?? "—"}%</span>
                </div>
              );
            })}
          </div>
        </div>
        <div className="rounded-lg border border-gray-800 bg-gray-900 p-3">
          <div className="text-xs text-gray-400 mb-1">BOFR — 1W / 1M / 3M</div>
          <div className="text-sm font-mono space-y-0.5">
            {["1W", "1M", "3M"].map(p => {
              const r = latestBofr.find(x => x.product === p);
              return (
                <div key={p} className="flex justify-between">
                  <span className="text-gray-500">{p}</span>
                  <span className="text-violet-300">{r?.rate_pct?.toFixed(2) ?? "—"}%</span>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* Charts */}
      <div className="grid md:grid-cols-2 gap-4">
        <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
          <h2 className="text-sm font-medium text-gray-300 mb-4">DOMMR — rate by product</h2>
          <RefRateChart rows={rows} rateType="DOMMR" />
        </div>
        <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
          <h2 className="text-sm font-medium text-gray-300 mb-4">BOFR — rate by product</h2>
          <RefRateChart rows={rows} rateType="BOFR" />
        </div>
      </div>

      {/* Latest day tables */}
      {latestDate && (
        <div className="grid md:grid-cols-2 gap-4">
          {(["DOMMR", "BOFR"] as const).map(rtype => {
            const latest = rows.filter(r => r.trade_date === latestDate && r.rate_type === rtype)
              .sort((a, b) => PRODUCTS.indexOf(a.product) - PRODUCTS.indexOf(b.product));
            return (
              <div key={rtype} className="rounded-xl border border-gray-800 bg-gray-900 p-4">
                <h2 className="text-sm font-medium text-gray-300 mb-3">{rtype} — {latestDate}</h2>
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-xs text-gray-400 border-b border-gray-800">
                      <th className="pb-2 text-left">Product</th>
                      <th className="pb-2 text-right">Rate %</th>
                      <th className="pb-2 text-right">Amount (cr)</th>
                      <th className="pb-2 text-right">Deals</th>
                    </tr>
                  </thead>
                  <tbody>
                    {latest.map((r, i) => (
                      <tr key={i} className="border-b border-gray-800/50">
                        <td className="py-1.5 text-gray-300">{r.product}</td>
                        <td className={`py-1.5 text-right font-mono ${rtype === "DOMMR" ? "text-sky-400" : "text-violet-400"}`}>
                          {r.rate_pct?.toFixed(2) ?? "—"}%
                        </td>
                        <td className="py-1.5 text-right font-mono text-gray-300">
                          {r.amount_crore?.toLocaleString() ?? "—"}
                        </td>
                        <td className="py-1.5 text-right font-mono text-gray-400">
                          {r.num_deals ?? "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          })}
        </div>
      )}

      {/* History table */}
      {rows.length > 0 && (
        <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
          <h2 className="text-sm font-medium text-gray-300 mb-4">Full History</h2>
          <div className="overflow-x-auto max-h-96 overflow-y-auto">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-gray-900">
                <tr className="text-xs text-gray-400 border-b border-gray-800">
                  <th className="pb-2 pr-4 text-left">Date</th>
                  <th className="pb-2 pr-4 text-left">Type</th>
                  <th className="pb-2 pr-4 text-left">Product</th>
                  <th className="pb-2 pr-4 text-right">Rate %</th>
                  <th className="pb-2 pr-4 text-right">Amount (cr)</th>
                  <th className="pb-2 text-right">Deals</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                    <td className="py-1.5 pr-4 text-gray-300">{r.trade_date}</td>
                    <td className="py-1.5 pr-4">
                      <span className={`text-xs font-medium ${r.rate_type === "DOMMR" ? "text-sky-400" : "text-violet-400"}`}>
                        {r.rate_type}
                      </span>
                    </td>
                    <td className="py-1.5 pr-4 text-gray-400">{r.product}</td>
                    <td className={`py-1.5 pr-4 text-right font-mono ${r.rate_type === "DOMMR" ? "text-sky-300" : "text-violet-300"}`}>
                      {r.rate_pct?.toFixed(2) ?? "—"}%
                    </td>
                    <td className="py-1.5 pr-4 text-right font-mono text-gray-300">
                      {r.amount_crore?.toLocaleString() ?? "—"}
                    </td>
                    <td className="py-1.5 text-right font-mono text-gray-400">
                      {r.num_deals ?? "—"}
                    </td>
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
