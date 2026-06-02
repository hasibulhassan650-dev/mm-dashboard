"use client";
import * as React from "react";
import { Panel, KpiCard, DataTable, SegTabs, LegendToggle, CorridorViz, type Kpi, type Col, type CorridorData } from "@/components/terminal/ui";
import { LineChart, StackedArea, type LineSeries, type StackCat } from "@/components/terminal/charts";
import { useTheme } from "@/components/terminal/ThemeProvider";

export interface OmoAuctionRow { date: string; inst: string; tenor: string; accepted: number; rate: number | null; direction: string }
export interface SecAuctionRow { sec: string; accepted: number; cutoff: number; cover: number | null }

export interface OverviewData {
  kpis: Kpi[];
  tenors: string[];
  today: number[];
  weekAgo: number[] | null;
  monthAgo: number[] | null;
  omoSeries: Record<string, number | string>[];
  omoCats: StackCat[];
  corridor: CorridorData | null;
  omoAuctions: OmoAuctionRow[];
  secAuctions: SecAuctionRow[];
}

export default function OverviewView({ d }: { d: OverviewData }) {
  const { grid } = useTheme();
  const [curveMode, setCurveMode] = React.useState("vs 1W");
  const [omoActive, setOmoActive] = React.useState(Object.fromEntries(d.omoCats.map((c) => [c.key, true])));

  const series: LineSeries[] = [{ name: "Today", data: d.today, color: "var(--accent)" }];
  if (curveMode === "vs 1W" && d.weekAgo) series.push({ name: "1W ago", data: d.weekAgo, color: "var(--info)", dashed: true });
  if (curveMode === "vs 1M" && d.monthAgo) series.push({ name: "1M ago", data: d.monthAgo, color: "var(--warn)", dashed: true });

  const omoCols: Col<OmoAuctionRow>[] = [
    { key: "date", label: "Date" },
    { key: "inst", label: "Instrument", render: (r) => <span className="pill-inst">{r.inst}</span> },
    { key: "tenor", label: "Tenor", mono: true },
    { key: "accepted", label: "Accepted", align: "r", mono: true, render: (r) => r.accepted.toLocaleString() },
    { key: "rate", label: "Rate", align: "r", mono: true, render: (r) => (r.rate != null ? r.rate.toFixed(2) + "%" : "—") },
    { key: "direction", label: "Flow", align: "r", render: (r) => <span className={r.direction === "INJECTION" ? "pos" : "neg"}>{r.direction === "INJECTION" ? "Inject" : "Absorb"}</span> },
  ];
  const secCols: Col<SecAuctionRow>[] = [
    { key: "sec", label: "Security" },
    { key: "accepted", label: "Accepted", align: "r", mono: true, render: (r) => r.accepted.toLocaleString() },
    { key: "cutoff", label: "Cut-off", align: "r", mono: true, render: (r) => r.cutoff.toFixed(2) + "%" },
    { key: "cover", label: "Cover", align: "r", mono: true, render: (r) => (r.cover != null ? r.cover.toFixed(2) + "×" : "—") },
  ];

  return (
    <>
      <div className="kpi-strip">{d.kpis.map((k) => <KpiCard key={k.id} k={k} />)}</div>
      <div className="grid12">
        <Panel title="Yield Curve" sub="Govt securities · cut-off yields" span={7}
          right={<SegTabs tabs={["vs 1W", "vs 1M"]} value={curveMode} onChange={setCurveMode} />}>
          <LineChart labels={d.tenors} series={series} height={300} showGrid={grid} />
        </Panel>
        <Panel title="Policy Rate Corridor" sub="Interest-rate corridor & call rate" span={5}>
          {d.corridor ? <CorridorViz c={d.corridor} /> : <div style={{ color: "var(--fg-mute)", padding: 24 }}>Corridor data unavailable.</div>}
        </Panel>
        <Panel title="OMO Outstanding" sub="60-day liquidity operations · ৳ thousand crore" span={12}
          right={<LegendToggle cats={d.omoCats} active={omoActive} onToggle={(k) => setOmoActive((s) => ({ ...s, [k]: !s[k] }))} />}>
          <StackedArea data={d.omoSeries} cats={d.omoCats} active={omoActive} height={300} />
        </Panel>
        <Panel title="Latest OMO Operations" sub="Repo · facilities" span={7} pad={false}>
          <DataTable cols={omoCols} rows={d.omoAuctions} />
        </Panel>
        <Panel title="Securities Auctions" sub="T-bills & T-bonds" span={5} pad={false}>
          <DataTable cols={secCols} rows={d.secAuctions} />
        </Panel>
      </div>
    </>
  );
}
