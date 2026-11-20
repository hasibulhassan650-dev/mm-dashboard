import Link from "next/link";
import type { Insights, InsightStatement } from "@/lib/api";
import { parseTs } from "@/lib/format";
import { Panel } from "@/components/terminal/ui";

/**
 * The part of the Liquidity Watch that answers a desk's first question: is liquidity loose or tight, why, and what is scheduled next.
 * Computed by api/routers/insights_logic.py from Bangladesh Bank's own data; every figure carries its date, an unknown is shown as
 * unknown (never 0), and news-derived events are listed beside the numbers and never inside them.
 */
const n0 = (v: number | null | undefined) =>
  v == null ? "—" : v.toLocaleString(undefined, { maximumFractionDigits: 0 });
const signed = (v: number | null | undefined) =>
  v == null ? "—" : `${v > 0 ? "+" : ""}${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;

const REGIME_COLOR: Record<string, string> = {
  TIGHT: "var(--neg)", SURPLUS: "var(--info)", BALANCED: "var(--pos)", UNKNOWN: "var(--warn)",
};
const SEV_COLOR: Record<InsightStatement["severity"], string> = {
  alert: "var(--neg)", watch: "var(--warn)", info: "var(--fg-mute)",
};

function CorridorBar({ regime }: { regime: Insights["regime"] }) {
  const c = regime.inputs.corridor, call = regime.inputs.call;
  if (!c || !call || c.slf == null || c.sdf == null || c.repo == null || c.slf <= c.sdf) return null;
  const pct = (v: number) => Math.max(0, Math.min(100, ((v - c.sdf!) / (c.slf! - c.sdf!)) * 100));
  const mark = (v: number, label: string, color: string, top: boolean) => (
    <div style={{ position: "absolute", left: `${pct(v)}%`, top: top ? -4 : 14, transform: "translateX(-50%)",
                  fontSize: 10.5, color, whiteSpace: "nowrap", textAlign: "center" }}>
      {top ? "" : label}
    </div>
  );
  return (
    <div style={{ margin: "14px 6px 30px", position: "relative", height: 10 }} aria-label="policy corridor with the overnight call rate">
      <div style={{ position: "absolute", inset: 0, borderRadius: 5, background: "var(--panel-2, var(--border))",
                    border: "1px solid var(--border)" }} />
      {[[c.sdf, `SDF ${c.sdf.toFixed(2)}`], [c.repo, `repo ${c.repo.toFixed(2)}`], [c.slf, `SLF ${c.slf.toFixed(2)}`]].map(([v, l]) => (
        <div key={String(l)}>
          <div style={{ position: "absolute", left: `${pct(v as number)}%`, top: -2, bottom: -2, width: 2, background: "var(--fg-mute)" }} />
          {mark(v as number, l as string, "var(--fg-mute)", false)}
        </div>
      ))}
      <div title={`overnight call ${call.rate.toFixed(2)}%`} style={{
        position: "absolute", left: `${pct(call.rate)}%`, top: -5, width: 14, height: 14, borderRadius: 7, transform: "translateX(-50%)",
        background: REGIME_COLOR[regime.label], border: "2px solid var(--panel)" }} />
      <div style={{ position: "absolute", left: `${pct(call.rate)}%`, top: -22, transform: "translateX(-50%)", fontSize: 11.5,
                    fontWeight: 700, color: REGIME_COLOR[regime.label], whiteSpace: "nowrap" }}>
        call {call.rate.toFixed(2)}%{call.provisional ? " (provisional)" : ""}
      </div>
    </div>
  );
}

export default function LiquidityInsights({ data }: { data: Insights }) {
  const r = data.regime, d = data.drivers, k = data.knowledge;
  const tile = (label: string, value: string, sub: string, color?: string) => (
    <div className="kpi">
      <div className="kpi-top"><span className="kpi-label">{label}</span></div>
      <div className="kpi-val"><span className="kpi-num" style={color ? { color } : undefined}>{value}</span></div>
      <div className="kpi-sub">{sub}</div>
    </div>
  );
  const sec = d.securities;
  return (
    <>
      {data.brief && (
        <div className="grid12" style={{ marginBottom: "var(--gap)" }}>
        <Panel title="Liquidity brief" span={12}
               sub={`${data.brief.by === "code" ? "composed from the facts by code" : "drafted by an AI model from the facts; every number written and checked by code"} · ${parseTs(data.brief.generated_utc).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}`}>
          <p style={{ margin: 0, fontSize: 14, lineHeight: 1.75, color: "var(--fg)" }}>{data.brief.text}</p>
          <div style={{ marginTop: 8, fontSize: 11, color: "var(--fg-mute)" }}>
            Shown only while the figures below are the ones it describes; it is rewritten when they change. Press-reported events are not part of it.
          </div>
        </Panel>
        </div>
      )}
      <div className="grid12" style={{ marginBottom: "var(--gap)" }}>
        <Panel title="Liquidity today" span={5}
               sub={`from Bangladesh Bank's own data · ${data.as_of}`}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
            <span style={{ fontSize: 30, fontWeight: 800, letterSpacing: "0.02em", color: REGIME_COLOR[r.label] }}>{r.label}</span>
            {r.position_in_corridor_pct != null && (
              <span style={{ fontSize: 12, color: "var(--fg-mute)" }}>{r.position_in_corridor_pct.toFixed(0)}% of the way from the SDF floor to the SLF ceiling</span>
            )}
          </div>
          <CorridorBar regime={r} />
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12.5, lineHeight: 1.65, color: "var(--fg-dim)" }}>
            {r.why.map((w, i) => <li key={i}>{w}</li>)}
          </ul>
          <div style={{ marginTop: 10, fontSize: 11, color: "var(--fg-mute)", lineHeight: 1.6 }}>
            Rule — tight: {r.rule.tight}; surplus: {r.rule.surplus}; otherwise {r.rule.otherwise}.
          </div>
        </Panel>

        <Panel title="What to know" span={7}
               sub={`${data.insights.filter((s) => s.severity !== "info").length} need attention · ranked`}>
          <div style={{ maxHeight: 330, overflowY: "auto" }}>
            {data.insights.map((s) => (
              <div key={s.id} style={{ padding: "7px 0", borderBottom: "1px solid var(--border)" }}>
                <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
                  <span style={{ color: SEV_COLOR[s.severity], fontSize: 10, lineHeight: 1 }}>●</span>
                  <b style={{ fontSize: 13, color: s.severity === "info" ? "var(--fg)" : SEV_COLOR[s.severity] }}>{s.headline}</b>
                </div>
                <div style={{ marginLeft: 18, fontSize: 12, color: "var(--fg-dim)", lineHeight: 1.55 }}>{s.detail}</div>
                {s.evidence.length > 0 && (
                  <div style={{ marginLeft: 18, fontSize: 11 }}>
                    {s.evidence.map((e, i) => (
                      <Link key={i} href={e.href} style={{ color: "var(--info)", textDecoration: "none", marginRight: 10 }}>{e.label} →</Link>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </Panel>
      </div>

      <div className="kpi-strip" style={{ gridTemplateColumns: "repeat(auto-fit,minmax(190px,1fr))" }}>
        {tile("BB net lending (stock)", n0(d.bb_net_lending_now_crore),
              `crore outstanding · ${signed(d.bb_net_lending_change_crore)} in a week · known to ${k.omo_known_to ?? "?"}`)}
        {tile("Overnight call money",
              r.inputs.call ? `${r.inputs.call.rate.toFixed(2)}%` : "—",
              d.call_change_bp == null ? "no reading a week earlier" : `${signed(d.call_change_bp)}bp vs a week earlier`)}
        {tile("Securities cash, last 7 days", sec.crore == null ? "partial" : signed(sec.crore),
              sec.crore == null ? `${sec.confirmed_days} of ${sec.days} working days confirmed — not summed` : "crore · + adds cash to banks",
              sec.crore == null ? "var(--warn)" : sec.crore >= 0 ? "var(--pos)" : "var(--neg)")}
        {tile("BB dollar operations, 7 days", d.fx.crore == null ? "none on record" : signed(d.fx.crore),
              d.fx.crore == null ? `last BB FX auction we hold: ${k.last_fx_auction ?? "none"}` : "crore of taka · + buys, − sells dollars",
              d.fx.crore == null ? undefined : d.fx.crore >= 0 ? "var(--pos)" : "var(--neg)")}
        {tile("BB lending maturing in 7 days", n0(d.bb_lending_maturing_7d_crore),
              "crore · mostly rolled each week; a stock, not an alert")}
      </div>

      <div className="grid12" style={{ marginBottom: "var(--gap)" }}>
        <Panel title="Next 10 working days" span={12} pad={false}
               sub="government securities settling against what comes in · BDT crore · + adds cash to the banks, − drains it">
          <div className="table-wrap">
            <table className="dt">
              <thead><tr>
                <th>Date</th><th className="r">Securities net</th><th className="r">Coupons + principal in</th>
                <th className="r">Auction settlements out</th><th className="r">OMO maturities (net)</th><th>Basis</th><th>News on this day</th>
              </tr></thead>
              <tbody>
                {data.calendar.map((c) => (
                  <tr key={c.date} style={c.holiday ? { opacity: 0.55 } : undefined}>
                    <td className="mono">{c.weekday} {c.date}</td>
                    <td className="r mono" style={{ color: c.securities_net_crore == null ? "var(--fg-mute)" : c.securities_net_crore >= 0 ? "var(--pos)" : "var(--neg)",
                                                   fontWeight: 700 }}>
                      {c.holiday ? "holiday" : c.securities_net_crore == null ? "unknown" : signed(c.securities_net_crore)}
                    </td>
                    <td className="r mono">{n0(c.govt_inflow_crore)}</td>
                    <td className="r mono">{n0(c.auction_out_crore)}</td>
                    <td className="r mono" style={{ color: "var(--fg-mute)" }} title="maturing facilities are usually refinanced; not in the securities net">{signed(c.omo_rolloff_crore)}</td>
                    <td style={{ fontSize: 11, color: "var(--fg-mute)", whiteSpace: "normal" }}>{c.basis}</td>
                    <td style={{ fontSize: 11, whiteSpace: "normal" }}>
                      {c.events.map((e) => <span key={e.id} title="from the news — not BB data, not in any figure" style={{ color: "var(--info)" }}>● {(e.summary ?? "event").slice(0, 60)} </span>)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ padding: "8px 12px", fontSize: 11.5, color: "var(--fg-mute)", borderTop: "1px solid var(--border)", lineHeight: 1.7 }}>
            {data.sign_convention} OMO operations are known up to {k.omo_known_to ?? "unknown"}; the auction calendar is published up to {k.auction_calendar_to ?? "unknown"}.
            A day beyond it shows <b>unknown</b>, never zero. Offered auction amounts are what BB announced; it may accept less.
          </div>
        </Panel>
      </div>
    </>
  );
}
