import { api } from "@/lib/api";
import StatCard from "@/components/StatCard";
import CashFlowChart from "@/components/CashFlowChart";
import Link from "next/link";
import DownloadButton from "@/components/DownloadButton";
import Freshness from "@/components/Freshness";
import { fmtDate } from "@/lib/format";

export const revalidate = 300;

export default async function CashFlowsPage() {
  const [flows, fresh] = await Promise.all([api.flows(6), api.freshness()]);

  const today = new Date().toISOString().slice(0, 10);
  const todayRow = [...flows].reverse().find(r => r.flow_date <= today) ?? flows[flows.length - 1];
  const upcoming = flows.filter(r => r.flow_date >= today).slice(0, 30);

  const totalInflow  = flows.reduce((s, r) => s + (r.total_inflow_bdt_mill ?? 0), 0);
  const totalOutflow = flows.reduce((s, r) => s + (r.auction_outflow_confirmed_mill ?? r.auction_outflow_planned_mill ?? 0), 0);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-white">Daily Cash Flows</h1>
        <p className="text-sm text-gray-400">Coupon inflows, principal maturities, auction outflows — last 6 months + 4 months ahead</p>
        <div className="mt-1"><Freshness updated={fresh.flows} /></div>
      </div>

      {todayRow && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard label="Today Inflow" value={`${(todayRow.total_inflow_bdt_mill/1000).toFixed(1)}k mn`} sub={fmtDate(todayRow.flow_date)} color="green" />
          <StatCard label="Today Outflow" value={`${((todayRow.auction_outflow_confirmed_mill||todayRow.auction_outflow_planned_mill)/1000).toFixed(1)}k mn`} sub="auction settlements" color="red" />
          <StatCard label="Net Borrowing" value={`${(todayRow.net_borrowing_bdt_mill/1000).toFixed(1)}k mn`} sub={todayRow.net_borrowing_bdt_mill > 0 ? "net borrower" : "net repayer"} color={todayRow.net_borrowing_bdt_mill > 0 ? "amber" : "blue"} />
          <StatCard label="6M Total Inflow" value={`${(totalInflow/1000).toFixed(0)}k mn`} sub={`vs ${(totalOutflow/1000).toFixed(0)}k mn outflow`} color="blue" />
        </div>
      )}

      <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
        <h2 className="text-sm font-medium text-gray-300 mb-4">Cash Flow Timeline (6 months history + 4 months ahead)</h2>
        <CashFlowChart data={flows} />
      </div>

      <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-medium text-gray-300">Upcoming Events (next 30 days)</h2>
          <DownloadButton data={flows} filename="cash_flows" />
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-gray-400 border-b border-gray-800">
                <th className="pb-2 pr-4">Date</th>
                <th className="pb-2 pr-4 text-right">Maturity (mn)</th>
                <th className="pb-2 pr-4 text-right">Coupon (mn)</th>
                <th className="pb-2 pr-4 text-right">Total Inflow (mn)</th>
                <th className="pb-2 pr-4 text-right">Auction Out (mn)</th>
                <th className="pb-2 pr-4 text-right">Net Borrowing (mn)</th>
                <th className="pb-2 text-center">Status</th>
              </tr>
            </thead>
            <tbody>
              {upcoming.map((r, i) => {
                const outflow = r.auction_outflow_confirmed_mill || r.auction_outflow_planned_mill;
                const isToday = r.flow_date === today;
                return (
                  <tr key={i} className={`border-b border-gray-800/50 hover:bg-gray-800/30 ${isToday ? "bg-blue-500/10" : ""}`}>
                    <td className="py-1.5 pr-4">
                      <Link href={`/drilldown?date=${r.flow_date}`} className="text-blue-400 hover:text-blue-300 hover:underline">
                        {fmtDate(r.flow_date)}{isToday && " ◀ today"}
                      </Link>
                    </td>
                    <td className="py-1.5 pr-4 text-right font-mono text-gray-300">{r.principal_inflow_bdt_mill.toLocaleString()}</td>
                    <td className="py-1.5 pr-4 text-right font-mono text-gray-300">{r.coupon_inflow_bdt_mill.toLocaleString()}</td>
                    <td className="py-1.5 pr-4 text-right font-mono text-white">{r.total_inflow_bdt_mill.toLocaleString()}</td>
                    <td className="py-1.5 pr-4 text-right font-mono text-red-400">{outflow.toLocaleString()}</td>
                    <td className={`py-1.5 pr-4 text-right font-mono ${r.net_borrowing_bdt_mill > 0 ? "text-amber-400" : "text-green-400"}`}>
                      {r.net_borrowing_bdt_mill > 0 ? "▲" : "▼"} {Math.abs(r.net_borrowing_bdt_mill).toLocaleString()}
                    </td>
                    <td className="py-1.5 text-center text-xs">{r.data_complete ? "✓" : <span className="text-amber-400">partial</span>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
