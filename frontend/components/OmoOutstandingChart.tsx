"use client";
import { useState } from "react";
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";
import { OmoOutstandingRow } from "@/lib/api";

const COLORS: Record<string, string> = {
  CB_REPO: "#3b82f6",
  IBLF:    "#8b5cf6",
  AR:      "#10b981",
  SLF:     "#f59e0b",
  SDF:     "#ef4444",
};

export default function OmoOutstandingChart({ data }: { data: OmoOutstandingRow[] }) {
  const byDate = new Map<string, Record<string, number | string>>();
  for (const row of data) {
    if (!byDate.has(row.date)) byDate.set(row.date, { date: row.date });
    byDate.get(row.date)![row.instrument] = row.outstanding_bdt_crore;
  }
  const chartData = Array.from(byDate.values()).sort((a, b) => String(a.date).localeCompare(String(b.date)));
  const instruments = [...new Set(data.map(r => r.instrument))];

  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const toggle = (key: string) => setHidden(prev => {
    const next = new Set(prev); next.has(key) ? next.delete(key) : next.add(key); return next;
  });

  return (
    <div>
      <div className="flex flex-wrap gap-1.5 mb-3">
        {instruments.map(instr => {
          const active = !hidden.has(instr);
          const color = COLORS[instr] ?? "#6b7280";
          return (
            <button key={instr} onClick={() => toggle(instr)}
              className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs border transition-all"
              style={{ background: active ? color + "22" : "transparent", borderColor: active ? color + "88" : "#374151", color: active ? color : "#4b5563" }}>
              <span className="w-2 h-2 rounded-full inline-block" style={{ background: active ? color : "#4b5563" }} />
              {instr}
            </button>
          );
        })}
      </div>
      <ResponsiveContainer width="100%" height={220}>
        <AreaChart data={chartData} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
          <XAxis dataKey="date" tick={{ fill: "#9ca3af", fontSize: 10 }}
            tickFormatter={d => String(d).slice(5)} interval="preserveStartEnd" />
          <YAxis tick={{ fill: "#9ca3af", fontSize: 11 }} tickFormatter={v => `${(v/1000).toFixed(0)}k`} width={40} />
          <Tooltip
            contentStyle={{ backgroundColor: "#111827", border: "1px solid #374151", borderRadius: 8 }}
            labelStyle={{ color: "#e5e7eb", fontSize: 12 }}
            formatter={(v, name) => [`${Number(v).toFixed(0)} cr`, String(name)]}
          />
          {instruments.map(instr => (
            <Area key={instr} type="monotone" dataKey={instr}
              stackId="1" stroke={COLORS[instr] ?? "#6b7280"}
              fill={COLORS[instr] ?? "#6b7280"} fillOpacity={0.6} strokeWidth={1.5}
              hide={hidden.has(instr)} />
          ))}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
