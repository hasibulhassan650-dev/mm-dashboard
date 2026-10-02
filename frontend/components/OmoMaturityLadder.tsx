import type { OmoMaturityLadder as Ladder } from "@/lib/api";
import { exportUrl } from "@/lib/api";
import { Panel } from "@/components/terminal/ui";
import { fmtCrore, fmtDate } from "@/lib/format";
import { OMO_INSTRUMENTS } from "@/lib/terminal";

/**
 * What rolls off on which day, by product.
 *
 * /api/omo/outstanding gives the daily STOCK per instrument; this is the FLOW --
 * which tranche matures when -- and it is what tells the desk where the funding
 * cliffs are.
 *
 * Columns come from the ladder's own `instruments` list (what is actually in the
 * data), ordered by the OMO_INSTRUMENTS registry, with anything unrecognised
 * appended rather than dropped. #1 rule on this project: never lose a product.
 */
const ORDER = new Map(OMO_INSTRUMENTS.map((i, n) => [i.key, n]));
const LABEL = new Map(OMO_INSTRUMENTS.map((i) => [i.key, i.label]));

export default function OmoMaturityLadderPanel({ data }: { data: Ladder }) {
  if (!data.days.length) {
    return (
      <Panel title="OMO Maturity Ladder" span={12}>
        <p style={{ margin: 0, fontSize: 12.5, color: "var(--fg-mute)" }}>
          Nothing maturing in this window, or <code>/api/omo/maturity-ladder</code> is not
          deployed yet. No figures are shown rather than zeros.
        </p>
      </Panel>
    );
  }

  // registry order first, then any instrument the registry does not know
  const cols = [...data.instruments].sort(
    (a, b) => (ORDER.get(a) ?? 99) - (ORDER.get(b) ?? 99) || a.localeCompare(b));

  return (
    <Panel
      title="OMO Maturity Ladder — Day by Day"
      sub={`${data.from} → ${data.to} · BDT crore · what each product rolls off, and the running net`}
      span={12}
      pad={false}
      right={<a href={exportUrl.omoLadder({ from: data.from, to: data.to })}
                className="seg-b" style={{ textDecoration: "none" }}>Download Excel</a>}
    >
      <div className="table-wrap" style={{ maxHeight: 520, overflowY: "auto" }}>
        <table className="dt">
          <thead><tr>
            <th>Maturity Date</th><th>Day</th>
            {cols.map((c) => (
              <th key={c} className="r" title={c}>{LABEL.get(c) ?? c}</th>
            ))}
            <th className="r">Injects (cr)</th>
            <th className="r">Drains (cr)</th>
            <th className="r">Net (cr)</th>
            <th className="r">Cumulative (cr)</th>
          </tr></thead>
          <tbody>
            {data.days.map((d) => (
              <tr key={d.date} style={d.is_past ? { opacity: 0.62 } : undefined}>
                <td className="mono">{fmtDate(d.date)}</td>
                <td>{d.weekday}</td>
                {cols.map((c) => (
                  <td key={c} className="r mono">
                    {/* absent = blank, not 0: that product had nothing maturing */}
                    {d.by_instrument[c] ? fmtCrore(d.by_instrument[c]) : ""}
                  </td>
                ))}
                <td className="r mono pos">{d.inflow_crore ? fmtCrore(d.inflow_crore) : ""}</td>
                <td className="r mono neg">{d.outflow_crore ? fmtCrore(d.outflow_crore) : ""}</td>
                <td className="r mono" style={{ fontWeight: 600,
                      color: d.net_crore < 0 ? "var(--warn)" : "var(--info)" }}>
                  {fmtCrore(d.net_crore)}
                </td>
                <td className="r mono" style={{ fontWeight: 600 }}>{fmtCrore(d.cum_net_crore)}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr style={{ borderTop: "2px solid var(--border)", fontWeight: 600 }}>
              <td colSpan={2}>Total</td>
              {cols.map((c) => (
                <td key={c} className="r mono">{fmtCrore(data.instrument_totals[c] ?? 0)}</td>
              ))}
              <td className="r mono pos">{fmtCrore(data.total_inflow_crore)}</td>
              <td className="r mono neg">{fmtCrore(data.total_outflow_crore)}</td>
              <td className="r mono" style={{ color: data.total_net_crore < 0 ? "var(--warn)" : "var(--info)" }}>
                {fmtCrore(data.total_net_crore)}
              </td>
              <td></td>
            </tr>
          </tfoot>
        </table>
      </div>
      <p style={{ margin: 0, padding: "8px 12px", fontSize: 11.5, color: "var(--fg-mute)",
                  borderTop: "1px solid var(--border)", lineHeight: 1.7 }}>
        <b style={{ color: "var(--fg)" }}>An operation&rsquo;s effect at maturity is the reverse of
        its direction.</b> An SDF (absorption) maturing <b style={{ color: "var(--pos)" }}>injects</b>
        {" "}— BB returns the deposit. A repo, AR or IBLF (injection) maturing
        {" "}<b style={{ color: "var(--warn)" }}>drains</b> — the bank repays BB. Blank cells mean
        that product had nothing maturing that day. Derived from live tranches; BB&rsquo;s own
        printed maturities are reconciled against this weekly by the deep audit.
      </p>
    </Panel>
  );
}
