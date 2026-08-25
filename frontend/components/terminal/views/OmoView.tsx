"use client";
import * as React from "react";
import { Panel, DataTable, LegendToggle, DrillModal, type Col, type DrillRow } from "@/components/terminal/ui";
import { StackedArea, type StackCat } from "@/components/terminal/charts";
import { omoDayDrill } from "@/components/terminal/views/OverviewView";
import OmoNetLiquidityChart from "@/components/OmoNetLiquidityChart";
import DownloadButton from "@/components/DownloadButton";
import type { OmoOutstandingRow, OmoTxnRow } from "@/lib/api";

export interface OmoOpRow { date: string; inst: string; tenor: string; accepted: number; rate: number | null; rateRange?: string | null; direction: string; maturity: string }

export interface OmoData {
  omoSeries: Record<string, number | string>[];
  omoCats: StackCat[];
  ops: OmoOpRow[];
  outstanding: OmoOutstandingRow[];
  txns: OmoTxnRow[];            // full window, for the forward maturity ladder
  stance: { label: string; tone: "tight" | "flush" } | null;
  latestNet: number | null;
}

const TODAY = new Date().toISOString().slice(0, 10);

export default function OmoView({ d }: { d: OmoData }) {
  const [active, setActive] = React.useState(Object.fromEntries(d.omoCats.map((c) => [c.key, true])));
  const [drill, setDrill] = React.useState<{ title: string; sub?: string; rows: DrillRow[] } | null>(null);
  const [tab, setTab] = React.useState<"outstanding" | "ladder">("outstanding");
  const last = d.omoSeries[d.omoSeries.length - 1];

  const cols: Col<OmoOpRow>[] = [
    { key: "date", label: "Date" },
    { key: "inst", label: "Instrument", render: (r) => <span className="pill-inst">{r.inst}</span> },
    { key: "tenor", label: "Tenor", mono: true },
    { key: "accepted", label: "Accepted (cr)", align: "r", mono: true, render: (r) => r.accepted.toLocaleString() },
    { key: "rate", label: "Rate", align: "r", mono: true, render: (r) => (r.rateRange ? r.rateRange.replace("-", "–") + "%" : r.rate != null ? r.rate.toFixed(2) + "%" : "—") },
    { key: "maturity", label: "Maturity" },
    { key: "direction", label: "Flow", align: "r", render: (r) => <span className={r.direction === "INJECTION" ? "pos" : "neg"}>{r.direction === "INJECTION" ? "Inject" : "Absorb"}</span> },
  ];

  // ── Outstanding by product & day (the graph, as a table) ────────────────────
  // Pivot the raw outstanding rows to date × instrument (every product, not just
  // the 5 charted) + a Total column, so the desk can export exactly what the
  // stacked area shows and see which product is outstanding on which day.
  const outPivot = React.useMemo(() => {
    const byDate = new Map<string, Record<string, number>>();
    const insts = new Set<string>();
    for (const r of d.outstanding) {
      insts.add(r.instrument);
      const slot = byDate.get(r.date) || {};
      slot[r.instrument] = (slot[r.instrument] || 0) + r.outstanding_bdt_crore;
      byDate.set(r.date, slot);
    }
    const instList = [...insts].sort();
    const rows = [...byDate.entries()]
      .sort((a, b) => b[0].localeCompare(a[0]))   // latest first
      .map(([date, vals]) => {
        const row: Record<string, number | string> = { date };
        let total = 0;
        for (const i of instList) { const v = Math.round(vals[i] || 0); row[i] = v; total += v; }
        row.Total = total;
        return row;
      });
    return { instList, rows };
  }, [d.outstanding]);

  // ── Forward maturity ladder: each live operation and when it drains/returns ──
  // An injection (repo/AR/IBLF/SLF) drains when it matures — banks repay BB; an
  // absorption (SDF) returns cash. Forward-looking, so it uses the full window
  // (independent of the history range), sorted by the date the flow lands.
  const ladder = React.useMemo(() =>
    d.txns
      .filter((t) => t.accepted_bdt_crore > 0 && t.maturity_date >= TODAY)
      .map((t) => ({
        maturity_date: t.maturity_date,
        instrument: t.instrument,
        tenor: t.tenor_label,
        injected_on: t.transaction_date,
        amount_cr: Math.round(t.accepted_bdt_crore),
        operation: t.direction,
        flow_at_maturity: t.direction === "INJECTION" ? "OUTFLOW — banks repay BB" : "INFLOW — cash returns",
      }))
      .sort((a, b) => a.maturity_date.localeCompare(b.maturity_date) || a.instrument.localeCompare(b.instrument)),
  [d.txns]);

  // Net drain per maturity date (outflow of injections − inflow of absorptions).
  const ladderByDate = React.useMemo(() => {
    const m = new Map<string, { date: string; outflow_cr: number; inflow_cr: number }>();
    for (const r of ladder) {
      const e = m.get(r.maturity_date) || { date: r.maturity_date, outflow_cr: 0, inflow_cr: 0 };
      if (r.operation === "INJECTION") e.outflow_cr += r.amount_cr; else e.inflow_cr += r.amount_cr;
      m.set(r.maturity_date, e);
    }
    return [...m.values()]
      .sort((a, b) => a.date.localeCompare(b.date))
      .map((e) => ({ ...e, net_drain_cr: e.outflow_cr - e.inflow_cr }));
  }, [ladder]);

  return (
    <>
      <div className="kpi-strip four">
        {d.omoCats.map((c) => (
          <div className="kpi" key={c.key}>
            <div className="kpi-top"><span className="kpi-label">{c.label}</span><span className="leg-sw" style={{ background: c.color }} /></div>
            <div className="kpi-val"><span className="kpi-num">{last ? Number(last[c.key]).toFixed(1) : "—"}</span><span className="kpi-unit">k cr</span></div>
            <div className="kpi-sub">{c.desc}</div>
          </div>
        ))}
      </div>

      <div style={{ gridColumn: "span 12", display: "flex", gap: 8, marginBottom: 2 }}>
        <div className="seg">
          <button className={"seg-b" + (tab === "outstanding" ? " on" : "")} onClick={() => setTab("outstanding")}>Outstanding</button>
          <button className={"seg-b" + (tab === "ladder" ? " on" : "")} onClick={() => setTab("ladder")}>Maturity Ladder</button>
        </div>
      </div>

      {tab === "outstanding" && <>
        <Panel title="OMO Outstanding" sub="৳ thousand crore · click a day for breakdown" span={12}
          right={<LegendToggle cats={d.omoCats} active={active} onToggle={(k) => setActive((s) => ({ ...s, [k]: !s[k] }))} />}>
          <StackedArea data={d.omoSeries} cats={d.omoCats} active={active} height={320}
            onPointClick={(i) => d.omoSeries[i] && setDrill(omoDayDrill(d.omoSeries[i], d.omoCats))} />
        </Panel>
        <Panel title="Net Liquidity Stance" sub="injection − absorption per day · >0 = BB adding liquidity (tight)" span={12}
          right={d.stance ? <span className={"delta " + (d.stance.tone === "tight" ? "neg" : "pos")}>{d.stance.label}{d.latestNet != null ? ` · ${Math.round(d.latestNet).toLocaleString()} cr` : ""}</span> : null}>
          <OmoNetLiquidityChart rows={d.outstanding} />
        </Panel>
        <Panel title="Outstanding by Product & Day" sub="৳ crore · which product is outstanding on which day, with the daily total" span={12} pad={false}
          right={<DownloadButton data={outPivot.rows} filename="omo_outstanding_by_product" />}>
          <div className="table-wrap" style={{ maxHeight: 420, overflow: "auto" }}>
            <table className="dt">
              <thead><tr>
                <th>Date</th>
                {outPivot.instList.map((i) => <th key={i} className="r">{i}</th>)}
                <th className="r">Total</th>
              </tr></thead>
              <tbody>
                {outPivot.rows.map((row, ri) => (
                  <tr key={ri}>
                    <td>{row.date}</td>
                    {outPivot.instList.map((i) => <td key={i} className="r mono">{Number(row[i]) ? Number(row[i]).toLocaleString() : ""}</td>)}
                    <td className="r mono" style={{ fontWeight: 600 }}>{Number(row.Total).toLocaleString()}</td>
                  </tr>
                ))}
                {outPivot.rows.length === 0 && <tr><td colSpan={outPivot.instList.length + 2} style={{ textAlign: "center", color: "var(--fg-mute)", padding: 16 }}>No outstanding in the selected window.</td></tr>}
              </tbody>
            </table>
          </div>
        </Panel>
        <Panel title="Recent Operations" sub="auction-by-auction detail" span={12} pad={false}
          right={<DownloadButton data={d.ops} filename="omo_transactions" />}>
          <DataTable cols={cols} rows={d.ops} />
        </Panel>
      </>}

      {tab === "ladder" && <>
        <Panel title="Maturity Ladder — Net Drain by Date" sub="৳ crore · when live OMO drains (banks repay BB) or returns (SDF unwinds)" span={12} pad={false}
          right={<DownloadButton data={ladderByDate} filename="omo_maturity_ladder_by_date" />}>
          <div className="table-wrap" style={{ maxHeight: 300, overflow: "auto" }}>
            <table className="dt">
              <thead><tr><th>Maturity Date</th><th className="r">Outflow (drain)</th><th className="r">Inflow (return)</th><th className="r">Net Drain</th></tr></thead>
              <tbody>
                {ladderByDate.map((r, i) => (
                  <tr key={i}>
                    <td>{r.date}</td>
                    <td className="r mono neg">{r.outflow_cr ? r.outflow_cr.toLocaleString() : ""}</td>
                    <td className="r mono pos">{r.inflow_cr ? r.inflow_cr.toLocaleString() : ""}</td>
                    <td className="r mono" style={{ fontWeight: 600, color: r.net_drain_cr > 0 ? "var(--warn)" : "var(--info)" }}>{r.net_drain_cr.toLocaleString()}</td>
                  </tr>
                ))}
                {ladderByDate.length === 0 && <tr><td colSpan={4} style={{ textAlign: "center", color: "var(--fg-mute)", padding: 16 }}>No live OMO maturing ahead.</td></tr>}
              </tbody>
            </table>
          </div>
        </Panel>
        <Panel title="Maturity Ladder — By Product & Operation" sub="every live operation: what was injected/absorbed, and the date it drains/returns" span={12} pad={false}
          right={<DownloadButton data={ladder} filename="omo_maturity_ladder_detail" />}>
          <div className="table-wrap" style={{ maxHeight: 460, overflow: "auto" }}>
            <table className="dt">
              <thead><tr>
                <th>Maturity Date</th><th>Product</th><th>Tenor</th><th>Injected/Absorbed On</th>
                <th className="r">Amount (cr)</th><th>Operation</th><th>Flow at Maturity</th>
              </tr></thead>
              <tbody>
                {ladder.map((r, i) => (
                  <tr key={i}>
                    <td>{r.maturity_date}</td>
                    <td><span className="pill-inst">{r.instrument}</span></td>
                    <td className="mono">{r.tenor}</td>
                    <td>{r.injected_on}</td>
                    <td className="r mono">{r.amount_cr.toLocaleString()}</td>
                    <td><span className={r.operation === "INJECTION" ? "pos" : "neg"}>{r.operation === "INJECTION" ? "Inject" : "Absorb"}</span></td>
                    <td style={{ color: r.operation === "INJECTION" ? "var(--warn)" : "var(--info)" }}>{r.flow_at_maturity}</td>
                  </tr>
                ))}
                {ladder.length === 0 && <tr><td colSpan={7} style={{ textAlign: "center", color: "var(--fg-mute)", padding: 16 }}>No live OMO maturing ahead.</td></tr>}
              </tbody>
            </table>
          </div>
        </Panel>
      </>}

      {drill && <DrillModal title={drill.title} sub={drill.sub} rows={drill.rows} onClose={() => setDrill(null)} />}
    </>
  );
}
