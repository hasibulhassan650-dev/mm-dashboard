import { Panel } from "@/components/terminal/ui";
import type { SukukBlock } from "@/lib/api";

/**
 * BD Govt Investment Sukuk, from BB's own outstanding table.
 *
 * A sukuk maturing is government cash going INTO the market, the same as a bond
 * redemption; it is in the cash ladder from the same table. Profit is shown only
 * for issues BB lists as half-yearly - an "on Maturity" issue pays one lump sum
 * that is not derivable, and a guessed figure is worse than a blank.
 */
const fmt = (v: number | null | undefined, d = 0) =>
  v == null ? "—" : v.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });

export default function SukukPanel({ data }: { data: SukukBlock | undefined }) {
  if (!data || data.error || !data.outstanding) {
    return (
      <Panel title="BGIS Sukuk (government Islamic sukuk)" span={12}>
        <p style={{ margin: 0, fontSize: 12.5, color: "var(--warn)" }}>
          The sukuk table is unavailable{data?.error ? ` (${data.error})` : ""}. Nothing is assumed in its place.
        </p>
      </Panel>
    );
  }
  const issues = data.outstanding;
  const up = (data.upcoming ?? []).slice(0, 14);
  const docs = data.documents ?? [];
  return (
    <>
      <Panel
        title="BGIS Sukuk — outstanding"
        sub={`${issues.length} issues · ${fmt(data.total_outstanding_crore)} cr · BB's own table · BDT crore`}
        span={12}
        pad={false}
      >
        <div className="table-wrap">
          <table className="dt">
            <thead><tr>
              <th>ISIN</th><th>Project</th><th>Tenor</th><th>Contract</th>
              <th>Issued</th><th>Matures</th><th className="r">Rate %</th>
              <th>Profit paid</th><th className="r">Outstanding (cr)</th>
            </tr></thead>
            <tbody>
              {issues.map((i) => (
                <tr key={i.isin}>
                  <td className="mono">{i.isin}</td>
                  <td style={{ whiteSpace: "normal", maxWidth: 320, padding: "6px var(--pad)" }}>{i.project}</td>
                  <td className="mono">{i.tenor_label ?? "—"}</td>
                  <td>{i.contract ?? "—"}</td>
                  <td className="mono">{i.issue_date}</td>
                  <td className="mono">{i.maturity_date}</td>
                  <td className="r mono">{i.profit_rate_pct == null ? "—" : i.profit_rate_pct.toFixed(2)}</td>
                  <td style={{ color: i.profit_derivable ? "var(--fg-dim)" : "var(--fg-mute)" }}>
                    {i.profit_frequency ?? "—"}</td>
                  <td className="r mono">{fmt(i.outstanding_crore)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel title="Sukuk cash coming in — next 12 months" sub="scheduled dates; cash settles on the next working day · BDT crore"
             span={6} pad={false}>
        <div className="table-wrap" style={{ maxHeight: 360, overflowY: "auto" }}>
          <table className="dt">
            <thead><tr><th>Date</th><th>Kind</th><th>Project</th><th className="r">Amount (cr)</th></tr></thead>
            <tbody>
              {up.length === 0 && (
                <tr><td colSpan={4} style={{ padding: "10px 12px", fontSize: 12.5, color: "var(--fg-mute)" }}>
                  Nothing scheduled in the next 12 months.</td></tr>
              )}
              {up.map((u, n) => (
                <tr key={`${u.isin}-${u.date}-${u.kind}-${n}`}>
                  <td className="mono">{u.date}</td>
                  <td style={{ color: u.kind === "PRINCIPAL" ? "var(--pos)" : "var(--fg-dim)" }}>
                    {u.kind === "PRINCIPAL" ? "principal" : "profit (derived)"}</td>
                  <td style={{ whiteSpace: "normal", maxWidth: 240, padding: "6px var(--pad)" }}>{u.project}</td>
                  <td className="r mono">{fmt(u.amount_crore, 2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div style={{ padding: "8px 12px", fontSize: 11.5, color: "var(--fg-mute)", borderTop: "1px solid var(--border)", lineHeight: 1.7 }}>
          {data.note}
        </div>
      </Panel>

      <Panel title="BB sukuk notices and prospectuses" sub="newest first · a new one is announced to you" span={6} pad={false}>
        <div className="table-wrap" style={{ maxHeight: 360, overflowY: "auto" }}>
          <table className="dt">
            <thead><tr><th>Date</th><th>Kind</th><th>Title</th><th /></tr></thead>
            <tbody>
              {docs.map((d) => (
                <tr key={d.doc_key}>
                  <td className="mono">{d.doc_date}</td>
                  <td>{d.kind === "NOTICE" ? "notice" : "prospectus"}</td>
                  <td style={{ whiteSpace: "normal", maxWidth: 300, padding: "6px var(--pad)" }}>{d.title}</td>
                  <td><a href={d.url} target="_blank" rel="noreferrer"
                         style={{ fontSize: 11, textDecoration: "none", color: "var(--info)" }}>PDF</a></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </>
  );
}
