"use client";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend, CartesianGrid } from "recharts";
import { YieldRow } from "@/lib/api";

const TENOR_COLORS: Record<string, string> = {
  "91D":    "#6366f1",
  "182D":   "#8b5cf6",
  "364D":   "#a78bfa",
  "2Y":     "#3b82f6",
  "5Y":     "#10b981",
  "10Y":    "#f59e0b",
  "15Y":    "#ef4444",
  "20Y":    "#ec4899",
  "3Y_FRTB":"#14b8a6",
};

const KEY_TENORS = ["91D", "364D", "2Y", "5Y", "10Y", "20Y"];

export default function YieldHistoryChart({ data }: { data: YieldRow[] }) {
  const filtered = data.filter(r => KEY_TENORS.includes(r.tenor_label));
  const byDate = new Map<string, Record<string, number | string>>();
  for (const row of filtered) {
    if (!byDate.has(row.auction_date)) byDate.set(row.auction_date, { date: row.auction_date });
    byDate.get(row.auction_date)![row.tenor_label] = row.cutoff_yield_pct;
  }
  const chartData = Array.from(byDate.values()).sort((a, b) => String(a.date).localeCompare(String(b.date)));
  const present = KEY_TENORS.filter(t => filtered.some(r => r.tenor_label === t));

  return (
    <ResponsiveContainer width="100%" height={220}>
      <LineChart data={chartData} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
        <XAxis dataKey="date" tick={{ fill: "#9ca3af", fontSize: 10 }}
          tickFormatter={d => d.slice(2, 7)} interval="preserveStartEnd" />
        <YAxis domain={["auto", "auto"]} tick={{ fill: "#9ca3af", fontSize: 11 }}
          tickFormatter={v => `${v}%`} width={42} />
        <Tooltip
          contentStyle={{ backgroundColor: "#111827", border: "1px solid #374151", borderRadius: 8 }}
          labelStyle={{ color: "#e5e7eb", fontSize: 11 }}
          formatter={(v, name) => [`${Number(v).toFixed(4)}%`, String(name)]}
        />
        <Legend wrapperStyle={{ fontSize: 11, color: "#9ca3af" }} />
        {present.map(t => (
          <Line key={t} type="monotone" dataKey={t}
            stroke={TENOR_COLORS[t] ?? "#6b7280"} strokeWidth={1.5}
            dot={false} connectNulls />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
