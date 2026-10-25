import { Panel } from "@/components/terminal/ui";
import { OMO_INSTRUMENTS } from "@/lib/terminal";
import type { OmoInjection } from "@/lib/api";

/**
 * How much is Bangladesh Bank injecting, facility by facility.
 *
 * A stock, measured on the last day BB published its operations - not today and
 * not a sum of what was dealt (which counts every rolled tranche twice). Rows
 * come from the API, so a facility nobody listed still appears. Government cash
 * (subsidy releases, arrears) is NOT here: it is not in BB's data.
 */
const LABEL: Record<string, string> = Object.fromEntries(OMO_INSTRUMENTS.map((i) => [i.key, i.label]));
const fmt = (v: number | null | undefined, d = 0) =>
  v == null ? "—" : v.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });

export default function OmoInjectionPanel({ data }: { data: OmoInjection | null }) {
  if (!data || !data.totals || !data.as_of) {
    return (
      <Panel title="BB liquidity injection" span={12}>
        <p style={{ margin: 0, fontSize: 12.5, color: "var(--fg-mute)" }}>
          The injection summary is unavailable right now. Nothing is assumed in its place.
        </p>
      </Panel>
    );
  }
  const t = data.totals;
  const kpi = (label: string, value: string, sub?: string, tone?: string) => (
    <div style={{ minWidth: 150 }}>
      <div style={{ fontSize: 10.5, textTransform: "uppercase", letterSpacing: ".08em", color: "var(--fg-mute)" }}>{label}</div>
      <div className="mono" style={{ fontSize: 20, fontWeight: 700, color: tone ?? "var(--fg)", marginTop: 2 }}>{value}</div>
      {sub && <div style={{ fontSize: 10.5, color: "var(--fg-mute)" }}>{sub}</div>}
    </div>
  );
  return (
    <Panel
      title="BB liquidity injection (OMO)"
      sub={`as of ${data.as_of} — the last day BB published; later days are not yet known · BDT crore`}
      span={12}
      pad={false}
    >
      <div style={{ display: "flex", flexWrap: "wrap", gap: 28, padding: "12px 16px", borderBottom: "1px solid var(--border)" }}>
        {kpi("Injected (outstanding)", fmt(t.injected_crore), `${fmt(t.injected_maturing_7d_crore)} rolls off in 7d`, "var(--pos)")}
        {kpi("Absorbed (SDF)", fmt(t.absorbed_crore), undefined, "var(--warn)")}
        {kpi("Net injected", fmt(t.net_injected_crore), "injected less absorbed")}
        {kpi("Islamic (IBLF + MLS)", fmt(t.islamic_injected_crore), `${fmt(100 * t.islamic_injected_crore / (t.injected_crore || 1), 1)}% of injected`)}
        {kpi("Conventional", fmt(t.conventional_injected_crore))}
      </div>
      <div className="table-wrap">
        <table className="dt">
          <thead><tr>
            <th>Facility</th><th>Side</th>
            <th className="r">Outstanding</th><th className="r">% of injected</th>
            <th className="r">Dealt {data.window_days}d</th><th className="r">Rate %</th>
            <th>Next maturity</th><th className="r">Rolls off 7d</th><th className="r">Rolls off 30d</th>
          </tr></thead>
          <tbody>
            {data.instruments.map((r) => (
              <tr key={r.instrument}>
                <td><b style={{ color: "var(--fg)" }}>{LABEL[r.instrument] ?? r.instrument}</b>
                  {r.islamic && <span style={{ marginLeft: 6, fontSize: 10, color: "var(--info)" }}>Islamic</span>}</td>
                <td style={{ color: r.direction === "INJECTION" ? "var(--pos)" : "var(--warn)" }}>
                  {r.direction === "INJECTION" ? "injects" : "absorbs"}</td>
                <td className="r mono">{fmt(r.outstanding_crore)}</td>
                <td className="r mono">{r.share_of_injected_pct == null ? "" : `${r.share_of_injected_pct.toFixed(1)}%`}</td>
                <td className="r mono">{fmt(r.dealt_window_crore)}</td>
                <td className="r mono">{r.wtd_rate_pct == null ? "—" : r.wtd_rate_pct.toFixed(2)}</td>
                <td className="mono">{r.next_maturity ?? "—"}</td>
                <td className="r mono">{fmt(r.maturing_7d_crore)}</td>
                <td className="r mono">{fmt(r.maturing_30d_crore)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div style={{ padding: "8px 16px", fontSize: 11.5, color: "var(--fg-mute)", borderTop: "1px solid var(--border)", lineHeight: 1.7 }}>
        Outstanding is the stock of tranches dealt and not yet matured. “Dealt” is what was struck in the window and
        is shown beside the stock, never added to it. This is BB&apos;s injection through its operations; government
        cash such as subsidy releases and arrears is not in BB&apos;s data and is tracked on Liquidity Watch.
      </div>
    </Panel>
  );
}
