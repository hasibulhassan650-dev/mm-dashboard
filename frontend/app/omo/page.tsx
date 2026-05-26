import { api } from "@/lib/api";
import StatCard from "@/components/StatCard";
import OmoOutstandingChart from "@/components/OmoOutstandingChart";

export const revalidate = 300;

export default async function OmoPage() {
  const [summary, outstanding, txns] = await Promise.all([
    api.omoSummary(),
    api.omoOutstanding(90),
    api.omoTransactions(60),
  ]);

  const totalInj = summary.filter(r => r.direction === "INJECTION").reduce((s, r) => s + r.outstanding_bdt_crore, 0);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-white">Open Market Operations</h1>
        <p className="text-sm text-gray-400">BB press release data · outstanding positions and transactions</p>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {summary.map(r => (
          <StatCard
            key={r.instrument}
            label={r.instrument}
            value={`${r.outstanding_bdt_crore.toFixed(0)} cr`}
            sub={`${r.tranches} tranches · matures ${r.next_maturity}`}
            color={r.direction === "INJECTION" ? "blue" : "red"}
          />
        ))}
      </div>

      <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
        <h2 className="text-sm font-medium text-gray-300 mb-1">Outstanding by Instrument (90 days)</h2>
        <p className="text-xs text-gray-500 mb-4">Total injection outstanding: {totalInj.toFixed(0)} cr</p>
        <OmoOutstandingChart data={outstanding} />
      </div>

      <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
        <h2 className="text-sm font-medium text-gray-300 mb-4">Recent Transactions (last 60 days)</h2>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-gray-400 border-b border-gray-800">
                <th className="pb-2 pr-4">Date</th>
                <th className="pb-2 pr-4">Instrument</th>
                <th className="pb-2 pr-4">Tenor</th>
                <th className="pb-2 pr-4 text-right">Accepted (cr)</th>
                <th className="pb-2 pr-4 text-right">Rate</th>
                <th className="pb-2">Matures</th>
              </tr>
            </thead>
            <tbody>
              {txns.map((t, i) => (
                <tr key={i} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                  <td className="py-1.5 pr-4 text-gray-300">{t.transaction_date}</td>
                  <td className="py-1.5 pr-4">
                    <span className={`px-1.5 py-0.5 rounded text-xs ${t.direction === "INJECTION" ? "bg-blue-500/20 text-blue-300" : "bg-red-500/20 text-red-300"}`}>
                      {t.instrument}
                    </span>
                  </td>
                  <td className="py-1.5 pr-4 text-gray-300">{t.tenor_label}</td>
                  <td className="py-1.5 pr-4 text-right text-white font-mono">{t.accepted_bdt_crore.toFixed(2)}</td>
                  <td className="py-1.5 pr-4 text-right text-gray-300">{t.rate_pct != null ? `${t.rate_pct}%` : "—"}</td>
                  <td className="py-1.5 text-gray-400 text-xs">{t.maturity_date}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
