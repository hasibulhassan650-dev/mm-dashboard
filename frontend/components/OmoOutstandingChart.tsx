"use client";
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend } from "recharts";
import { OmoOutstandingRow } from "@/lib/api";

const COLORS: Record<string, string> = {
  CB_REPO: "#3b82f6",
  IBLF:    "#8b5cf6",
  AR:      "#10b981",
  SLF:     "#f59e0b",
  SDF:     "#ef4444",
};

export default function OmoOutstandingChart({ data }: { data: OmoOutstandingRow[] }) {
  // Pivot: [{date, CB_REPO: x, IBLF: y, ...}]
  const byDate = new Map<string, Record<string, number | string>>();
  for (const row of data) {
    if (!byDate.has(row.date)) byDate.set(row.date, { date: row.date });
    byDate.get(row.date)![row.instrument] = row.outstanding_bdt_crore;
  }
  const chartData = Array.from(byDate.values()).sort((a, b) => String(a.date).localeCompare(String(b.date)));
  const instruments = [...new Set(data.map(r => r.instrument))];

  return (
    <ResponsiveContainer width="100%" height={220}>
      <AreaChart data={chartData} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
        <XAxis dataKey="date" tick={{ fill: "#9ca3af", fontSize: 10 }}
          tickFormatter={d => d.slice(5)} interval="preserveStartEnd" />
        <YAxis tick={{ fill: "#9ca3af", fontSize: 11 }} tickFormatter={v => `${(v/1000).toFixed(0)}k`} width={40} />
        <Tooltip
          contentStyle={{ backgroundColor: "#111827", border: "1px solid #374151", borderRadius: 8 }}
          labelStyle={{ color: "#e5e7eb", fontSize: 12 }}
          formatter={(v, name) => [`${Number(v).toFixed(0)} cr`, String(name)]}
        />
        <Legend wrapperStyle={{ fontSize: 11, color: "#9ca3af" }} />
        {instruments.map(instr => (
          <Area key={instr} type="monotone" dataKey={instr}
            stackId="1" stroke={COLORS[instr] ?? "#6b7280"}
            fill={COLORS[instr] ?? "#6b7280"} fillOpacity={0.6} strokeWidth={1.5} />
        ))}
      </AreaChart>
    </ResponsiveContainer>
  );
}
