"use client";
import { useState } from "react";
import {
  ComposedChart, Area, Line, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid,
} from "recharts";
import { CallMoneyDailySummary } from "@/lib/api";
import { fmtDateShort, fmtCrore, fmtPct } from "@/lib/format";

const SERIES = [
  { key: "avg",    label: "Avg Rate",  color: "#f59e0b" },
  { key: "high",   label: "High Rate", color: "#dc2626" },
  { key: "low",    label: "Low Rate",  color: "#16a34a" },
  { key: "volume", label: "Volume",    color: "#4b5563" },
];

interface TooltipProps { active?: boolean; payload?: { value: number; name: string; color: string }[]; label?: string }

function CMTooltip({ active, payload, label }: TooltipProps) {
  if (!active || !payload?.length) return null;
  return (
    <div style={{ background: "#111827", border: "1px solid #374151", borderRadius: 8, padding: "8px 12px", fontSize: 12 }}>
      <div style={{ color: "#e5e7eb", marginBottom: 6, fontWeight: 600 }}>{label}</div>
      {payload.map((p, i) => (
        <div key={i} style={{ color: p.color, fontFamily: "monospace" }}>
          {p.name}: {p.name.includes("Volume") ? fmtCrore(Number(p.value)) : fmtPct(Number(p.value))}
        </div>
      ))}
    </div>
  );
}

export default function CallMoneyChart({ data }: { data: CallMoneyDailySummary[] }) {
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const toggle = (key: string) => setHidden(prev => {
    const next = new Set(prev); next.has(key) ? next.delete(key) : next.add(key); return next;
  });

  const chartData = data.map(r => ({
    date:    fmtDateShort(r.trade_date),
    avg:     r.overnight_wavg_rate ?? null,
    high:    r.overnight_high ?? null,
    low:     r.overnight_low ?? null,
    volume:  r.overnight_volume_crore ?? null,
  }));

  const rates = chartData.map(d => d.avg).filter((v): v is number => v !== null);
  const yMin  = rates.length ? Math.max(0, Math.min(...rates) - 0.5) : 0;
  const yMax  = rates.length ? Math.max(...rates) + 0.5 : 15;

  return (
    <div>
      <div className="flex flex-wrap gap-1.5 mb-3">
        {SERIES.map(s => {
          const active = !hidden.has(s.key);
          return (
            <button key={s.key} onClick={() => toggle(s.key)}
              className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs border transition-all"
              style={{ background: active ? s.color + "22" : "transparent", borderColor: active ? s.color + "88" : "#374151", color: active ? s.color : "#4b5563" }}>
              <span className="w-2 h-2 rounded-full inline-block" style={{ background: active ? s.color : "#4b5563" }} />
              {s.label}
            </button>
          );
        })}
      </div>
      <ResponsiveContainer width="100%" height={280}>
        <ComposedChart data={chartData} margin={{ top: 4, right: 48, bottom: 4, left: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
          <XAxis dataKey="date" tick={{ fill: "#9ca3af", fontSize: 10 }} interval="preserveStartEnd" />
          <YAxis yAxisId="rate" domain={[yMin, yMax]} tick={{ fill: "#9ca3af", fontSize: 10 }} tickFormatter={v => `${v}%`} width={42} />
          <YAxis yAxisId="vol" orientation="right" tick={{ fill: "#374151", fontSize: 10 }} tickFormatter={v => `${(v/1000).toFixed(0)}k`} width={40} />
          <Tooltip content={<CMTooltip />} />
          <Area yAxisId="vol" type="monotone" dataKey="volume" name="Overnight Volume"
            fill="#1f2937" stroke="#374151" fillOpacity={0.4} strokeWidth={1} dot={false}
            hide={hidden.has("volume")} />
          <Line yAxisId="rate" type="monotone" dataKey="high" name="High rate"
            stroke="#dc2626" strokeWidth={1} strokeDasharray="3 2" dot={false} connectNulls
            hide={hidden.has("high")} />
          <Line yAxisId="rate" type="monotone" dataKey="low" name="Low rate"
            stroke="#16a34a" strokeWidth={1} strokeDasharray="3 2" dot={false} connectNulls
            hide={hidden.has("low")} />
          <Line yAxisId="rate" type="monotone" dataKey="avg" name="Avg rate"
            stroke="#f59e0b" strokeWidth={2.5}
            dot={{ r: 3, fill: "#f59e0b", stroke: "white", strokeWidth: 1 }} connectNulls
            hide={hidden.has("avg")} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
