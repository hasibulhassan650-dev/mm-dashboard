"use client";
import { useState } from "react";
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";
import { OmoOutstandingRow } from "@/lib/api";
import { fmtDateShort, fmtCrore } from "@/lib/format";

const COLORS: Record<string, string> = {
  CB_REPO: "var(--s-blue)",
  IBLF:    "var(--s-violet)",
  AR:      "var(--s-green)",
  SLF:     "var(--s-amber)",
  SDF:     "var(--s-red)",
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
          const color = COLORS[instr] ?? "var(--fg-mute)";
          return (
            <button key={instr} onClick={() => toggle(instr)}
              className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs border transition-all"
              style={{ background: active ? `color-mix(in oklab, ${color} 14%, transparent)` : "transparent", borderColor: active ? `color-mix(in oklab, ${color} 55%, transparent)` : "var(--border)", color: active ? color : "var(--fg-mute)" }}>
              <span className="w-2 h-2 rounded-full inline-block" style={{ background: active ? color : "var(--fg-mute)" }} />
              {instr}
            </button>
          );
        })}
      </div>
      <ResponsiveContainer width="100%" height={220}>
        <AreaChart data={chartData} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
          <XAxis dataKey="date" tick={{ fill: "var(--fg-mute)", fontSize: 10 }}
            tickFormatter={d => fmtDateShort(String(d))} interval="preserveStartEnd" />
          <YAxis tick={{ fill: "var(--fg-mute)", fontSize: 11 }} tickFormatter={v => `${(v/1000).toFixed(0)}k`} width={40} />
          <Tooltip
            contentStyle={{ backgroundColor: "var(--tip-bg)", border: "1px solid var(--border)", borderRadius: 8 }}
            labelStyle={{ color: "var(--fg)", fontSize: 12 }}
            formatter={(v, name) => [fmtCrore(Number(v)), String(name)]}
          />
          {instruments.map(instr => (
            <Area key={instr} type="monotone" dataKey={instr}
              stackId="1" stroke={COLORS[instr] ?? "var(--fg-mute)"}
              fill={COLORS[instr] ?? "var(--fg-mute)"} fillOpacity={0.6} strokeWidth={1.5}
              hide={hidden.has(instr)} />
          ))}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
