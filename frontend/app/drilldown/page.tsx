import { api } from "@/lib/api";
import StatCard from "@/components/StatCard";
import DownloadButton from "@/components/DownloadButton";
import Link from "next/link";

export const revalidate = 60;

function prevWorkday(date: string): string {
  const d = new Date(date);
  do { d.setDate(d.getDate() - 1); } while (d.getDay() === 5 || d.getDay() === 6); // skip Fri+Sat
  return d.toISOString().slice(0, 10);
}
function nextWorkday(date: string): string {
  const d = new Date(date);
  do { d.setDate(d.getDate() + 1); } while (d.getDay() === 5 || d.getDay() === 6);
  return d.toISOString().slice(0, 10);
}

export default async function DrilldownPage({
  searchParams,
}: {
  searchParams: Promise<{ date?: string }>;
}) {
  const params = await searchParams;
  const date = params.date ?? new Date().toISOString().slice(0, 10);
  const data = await api.drilldown(date);
  const s = data.summary;

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-4">
        <Link href={`/drilldown?date=${prevWorkday(date)}`}
          className="px-3 py-1.5 rounded bg-gray-800 hover:bg-gray-700 text-sm text-gray-300">◀ Prev</Link>
        <div>
          <h1 className="text-xl font-semibold text-white">Date Drilldown — {date}</h1>
          <p className="text-sm text-gray-400">All cash flow events on this date</p>
        </div>
        <Link href={`/drilldown?date=${nextWorkday(date)}`}
          className="px-3 py-1.5 rounded bg-gray-800 hover:bg-gray-700 text-sm text-gray-300">Next ▶</Link>
        <Link href="/cashflows" className="ml-auto text-xs text-blue-400 hover:underline">← Back to Cash Flows</Link>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        <StatCard label="Maturity Inflow" value={`${s.maturity_inflow_mill.toLocaleString()}`} sub="BDT million" color="green" />
        <StatCard label="Coupon Inflow"   value={`${s.coupon_inflow_mill.toLocaleString()}`}   sub="BDT million" color="green" />
        <StatCard label="Total Inflow"    value={`${s.total_inflow_mill.toLocaleString()}`}    sub="BDT million" color="blue" />
        <StatCard label="Auction Outflow" value={`${s.auction_outflow_mill.toLocaleString()}`} sub="BDT million" color="red" />
        <StatCard label="Net Borrowing"   value={`${s.net_borrowing_mill > 0 ? "▲" : "▼"} ${Math.abs(s.net_borrowing_mill).toLocaleString()}`}
          sub={s.net_borrowing_mill > 0 ? "net borrower" : "net repayer"}
          color={s.net_borrowing_mill > 0 ? "amber" : "green"} />
      </div>

      <div className="grid md:grid-cols-3 gap-6">
        {/* Maturities */}
        <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-medium text-gray-300">Maturities ({data.maturities.length})</h2>
            {data.maturities.length > 0 && <DownloadButton data={data.maturities} filename={`maturities_${date}`} label="Excel" />}
          </div>
          {data.maturities.length === 0
            ? <p className="text-xs text-gray-500">No maturities on this date</p>
            : <table className="w-full text-xs">
                <thead><tr className="text-gray-400 border-b border-gray-800">
                  <th className="pb-1 pr-2 text-left">ISIN</th>
                  <th className="pb-1 pr-2 text-left">Name</th>
                  <th className="pb-1 text-right">Principal (mn)</th>
                </tr></thead>
                <tbody>
                  {data.maturities.map((m, i) => (
                    <tr key={i} className="border-b border-gray-800/40">
                      <td className="py-1 pr-2 font-mono text-gray-400">{m.isin}</td>
                      <td className="py-1 pr-2 text-gray-300 truncate max-w-[120px]">{m.security_name_norm ?? m.isin}</td>
                      <td className="py-1 text-right text-white font-mono">{(m.principal_bdt_mill ?? 0).toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
          }
        </div>

        {/* Coupons */}
        <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-medium text-gray-300">Coupons ({data.coupons.length})</h2>
            {data.coupons.length > 0 && <DownloadButton data={data.coupons} filename={`coupons_${date}`} label="Excel" />}
          </div>
          {data.coupons.length === 0
            ? <p className="text-xs text-gray-500">No coupon payments on this date</p>
            : <table className="w-full text-xs">
                <thead><tr className="text-gray-400 border-b border-gray-800">
                  <th className="pb-1 pr-2 text-left">ISIN</th>
                  <th className="pb-1 pr-2 text-right">Rate</th>
                  <th className="pb-1 text-right">Amount (mn)</th>
                </tr></thead>
                <tbody>
                  {data.coupons.map((c, i) => (
                    <tr key={i} className="border-b border-gray-800/40">
                      <td className="py-1 pr-2 font-mono text-gray-400">{c.isin}</td>
                      <td className="py-1 pr-2 text-right text-gray-300">{c.coupon_rate_used_pct}%</td>
                      <td className="py-1 text-right text-white font-mono">{(c.amount_bdt_mill ?? 0).toFixed(4)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
          }
        </div>

        {/* Auctions */}
        <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-medium text-gray-300">Auction Settlements ({data.auctions.length})</h2>
            {data.auctions.length > 0 && <DownloadButton data={data.auctions} filename={`auctions_${date}`} label="Excel" />}
          </div>
          {data.auctions.length === 0
            ? <p className="text-xs text-gray-500">No auction settlements on this date</p>
            : <table className="w-full text-xs">
                <thead><tr className="text-gray-400 border-b border-gray-800">
                  <th className="pb-1 pr-2 text-left">Type</th>
                  <th className="pb-1 pr-2 text-left">Tenor</th>
                  <th className="pb-1 pr-2 text-right">Accepted (mn)</th>
                  <th className="pb-1 text-left">Status</th>
                </tr></thead>
                <tbody>
                  {data.auctions.map((a, i) => (
                    <tr key={i} className="border-b border-gray-800/40">
                      <td className="py-1 pr-2 text-gray-300">{a.security_type}</td>
                      <td className="py-1 pr-2 text-gray-300">{a.tenor_label}</td>
                      <td className="py-1 pr-2 text-right text-white font-mono">
                        {a.accepted_amount_bdt_mill ? a.accepted_amount_bdt_mill.toLocaleString() : "PLANNED"}
                      </td>
                      <td className="py-1">
                        <span className={`px-1 py-0.5 rounded text-xs ${a.outflow_status === "CONFIRMED" ? "bg-green-500/20 text-green-300" : "bg-amber-500/20 text-amber-300"}`}>
                          {a.outflow_status}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
          }
        </div>
      </div>
    </div>
  );
}
