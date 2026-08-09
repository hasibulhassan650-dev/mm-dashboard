import { api } from "@/lib/api";
import RelatedLinks from "@/components/RelatedLinks";
import { fmtDate } from "@/lib/format";
import { Panel } from "@/components/terminal/ui";
import DownloadButton from "@/components/DownloadButton";
import Freshness from "@/components/Freshness";

export const revalidate = 300;

export default async function SecuritiesPage() {
  const [securities, fresh] = await Promise.all([api.securities().catch(() => []), api.freshness()]);
  const tbonds = securities.filter((s) => s.security_type === "T_BOND");
  const tbills = securities.filter((s) => s.security_type === "T_BILL");
  const frtb = securities.filter((s) => s.security_type === "FRTB");
  const totalOut = securities.reduce((s, r) => s + (r.outstanding_bdt_mill ?? 0), 0);
  const now = new Date();
  const today = now.toISOString().slice(0, 10);
  const cutoff90 = new Date(now.getTime() + 90 * 86400000).toISOString().slice(0, 10);
  const maturing90 = securities.filter((s) => s.maturity_date >= today && s.maturity_date <= cutoff90);

  const groups = [
    { label: "Treasury Bonds (T-Bond)", items: tbonds, file: "tbond_securities" },
    { label: "Fixed Rate Treasury Bonds (FRTB)", items: frtb, file: "frtb_securities" },
    { label: "Treasury Bills (T-Bill)", items: tbills, file: "tbill_securities" },
  ];

  return (
    <>
      <div style={{ marginBottom: "var(--gap)" }}><Freshness updated={fresh.securities} /></div>

      <div className="kpi-strip" style={{ gridTemplateColumns: "repeat(4,1fr)" }}>
        <div className="kpi"><div className="kpi-top"><span className="kpi-label">Total Outstanding</span></div><div className="kpi-val"><span className="kpi-cur">৳</span><span className="kpi-num">{(totalOut / 1000).toFixed(0)}k</span><span className="kpi-unit">cr</span></div><div className="kpi-sub">face value</div></div>
        <div className="kpi"><div className="kpi-top"><span className="kpi-label">T-Bonds</span></div><div className="kpi-val"><span className="kpi-num" style={{ color: "var(--info)" }}>{tbonds.length}</span></div><div className="kpi-sub">active</div></div>
        <div className="kpi"><div className="kpi-top"><span className="kpi-label">T-Bills</span></div><div className="kpi-val"><span className="kpi-num" style={{ color: "var(--accent)" }}>{tbills.length}</span></div><div className="kpi-sub">active</div></div>
        <div className="kpi"><div className="kpi-top"><span className="kpi-label">Maturing (90d)</span></div><div className="kpi-val"><span className="kpi-num neg">{maturing90.length}</span></div><div className="kpi-sub">soon</div></div>
      </div>

      <div className="grid12">
        {groups.filter((g) => g.items.length > 0).map((g) => (
          <Panel key={g.label} title={g.label} sub={`${g.items.length} securities`} span={12} pad={false}
            right={<DownloadButton data={g.items} filename={g.file} />}>
            <div className="table-wrap" style={{ maxHeight: 380, overflowY: "auto" }}>
              <table className="dt">
                <thead><tr><th>ISIN</th><th>Name</th><th className="r">Coupon</th><th>Issue</th><th>Maturity</th><th className="r">Outstanding (mn)</th></tr></thead>
                <tbody>
                  {g.items.map((s) => (
                    <tr key={s.isin}>
                      <td className="mono">{s.isin}</td>
                      <td style={{ maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis" }}>{s.security_name_norm}</td>
                      <td className="r mono">{s.coupon_rate_pct?.toFixed(2) ?? "—"}%</td>
                      <td style={{ fontSize: 11 }}>{fmtDate(s.issue_date)}</td>
                      <td style={{ fontSize: 11, color: s.maturity_date <= cutoff90 ? "var(--warn)" : undefined }}>{fmtDate(s.maturity_date)}</td>
                      <td className="r mono">{s.outstanding_bdt_mill?.toLocaleString() ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
        ))}
      </div>
      <RelatedLinks items={[
        { href: "/yields", label: "Yields", why: "the curve these securities price on" },
        { href: "/cashflows", label: "Cash Flows", why: "their coupons & maturities" },
        { href: "/portfolio", label: "Portfolio", why: "value your holdings of them" },
      ]} />
    </>
  );
}
