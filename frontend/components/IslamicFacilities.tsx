import { Panel } from "@/components/terminal/ui";
import Link from "next/link";
import type { OmoInjection, OmoTxnRow } from "@/lib/api";

/**
 * BB's liquidity to the Islamic banks: IBLF (Islamic Banks Liquidity Facility)
 * and MLS (Mudaraba Liquidity Support). They sit in the OMO data like every other
 * facility, so this is a view of it, not a second source - the figures here and
 * on /omo are the same rows.
 */
const FACILITIES = new Set(["IBLF", "MLS"]);
const NAME: Record<string, string> = { IBLF: "IBLF — Islamic Banks Liquidity Facility", MLS: "MLS — Mudaraba Liquidity Support" };
const fmt = (v: number | null | undefined, d = 0) =>
  v == null ? "—" : v.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });

export default function IslamicFacilities({ inj, txns }: { inj: OmoInjection | null; txns: OmoTxnRow[] }) {
  const rows = (inj?.instruments ?? []).filter((r) => FACILITIES.has(r.instrument));
  const ops = txns.filter((t) => FACILITIES.has(t.instrument) && t.accepted_bdt_crore > 0).slice(0, 15);
  if (!inj?.as_of) {
    return (
      <Panel title="Islamic liquidity facilities (IBLF, MLS)" span={12}>
        <p style={{ margin: 0, fontSize: 12.5, color: "var(--fg-mute)" }}>OMO data is unavailable right now; nothing is assumed in its place.</p>
      </Panel>
    );
  }
  return (
    <Panel
      title="Islamic liquidity facilities — BB to Islamic banks"
      sub={`outstanding as of ${inj.as_of} · BDT crore · same rows as the OMO page`}
      span={12}
      pad={false}
      right={<Link href="/omo" style={{ fontSize: 11.5, color: "var(--info)", textDecoration: "none" }}>All OMO facilities →</Link>}
    >
      <div className="table-wrap">
        <table className="dt">
          <thead><tr>
            <th>Facility</th><th className="r">Outstanding</th><th className="r">% of injected</th>
            <th className="r">Dealt {inj.window_days}d</th><th className="r">Rate %</th>
            <th>Next maturity</th><th className="r">Rolls off 30d</th>
          </tr></thead>
          <tbody>
            {rows.length === 0 && (
              <tr><td colSpan={7} style={{ padding: "10px 12px", fontSize: 12.5, color: "var(--fg-mute)" }}>
                No IBLF or MLS tranche is outstanding as of {inj.as_of}.</td></tr>
            )}
            {rows.map((r) => (
              <tr key={r.instrument}>
                <td><b style={{ color: "var(--fg)" }}>{NAME[r.instrument] ?? r.instrument}</b></td>
                <td className="r mono">{fmt(r.outstanding_crore)}</td>
                <td className="r mono">{r.share_of_injected_pct == null ? "" : `${r.share_of_injected_pct.toFixed(1)}%`}</td>
                <td className="r mono">{fmt(r.dealt_window_crore)}</td>
                <td className="r mono">{r.wtd_rate_pct == null ? "—" : r.wtd_rate_pct.toFixed(2)}</td>
                <td className="mono">{r.next_maturity ?? "—"}</td>
                <td className="r mono">{fmt(r.maturing_30d_crore)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="table-wrap" style={{ maxHeight: 320, overflowY: "auto", borderTop: "1px solid var(--border)" }}>
        <table className="dt">
          <thead><tr>
            <th>Dealt</th><th>Facility</th><th>Tenor</th><th className="r">Accepted (cr)</th>
            <th className="r">Rate %</th><th>Matures</th>
          </tr></thead>
          <tbody>
            {ops.length === 0 && (
              <tr><td colSpan={6} style={{ padding: "10px 12px", fontSize: 12.5, color: "var(--fg-mute)" }}>
                No IBLF or MLS operation in the last 90 days.</td></tr>
            )}
            {ops.map((t, i) => (
              <tr key={`${t.transaction_date}-${t.instrument}-${t.maturity_date}-${i}`}>
                <td className="mono">{String(t.transaction_date).slice(0, 10)}</td>
                <td>{t.instrument}</td>
                <td className="mono">{t.tenor_label}</td>
                <td className="r mono">{fmt(t.accepted_bdt_crore, 2)}</td>
                <td className="r mono">{t.rate_pct == null ? "—" : t.rate_pct.toFixed(2)}</td>
                <td className="mono">{String(t.maturity_date).slice(0, 10)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}
