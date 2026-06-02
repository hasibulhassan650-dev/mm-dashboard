"use client";
import * as React from "react";
import { Panel, DataTable, LegendToggle, type Col } from "@/components/terminal/ui";
import { StackedArea, type StackCat } from "@/components/terminal/charts";
import OmoNetLiquidityChart from "@/components/OmoNetLiquidityChart";
import DownloadButton from "@/components/DownloadButton";
import type { OmoOutstandingRow } from "@/lib/api";

export interface OmoOpRow { date: string; inst: string; tenor: string; accepted: number; rate: number | null; direction: string; maturity: string }

export interface OmoData {
  omoSeries: Record<string, number | string>[];
  omoCats: StackCat[];
  ops: OmoOpRow[];
  outstanding: OmoOutstandingRow[];
  stance: { label: string; tone: "tight" | "flush" } | null;
  latestNet: number | null;
}

export default function OmoView({ d }: { d: OmoData }) {
  const [active, setActive] = React.useState(Object.fromEntries(d.omoCats.map((c) => [c.key, true])));
  const last = d.omoSeries[d.omoSeries.length - 1];

  const cols: Col<OmoOpRow>[] = [
    { key: "date", label: "Date" },
    { key: "inst", label: "Instrument", render: (r) => <span className="pill-inst">{r.inst}</span> },
    { key: "tenor", label: "Tenor", mono: true },
    { key: "accepted", label: "Accepted (cr)", align: "r", mono: true, render: (r) => r.accepted.toLocaleString() },
    { key: "rate", label: "Rate", align: "r", mono: true, render: (r) => (r.rate != null ? r.rate.toFixed(2) + "%" : "—") },
    { key: "maturity", label: "Maturity" },
    { key: "direction", label: "Flow", align: "r", render: (r) => <span className={r.direction === "INJECTION" ? "pos" : "neg"}>{r.direction === "INJECTION" ? "Inject" : "Absorb"}</span> },
  ];

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
      <Panel title="OMO Outstanding" sub="stacked liquidity position · ৳ thousand crore" span={12}
        right={<LegendToggle cats={d.omoCats} active={active} onToggle={(k) => setActive((s) => ({ ...s, [k]: !s[k] }))} />}>
        <StackedArea data={d.omoSeries} cats={d.omoCats} active={active} height={320} />
      </Panel>
      <Panel title="Net Liquidity Stance" sub="injection − absorption per day · >0 = BB adding liquidity (tight)" span={12}
        right={d.stance ? <span className={"delta " + (d.stance.tone === "tight" ? "neg" : "pos")}>{d.stance.label}{d.latestNet != null ? ` · ${Math.round(d.latestNet).toLocaleString()} cr` : ""}</span> : null}>
        <OmoNetLiquidityChart rows={d.outstanding} />
      </Panel>
      <Panel title="Recent Operations" sub="auction-by-auction detail" span={12} pad={false}
        right={<DownloadButton data={d.ops} filename="omo_transactions" />}>
        <DataTable cols={cols} rows={d.ops} />
      </Panel>
    </>
  );
}
