import { api } from "@/lib/api";
import StatCard from "@/components/StatCard";
import YieldCurveChart from "@/components/YieldCurveChart";
import OmoOutstandingChart from "@/components/OmoOutstandingChart";

export const revalidate = 300;

function fmt(n: number) {
  if (n >= 1000) return (n / 1000).toFixed(1) + "k";
  return n.toFixed(0);
}

export default async function Home() {
  const [summary, curve, outstanding] = await Promise.all([
    api.omoSummary(),
    api.yieldCurve(),
    api.omoOutstanding(60),
  ]);

  const totalInjection = summary
    .filter(r => r.direction === "INJECTION")
    .reduce((s, r) => s + r.outstanding_bdt_crore, 0);

  const tenYear = curve.find(r => r.tenor_label === "10Y");
  const tbill91 = curve.find(r => r.tenor_label === "91D");

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-white">Overview</h1>
        <p className="text-sm text-gray-400">Bangladesh money market · live data from Bangladesh Bank</p>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="OMO Net Injection" value={`৳${fmt(totalInjection)} cr`} sub="outstanding today" color="blue" />
        <StatCard label="10Y T-Bond Yield" value={tenYear ? `${tenYear.cutoff_yield_pct.toFixed(2)}%` : "—"} sub={tenYear?.auction_date ?? ""} color="green" />
        <StatCard label="91D T-Bill Yield" value={tbill91 ? `${tbill91.cutoff_yield_pct.toFixed(2)}%` : "—"} sub={tbill91?.auction_date ?? ""} color="amber" />
        <StatCard label="OMO Instruments" value={String(summary.length)} sub={`${summary.filter(r => r.direction === "INJECTION").length} injection active`} color="blue" />
      </div>

      <div className="grid md:grid-cols-2 gap-6">
        <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
          <h2 className="text-sm font-medium text-gray-300 mb-4">Yield Curve</h2>
          <YieldCurveChart data={curve} />
        </div>
        <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
          <h2 className="text-sm font-medium text-gray-300 mb-4">OMO Outstanding (60 days)</h2>
          <OmoOutstandingChart data={outstanding} />
        </div>
      </div>
    </div>
  );
}
