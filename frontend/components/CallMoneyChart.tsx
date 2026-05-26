"use client";
import {
  ComposedChart, Area, Line, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, ReferenceLine,
} from "recharts";
import { CallMoneyDailySummary } from "@/lib/api";

interface TooltipProps { active?: boolean; payload?: { value: number; name: string; color: string }[]; label?: string }

function CMTooltip({ active, payload, label }: TooltipProps) {
  if (!active || !payload?.length) return null;
  return (
    <div style={{ background: "#111827", border: "1px solid #374151", borderRadius: 8, padding: "8px 12px", fontSize: 12 }}>
      <div style={{ color: "#e5e7eb", marginBottom: 6, fontWeight: 600 }}>{label}</div>
      {payload.map((p, i) => (
        <div key={i} style={{ color: p.color, fontFamily: "monospace" }}>
          {p.name}: {p.name.includes("Volume") ? `${Number(p.value).toLocaleString()} cr` : `${Number(p.value).toFixed(2)}%`}
        </div>
      ))}
    </div>
  );
}

export default function CallMoneyChart({ data }: { data: CallMoneyDailySummary[] }) {
  const chartData = data.map(r => ({
    date:    r.trade_date.slice(5),
    avg:     r.overnight_wavg_rate ?? null,
    high:    r.overnight_high ?? null,
    low:     r.overnight_low ?? null,
    volume:  r.overnight_volume_crore ?? null,
  }));

  const rates = chartData.map(d => d.avg).filter((v): v is number => v !== null);
  const yMin  = rates.length ? Math.max(0, Math.min(...rates) - 0.5) : 0;
  const yMax  = rates.length ? Math.max(...rates) + 0.5 : 15;

  return (
    <ResponsiveContainer width="100%" height={280}>
      <ComposedChart data={chartData} margin={{ top: 4, right: 48, bottom: 4, left: 4 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
        <XAxis dataKey="date" tick={{ fill: "#9ca3af", fontSize: 10 }} interval="preserveStartEnd" />
        <YAxis
          yAxisId="rate" domain={[yMin, yMax]}
          tick={{ fill: "#9ca3af", fontSize: 10 }}
          tickFormatter={v => `${v}%`} width={42}
        />
        <YAxis
          yAxisId="vol" orientation="right"
          tick={{ fill: "#374151", fontSize: 10 }}
          tickFormatter={v => `${(v / 1000).toFixed(0)}k`} width={40}
        />
        <Tooltip content={<CMTooltip />} />
        <Area
          yAxisId="vol" type="monotone" dataKey="volume"
          name="Overnight Volume" fill="#1f2937" stroke="#374151"
          fillOpacity={0.4} strokeWidth={1} dot={false}
        />
        <Line
          yAxisId="rate" type="monotone" dataKey="high"
          name="High rate" stroke="#dc2626" strokeWidth={1}
          strokeDasharray="3 2" dot={false} connectNulls
        />
        <Line
          yAxisId="rate" type="monotone" dataKey="low"
          name="Low rate" stroke="#16a34a" strokeWidth={1}
          strokeDasharray="3 2" dot={false} connectNulls
        />
        <Line
          yAxisId="rate" type="monotone" dataKey="avg"
          name="Avg rate" stroke="#f59e0b" strokeWidth={2.5}
          dot={{ r: 3, fill: "#f59e0b", stroke: "white", strokeWidth: 1 }}
          connectNulls
        />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
