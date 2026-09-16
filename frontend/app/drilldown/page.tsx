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
          className="px-3 py-1.5 rounded b-panel2 hover-b-panel2 text-sm t-dim">◀ Prev</Link>
        <div>
          <h1 className="text-xl font-semibold t-fg">Date Drilldown — {date}</h1>
          <p className="text-sm t-dim">All cash flow events on this date</p>
        </div>
        <Link href={`/drilldown?date=${nextWorkday(date)}`}
          className="px-3 py-1.5 rounded b-panel2 hover-b-panel2 text-sm t-dim">Next ▶</Link>
        <Link href="/cashflows" className="ml-auto text-xs t-info hover:underline">← Back to Cash Flows</Link>
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
        <div className="rounded-xl border bd b-panel p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-medium t-dim">Maturities ({data.maturities.length})</h2>
            {data.maturities.length > 0 && <DownloadButton data={data.maturities} filename={`maturities_${date}`} label="Excel" />}
          </div>
          {data.maturities.length === 0
            ? <p className="text-xs t-mute">No maturities on this date</p>
            : <table className="w-full text-xs">
                <thead><tr className="t-dim border-b bd">
                  <th className="pb-1 pr-2 text-left">ISIN</th>
                  <th className="pb-1 pr-2 text-left">Name</th>
                  <th className="pb-1 text-right">Principal (mn)</th>
                </tr></thead>
                <tbody>
                  {data.maturities.map((m, i) => (
                    <tr key={i} className="border-b bd">
                      <td className="py-1 pr-2 font-mono t-dim">{m.isin}</td>
                      <td className="py-1 pr-2 t-dim truncate max-w-[120px]">{m.security_name_norm ?? m.isin}</td>
                      <td className="py-1 text-right t-fg font-mono">{(m.principal_bdt_mill ?? 0).toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
          }
        </div>

        {/* Coupons */}
        <div className="rounded-xl border bd b-panel p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-medium t-dim">Coupons ({data.coupons.length})</h2>
            {data.coupons.length > 0 && <DownloadButton data={data.coupons} filename={`coupons_${date}`} label="Excel" />}
          </div>
          {data.coupons.length === 0
            ? <p className="text-xs t-mute">No coupon payments on this date</p>
            : <table className="w-full text-xs">
                <thead><tr className="t-dim border-b bd">
                  <th className="pb-1 pr-2 text-left">ISIN</th>
                  <th className="pb-1 pr-2 text-right">Rate</th>
                  <th className="pb-1 text-right">Amount (mn)</th>
                </tr></thead>
                <tbody>
                  {data.coupons.map((c, i) => (
                    <tr key={i} className="border-b bd">
                      <td className="py-1 pr-2 font-mono t-dim">{c.isin}</td>
                      <td className="py-1 pr-2 text-right t-dim">{c.coupon_rate_used_pct}%</td>
                      <td className="py-1 text-right t-fg font-mono">{(c.amount_bdt_mill ?? 0).toFixed(4)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
          }
        </div>

        {/* Auctions */}
        <div className="rounded-xl border bd b-panel p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-medium t-dim">Auction Settlements ({data.auctions.length})</h2>
            {data.auctions.length > 0 && <DownloadButton data={data.auctions} filename={`auctions_${date}`} label="Excel" />}
          </div>
          {data.auctions.length === 0
            ? <p className="text-xs t-mute">No auction settlements on this date</p>
            : <table className="w-full text-xs">
                <thead><tr className="t-dim border-b bd">
                  <th className="pb-1 pr-2 text-left">Type</th>
                  <th className="pb-1 pr-2 text-left">Tenor</th>
                  <th className="pb-1 pr-2 text-right">Accepted (mn)</th>
                  <th className="pb-1 text-left">Status</th>
                </tr></thead>
                <tbody>
                  {data.auctions.map((a, i) => (
                    <tr key={i} className="border-b bd">
                      <td className="py-1 pr-2 t-dim">{a.security_type}</td>
                      <td className="py-1 pr-2 t-dim">{a.tenor_label}</td>
                      <td className="py-1 pr-2 text-right t-fg font-mono">
                        {a.accepted_amount_bdt_mill ? a.accepted_amount_bdt_mill.toLocaleString() : "PLANNED"}
                      </td>
                      <td className="py-1">
                        <span className={`px-1 py-0.5 rounded text-xs ${a.outflow_status === "CONFIRMED" ? "b-pos-soft t-pos" : "b-warn-soft t-warn"}`}>
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
