"use client";
import * as React from "react";
import { Panel, KpiCard, DataTable, type Kpi, type Col } from "@/components/terminal/ui";
import { LineChart, type LineSeries } from "@/components/terminal/charts";
import { useTheme } from "@/components/terminal/ThemeProvider";

// Serializable column spec (server-passable — no functions). `fmt` selects a
// client-side renderer so server components can describe tables declaratively.
export type Fmt = "text" | "num" | "num1" | "num2" | "pct" | "rate" | "crore" | "mn" | "pill" | "posneg" | "signedBps";
export interface ColSpec { key: string; label: string; align?: "r"; mono?: boolean; fmt?: Fmt }

export interface ChartSpec { title: string; sub?: string; labels: string[]; series: LineSeries[]; height?: number; yUnit?: string; span?: number }
export interface TableSpec { title: string; sub?: string; cols: ColSpec[]; rows: Record<string, unknown>[]; span?: number }

export interface GenericData {
  kpis?: Kpi[];
  kpiFour?: boolean;
  charts?: ChartSpec[];
  tables?: TableSpec[];
}

function num(n: unknown, dp = 0): string {
  if (n == null || Number.isNaN(Number(n))) return "—";
  return Number(n).toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp });
}

function cell(spec: ColSpec, row: Record<string, unknown>): React.ReactNode {
  const v = row[spec.key];
  switch (spec.fmt) {
    case "num": return num(v);
    case "num1": return num(v, 1);
    case "num2": return num(v, 2);
    case "pct": return v == null ? "—" : Number(v).toFixed(2) + "%";
    case "rate": return v == null ? "—" : Number(v).toFixed(4);
    case "crore": return v == null ? "—" : num(v);
    case "mn": return v == null ? "—" : num(v);
    case "pill": return <span className="pill-inst">{String(v ?? "—")}</span>;
    case "posneg": {
      if (v == null) return <span style={{ color: "var(--fg-mute)" }}>—</span>;
      const n = Number(v);
      return <span className={n >= 0 ? "pos" : "neg"}>{n >= 0 ? "+" : ""}{n.toFixed(2)}</span>;
    }
    case "signedBps": {
      if (v == null) return <span style={{ color: "var(--fg-mute)" }}>—</span>;
      const n = Number(v);
      return <span className={n >= 0 ? "pos" : "neg"}>{n >= 0 ? "+" : ""}{Math.round(n)}</span>;
    }
    default: return v == null ? "—" : String(v);
  }
}

function toCols(specs: ColSpec[]): Col<Record<string, unknown>>[] {
  return specs.map((s) => ({
    key: s.key, label: s.label, align: s.align, mono: s.mono,
    render: (r: Record<string, unknown>) => cell(s, r),
  }));
}

export default function GenericView({ d }: { d: GenericData }) {
  const { grid } = useTheme();
  return (
    <>
      {d.kpis && d.kpis.length > 0 && (
        <div className={"kpi-strip" + (d.kpiFour ? " four" : "")} style={d.kpiFour ? undefined : { marginBottom: "var(--gap)" }}>
          {d.kpis.map((k) => <KpiCard key={k.id} k={k} />)}
        </div>
      )}
      <div className="grid12">
        {d.charts?.map((c, i) => (
          <Panel key={"c" + i} title={c.title} sub={c.sub} span={c.span ?? 12}>
            <LineChart labels={c.labels} series={c.series} height={c.height ?? 300} yUnit={c.yUnit ?? "%"} showGrid={grid} />
          </Panel>
        ))}
        {d.tables?.map((t, i) => (
          <Panel key={"t" + i} title={t.title} sub={t.sub} span={t.span ?? 12} pad={false}>
            <DataTable cols={toCols(t.cols)} rows={t.rows} />
          </Panel>
        ))}
      </div>
    </>
  );
}
