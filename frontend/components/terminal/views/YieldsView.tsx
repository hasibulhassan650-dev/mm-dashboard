"use client";
import * as React from "react";
import { Panel, DataTable, SegTabs, MetricList, type Col } from "@/components/terminal/ui";
import { LineChart, type LineSeries } from "@/components/terminal/charts";
import { useTheme } from "@/components/terminal/ThemeProvider";

export interface YieldsData {
  tenors: string[];
  today: number[];
  weekAgo: number[] | null;
  monthAgo: number[] | null;
}

interface Row { tenor: string; today: number; w: number | null; m: number | null }

export default function YieldsView({ d }: { d: YieldsData }) {
  const { grid } = useTheme();
  const [mode, setMode] = React.useState("vs 1M");

  const series: LineSeries[] = [{ name: "Today", data: d.today, color: "var(--accent)" }];
  if (mode === "vs 1W" && d.weekAgo) series.push({ name: "1W ago", data: d.weekAgo, color: "var(--info)", dashed: true });
  if (mode === "vs 1M" && d.monthAgo) series.push({ name: "1M ago", data: d.monthAgo, color: "var(--warn)", dashed: true });

  const rows: Row[] = d.tenors.map((t, i) => ({
    tenor: t, today: d.today[i],
    w: d.weekAgo ? d.today[i] - d.weekAgo[i] : null,
    m: d.monthAgo ? d.today[i] - d.monthAgo[i] : null,
  }));
  const bpsCell = (v: number | null) => v == null ? <span style={{ color: "var(--fg-mute)" }}>—</span>
    : <span className={v >= 0 ? "pos" : "neg"}>{v >= 0 ? "+" : ""}{Math.round(v * 100)}</span>;
  const cols: Col<Row>[] = [
    { key: "tenor", label: "Tenor", mono: true },
    { key: "today", label: "Yield", align: "r", mono: true, render: (r) => r.today.toFixed(2) + "%" },
    { key: "w", label: "Δ 1W (bps)", align: "r", mono: true, render: (r) => bpsCell(r.w) },
    { key: "m", label: "Δ 1M (bps)", align: "r", mono: true, render: (r) => bpsCell(r.m) },
  ];

  const idx = (t: string) => d.tenors.indexOf(t);
  const spread = (a: string, b: string) => {
    const ia = idx(a), ib = idx(b);
    return ia >= 0 && ib >= 0 ? Math.round((d.today[ia] - d.today[ib]) * 100) : null;
  };
  const long = d.today[d.today.length - 1], short = d.today[0];
  const metrics = [
    { k: "10Y – 91D spread", v: <span className="mono">{spread("10Y", "91D") ?? "—"} bps</span> },
    { k: "2Y – 91D spread", v: <span className="mono">{spread("2Y", "91D") ?? "—"} bps</span> },
    { k: "Curve shape", v: <span className={long > short ? "pos" : "neg"}>{long > short ? "Upward" : "Inverted"}</span> },
    { k: `Long end (${d.tenors[d.tenors.length - 1] ?? "—"})`, v: <span className="mono">{long != null ? long.toFixed(2) + "%" : "—"}</span> },
    { k: `Short end (${d.tenors[0] ?? "—"})`, v: <span className="mono">{short != null ? short.toFixed(2) + "%" : "—"}</span> },
  ];

  return (
    <div className="grid12">
      <Panel title="Sovereign Yield Curve" sub="Bangladesh Bank · cut-off yields" span={8}
        right={<SegTabs tabs={["vs 1W", "vs 1M"]} value={mode} onChange={setMode} />}>
        <LineChart labels={d.tenors} series={series} height={360} showGrid={grid} />
      </Panel>
      <div className="stack4">
        <Panel title="Curve Metrics" span={4}><MetricList items={metrics} /></Panel>
      </div>
      <Panel title="Tenor Detail" sub="Yields & weekly / monthly change" span={12} pad={false}>
        <DataTable cols={cols} rows={rows} />
      </Panel>
    </div>
  );
}
