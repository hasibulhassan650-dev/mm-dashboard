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
      title="OMO Ladder — Day by Day"
      sub={`${data.from} → ${data.to} · BDT crore · what each product rolls off, what BB dealt, and the true net flow`}
      span={12}
      pad={false}
      right={<a href={exportUrl.omoLadder({ from: data.from, to: data.to })}
                className="seg-b" style={{ textDecoration: "none" }}>Download Excel</a>}
    >
      <div className="table-wrap" style={{ maxHeight: 520, overflowY: "auto" }}>
        <table className="dt">
          <thead>
            <tr>
              <th rowSpan={2}>Date</th><th rowSpan={2}>Day</th>
              <th colSpan={cols.length + 1} className="r">Maturing — rolls off (cr)</th>
              <th colSpan={2} className="r">Dealt by BB (cr)</th>
              <th rowSpan={2} className="r" title="All four legs: what actually happened to liquidity">
                Net OMO flow (cr)
              </th>
              <th rowSpan={2} className="r">Cumulative flow (cr)</th>
              <th rowSpan={2}>Basis</th>
            </tr>
            <tr>
              {cols.map((c) => (
                <th key={c} className="r" title={c}>{LABEL.get(c) ?? c}</th>
              ))}
              <th className="r" title="Maturity legs only — the funding cliff, not a liquidity net">
                Net roll-off
              </th>
              <th className="r" title="New repo / AR / IBLF struck that day — cash out to banks">
                Injected
              </th>
              <th className="r" title="New SDF taken that day — banks park cash at BB">
                Absorbed
              </th>
            </tr>
          </thead>
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
                <td className="r mono" style={{ color: d.roll_net_crore < 0 ? "var(--neg)" : "var(--pos)" }}>
                  {fmtCrore(d.roll_net_crore)}
                </td>
                <td className="r mono pos">
                  {d.omo_coverage === "complete"
                    ? (d.new_inflow_crore ? fmtCrore(d.new_inflow_crore) : "")
                    : <span style={{ color: "var(--info)" }}>—</span>}
                </td>
                <td className="r mono neg">
                  {d.omo_coverage === "complete"
                    ? (d.new_outflow_crore ? fmtCrore(d.new_outflow_crore) : "")
                    : <span style={{ color: "var(--info)" }}>—</span>}
                </td>
                <td className="r mono" style={{ fontWeight: 600,
                      color: d.net_crore < 0 ? "var(--warn)" : "var(--info)" }}>
                  {fmtCrore(d.net_crore)}
                </td>
                <td className="r mono" style={{ fontWeight: 600 }}>{fmtCrore(d.cum_net_crore)}</td>
                <td style={{ fontSize: 10.5 }}>
                  {d.omo_coverage === "complete"
                    ? <span style={{ color: "var(--pos)" }}>full flow</span>
                    : <span style={{ color: "var(--warn)" }}
                            title={d.omo_coverage === "future"
                              ? "BB has not acted yet — maturity legs only"
                              : "BB has not published this day's operations yet — maturity legs only"}>
                        roll-off only
                      </span>}
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr style={{ borderTop: "2px solid var(--border)", fontWeight: 600 }}>
              <td colSpan={2}>Total</td>
              {cols.map((c) => (
                <td key={c} className="r mono">{fmtCrore(data.instrument_totals[c] ?? 0)}</td>
              ))}
              <td className="r mono">{fmtCrore(data.total_roll_net_crore)}</td>
              <td className="r mono pos">{fmtCrore(data.total_new_inflow_crore)}</td>
              <td className="r mono neg">{fmtCrore(data.total_new_outflow_crore)}</td>
              <td className="r mono" style={{ color: data.total_net_crore < 0 ? "var(--warn)" : "var(--info)" }}>
                {fmtCrore(data.total_net_crore)}
              </td>
              <td colSpan={2}></td>
            </tr>
          </tfoot>
        </table>
      </div>
      <p style={{ margin: 0, padding: "8px 12px", fontSize: 11.5, color: "var(--fg-mute)",
                  borderTop: "1px solid var(--border)", lineHeight: 1.7 }}>
        <b style={{ color: "var(--fg)" }}>Every tranche moves liquidity twice, with opposite
        signs.</b> On its <i>deal</i> date a new repo/AR/IBLF <b style={{ color: "var(--pos)" }}>
        injects</b> and a new SDF <b style={{ color: "var(--warn)" }}>absorbs</b>. At
        {" "}<i>maturity</i> the signs reverse — the repo is repaid so cash leaves, the SDF is
        returned so cash arrives. <b style={{ color: "var(--fg)" }}>Net roll-off</b> is the
        maturity legs only, the funding cliff; <b style={{ color: "var(--fg)" }}>Net OMO flow</b> is
        all four legs, what actually happened to liquidity. Where BB has not published a day&rsquo;s
        operations the dealt columns read &ldquo;—&rdquo; and the basis says
        {" "}<i>roll-off only</i> — absent, not zero. Blank product cells mean that product had
        nothing on that leg. Derived from live tranches; BB&rsquo;s printed maturities are
        reconciled against this weekly by the deep audit.
      </p>
    </Panel>
  );
}
