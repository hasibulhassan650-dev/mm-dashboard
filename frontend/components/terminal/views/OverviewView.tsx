"use client";
import * as React from "react";
import { Panel, KpiCard, DataTable, SegTabs, LegendToggle, CorridorViz, DrillModal, type Kpi, type Col, type CorridorData, type DrillRow } from "@/components/terminal/ui";
import { LineChart, StackedArea, type LineSeries, type StackCat } from "@/components/terminal/charts";
import { useTheme } from "@/components/terminal/ThemeProvider";

interface Drill { title: string; sub?: string; rows: DrillRow[]; footer?: React.ReactNode }

/** Build a tenor drill-down from aligned today/weekAgo/monthAgo arrays. */
export function tenorDrill(tenors: string[], today: number[], weekAgo: number[] | null, monthAgo: number[] | null, i: number): Drill {
  const rows: DrillRow[] = [{ k: "Cut-off yield (today)", v: <span className="mono">{today[i].toFixed(3)}%</span> }];
  if (weekAgo) { const dlt = today[i] - weekAgo[i]; rows.push({ k: "1 week ago", v: <span className="mono">{weekAgo[i].toFixed(3)}% <span className={dlt >= 0 ? "pos" : "neg"}>({dlt >= 0 ? "+" : ""}{Math.round(dlt * 100)}bps)</span></span> }); }
  if (monthAgo) { const dlt = today[i] - monthAgo[i]; rows.push({ k: "1 month ago", v: <span className="mono">{monthAgo[i].toFixed(3)}% <span className={dlt >= 0 ? "pos" : "neg"}>({dlt >= 0 ? "+" : ""}{Math.round(dlt * 100)}bps)</span></span> }); }
  return { title: `${tenors[i]} tenor`, sub: "sovereign cut-off yield", rows, footer: "Open the Yields page → Auction Explorer for this tenor's full history." };
}

/** Build an OMO day drill-down from a pivoted series row. */
export function omoDayDrill(row: Record<string, number | string>, cats: StackCat[]): Drill {
  let total = 0;
  const rows: DrillRow[] = cats.map((c) => { const v = Number(row[c.key]) || 0; total += v; return { k: c.label, v: <span className="mono">{v.toFixed(2)}k cr</span> }; });
  rows.push({ k: "Total outstanding", v: <span className="mono" style={{ color: "var(--accent)" }}>{total.toFixed(2)}k cr</span> });
  return { title: `OMO · ${row.label}`, sub: "outstanding by instrument", rows };
}

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
  const [drill, setDrill] = React.useState<Drill | null>(null);

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
        <Panel title="Yield Curve" sub="cut-off yields · click a tenor for detail" span={7}
          right={<SegTabs tabs={["vs 1W", "vs 1M"]} value={curveMode} onChange={setCurveMode} />}>
          <LineChart labels={d.tenors} series={series} height={300} showGrid={grid}
            onPointClick={(i) => setDrill(tenorDrill(d.tenors, d.today, d.weekAgo, d.monthAgo, i))} />
        </Panel>
        <Panel title="Policy Rate Corridor" sub="Interest-rate corridor & call rate" span={5}>
          {d.corridor ? <CorridorViz c={d.corridor} /> : <div style={{ color: "var(--fg-mute)", padding: 24 }}>Corridor data unavailable.</div>}
        </Panel>
        <Panel title="OMO Outstanding" sub="60-day liquidity · ৳ thousand crore · click a day" span={12}
          right={<LegendToggle cats={d.omoCats} active={omoActive} onToggle={(k) => setOmoActive((s) => ({ ...s, [k]: !s[k] }))} />}>
          <StackedArea data={d.omoSeries} cats={d.omoCats} active={omoActive} height={300}
            onPointClick={(i) => d.omoSeries[i] && setDrill(omoDayDrill(d.omoSeries[i], d.omoCats))} />
        </Panel>
        <Panel title="Latest OMO Operations" sub="Repo · facilities" span={7} pad={false}>
          <DataTable cols={omoCols} rows={d.omoAuctions} />
        </Panel>
        <Panel title="Securities Auctions" sub="T-bills & T-bonds" span={5} pad={false}>
          <DataTable cols={secCols} rows={d.secAuctions} />
        </Panel>
      </div>
      {drill && <DrillModal title={drill.title} sub={drill.sub} rows={drill.rows} footer={drill.footer} onClose={() => setDrill(null)} />}
    </>
  );
}
