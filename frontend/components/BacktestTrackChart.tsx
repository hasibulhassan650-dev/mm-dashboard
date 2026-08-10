"use client";
import * as React from "react";
import {
  ComposedChart, Line, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, Legend,
} from "recharts";
import { BacktestPredictionRow, ForecastModel } from "@/lib/api";
import { fmtDateShort, fmtPct } from "@/lib/format";

/**
 * Every out-of-sample backtest prediction against what actually printed.
 *
 * This is a SIMULATION — each point is what the model would have said having
 * seen only prior auctions. It is not the published forecast log; that lives
 * in auction_forecasts and only grows one auction at a time. Showing the
 * simulation is what makes the model's behaviour inspectable today rather
 * than in a year, so long as it is never passed off as a live record.
 *
 * Levels, not changes: here the question is "does the model track the rate",
 * and the eye reads tracking best on the actual series.
 */
const SERIES: { key: ForecastModel; label: string; color: string }[] = [
  { key: "momentum", label: "Momentum", color: "#1f9e6e" },   // --accent
  { key: "naive",    label: "Naive",    color: "#4f8ff7" },   // --info
  // Not --warn (#d99a1f): too light for the dark surface (L .73 vs .67 cap).
  // This burnt orange clears the lightness band and CVD checks in both themes.
  { key: "ols",      label: "OLS",      color: "#c2703c" },
];

interface Row { date: string; actual: number | null; naive?: number | null; momentum?: number | null; ols?: number | null }

interface TipProps { active?: boolean; payload?: { value: number; name: string; color: string }[]; label?: string }
function Tip({ active, payload, label }: TipProps) {
  if (!active || !payload?.length) return null;
  return (
    <div style={{ background: "var(--panel-2)", border: "1px solid var(--border)", borderRadius: 8, padding: "8px 12px", fontSize: 11.5 }}>
      <div style={{ color: "var(--fg)", marginBottom: 6, fontWeight: 600 }}>{label}</div>
      {payload.map((p, i) => (
        <div key={i} style={{ color: p.color, fontFamily: "var(--mono)" }}>
          {p.name}: {p.value == null ? "—" : fmtPct(p.value, 4)}
        </div>
      ))}
    </div>
  );
}

export default function BacktestTrackChart({ rows }: { rows: BacktestPredictionRow[] }) {
  const data = React.useMemo(() => {
    const byDate = new Map<string, Row>();
    for (const r of rows) {
      const d = byDate.get(r.auction_date) ?? { date: r.auction_date, actual: r.actual_yield };
      d.actual = d.actual ?? r.actual_yield;
      d[r.model] = r.pred_yield;
      byDate.set(r.auction_date, d);
    }
    return [...byDate.values()]
      .sort((a, b) => a.date.localeCompare(b.date))
      .map(d => ({ ...d, label: fmtDateShort(d.date) }));
  }, [rows]);

  if (data.length === 0) {
    return <div style={{ padding: 28, textAlign: "center", color: "var(--fg-mute)", fontSize: 12.5 }}>
      No backtest predictions stored for this tenor yet.
    </div>;
  }

  const present = SERIES.filter(s => data.some(d => d[s.key] != null));

  return (
    <ResponsiveContainer width="100%" height={300}>
      <ComposedChart data={data} margin={{ top: 6, right: 14, bottom: 4, left: 4 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--grid)" />
        <XAxis dataKey="label" tick={{ fill: "var(--fg-mute)", fontSize: 10 }} interval="preserveStartEnd" minTickGap={28} />
        <YAxis tick={{ fill: "var(--fg-mute)", fontSize: 10 }} width={46} domain={["auto", "auto"]}
          tickFormatter={v => `${v.toFixed(1)}%`} />
        <Tooltip content={<Tip />} />
        <Legend wrapperStyle={{ fontSize: 11.5 }} />
        {/* Actual sits underneath, heavier — it is the thing being tracked. */}
        <Line type="monotone" dataKey="actual" name="Actual cutoff" stroke="var(--fg)"
          strokeWidth={2.5} dot={false} connectNulls />
        {present.map(s => (
          <Line key={s.key} type="monotone" dataKey={s.key} name={s.label} stroke={s.color}
            strokeWidth={1.5} strokeDasharray="4 3" dot={false} connectNulls />
        ))}
      </ComposedChart>
    </ResponsiveContainer>
  );
}
