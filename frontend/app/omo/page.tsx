import { api } from "@/lib/api";
import StatCard from "@/components/StatCard";
import OmoOutstandingChart from "@/components/OmoOutstandingChart";
import OmoNetLiquidityChart from "@/components/OmoNetLiquidityChart";
import Freshness from "@/components/Freshness";
import InfoTip from "@/components/InfoTip";
import { fmtDate, fmtCrore } from "@/lib/format";
import { netLiquiditySeries } from "@/lib/analytics";
import DownloadButton from "@/components/DownloadButton";

export const revalidate = 300;

export default async function OmoPage() {
  const [summary, outstanding, txns, fresh] = await Promise.all([
    api.omoSummary(),
    api.omoOutstanding(90),
    api.omoTransactions(60),
    api.freshness(),
  ]);

  const totalInj = summary.filter(r => r.direction === "INJECTION").reduce((s, r) => s + r.outstanding_bdt_crore, 0);

  const netSeries = netLiquiditySeries(outstanding);
  const latestNet = netSeries.at(-1)?.net ?? null;
  const stance =
    latestNet == null ? null
    : latestNet > 0 ? { label: "Tight — BB net-injecting", cls: "text-amber-400 border-amber-800/60 bg-amber-950/30" }
    : { label: "Flush — BB net-absorbing", cls: "text-green-400 border-green-800/60 bg-green-950/30" };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-white">Open Market Operations</h1>
        <p className="text-sm text-gray-400">BB press release data · outstanding positions and transactions</p>
        <div className="mt-1"><Freshness updated={fresh.omo} /></div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {summary.map(r => (
          <StatCard
            key={r.instrument}
            label={r.instrument}
            value={fmtCrore(r.outstanding_bdt_crore)}
            sub={`${r.tranches} tranches · matures ${fmtDate(r.next_maturity)}`}
            color={r.direction === "INJECTION" ? "blue" : "red"}
          />
        ))}
      </div>

      <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
        <h2 className="text-sm font-medium text-gray-300 mb-1">Outstanding by Instrument (90 days)</h2>
        <p className="text-xs text-gray-500 mb-4">Total injection outstanding: {fmtCrore(totalInj)}</p>
        <OmoOutstandingChart data={outstanding} />
      </div>

      <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
        <div className="flex items-center justify-between mb-1 flex-wrap gap-2">
          <h2 className="text-sm font-medium text-gray-300 flex items-center">
            Net Liquidity Stance<InfoTip term="OMO" />
          </h2>
          {stance && (
            <span className={`text-xs font-medium px-2 py-0.5 rounded border ${stance.cls}`}>
              {stance.label} · {fmtCrore(latestNet)}
            </span>
          )}
        </div>
        <p className="text-xs text-gray-500 mb-4">
          Injection outstanding minus absorption, per day. Above zero = BB adding net liquidity (system short / tight);
          below zero = BB draining (flush).
        </p>
        <OmoNetLiquidityChart rows={outstanding} />
      </div>

      <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-medium text-gray-300">Recent Transactions (last 60 days)</h2>
          <DownloadButton data={txns} filename="omo_transactions" />
        </div>
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
                  <td className="py-1.5 pr-4 text-gray-300">{fmtDate(t.transaction_date)}</td>
                  <td className="py-1.5 pr-4">
                    <span className={`px-1.5 py-0.5 rounded text-xs ${t.direction === "INJECTION" ? "bg-blue-500/20 text-blue-300" : "bg-red-500/20 text-red-300"}`}>
                      {t.instrument}
                    </span>
                  </td>
                  <td className="py-1.5 pr-4 text-gray-300">{t.tenor_label}</td>
                  <td className="py-1.5 pr-4 text-right text-white font-mono">{t.accepted_bdt_crore.toFixed(2)}</td>
                  <td className="py-1.5 pr-4 text-right text-gray-300">{t.rate_pct != null ? `${t.rate_pct}%` : "—"}</td>
                  <td className="py-1.5 text-gray-400 text-xs">{fmtDate(t.maturity_date)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
