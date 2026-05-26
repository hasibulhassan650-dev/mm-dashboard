import { api } from "@/lib/api";
import StatCard from "@/components/StatCard";
import DownloadButton from "@/components/DownloadButton";

export const revalidate = 300;

export default async function SecuritiesPage() {
  const securities = await api.securities();

  const tbonds = securities.filter(s => s.security_type === "T_BOND");
  const tbills = securities.filter(s => s.security_type === "T_BILL");
  const frtb   = securities.filter(s => s.security_type === "FRTB");

  const totalOutstanding = securities.reduce((s, r) => s + (r.outstanding_bdt_mill ?? 0), 0);

  const today = new Date().toISOString().slice(0, 10);
  const maturing90 = securities.filter(s => s.maturity_date >= today &&
    s.maturity_date <= new Date(Date.now() + 90 * 86400000).toISOString().slice(0, 10));

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-white">Securities</h1>
        <p className="text-sm text-gray-400">GSOM — T-Bills, T-Bonds, FRTB outstanding</p>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Total Outstanding" value={`${(totalOutstanding / 1000).toFixed(0)}k cr`} sub="BDT million → crore equiv." color="blue" />
        <StatCard label="T-Bonds" value={String(tbonds.length)} sub="active securities" color="green" />
        <StatCard label="T-Bills" value={String(tbills.length)} sub="active securities" color="amber" />
        <StatCard label="Maturing (90d)" value={String(maturing90.length)} sub="securities maturing soon" color="red" />
      </div>

      {[
        { label: "Treasury Bonds (T-Bond)", items: tbonds },
        { label: "Fixed Rate Treasury Bonds (FRTB)", items: frtb },
        { label: "Treasury Bills (T-Bill)", items: tbills },
      ].map(({ label, items }) => items.length > 0 && (
        <div key={label} className="rounded-xl border border-gray-800 bg-gray-900 p-4">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-medium text-gray-300">{label} ({items.length})</h2>
            <DownloadButton data={items} filename={label.split(" ")[0].toLowerCase() + "_securities"} />
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-gray-400 border-b border-gray-800">
                  <th className="pb-2 pr-4">ISIN</th>
                  <th className="pb-2 pr-4">Name</th>
                  <th className="pb-2 pr-4 text-right">Coupon</th>
                  <th className="pb-2 pr-4">Issue Date</th>
                  <th className="pb-2 pr-4">Maturity</th>
                  <th className="pb-2 text-right">Outstanding (mill)</th>
                </tr>
              </thead>
              <tbody>
                {items.map(s => (
                  <tr key={s.isin} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                    <td className="py-1.5 pr-4 font-mono text-xs text-gray-300">{s.isin}</td>
                    <td className="py-1.5 pr-4 text-gray-200 max-w-[200px] truncate">{s.security_name_norm}</td>
                    <td className="py-1.5 pr-4 text-right text-white font-mono">{s.coupon_rate_pct?.toFixed(2) ?? "—"}%</td>
                    <td className="py-1.5 pr-4 text-gray-400 text-xs">{s.issue_date}</td>
                    <td className={`py-1.5 pr-4 text-xs ${s.maturity_date <= new Date(Date.now() + 90*86400000).toISOString().slice(0,10) ? "text-amber-400" : "text-gray-400"}`}>
                      {s.maturity_date}
                    </td>
                    <td className="py-1.5 text-right font-mono text-gray-300">{s.outstanding_bdt_mill?.toLocaleString() ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </div>
  );
}
