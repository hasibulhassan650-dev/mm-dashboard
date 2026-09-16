"use client";
import { useState } from "react";
import {
  ComposedChart, Area, Line, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, ReferenceLine,
} from "recharts";
import { CallMoneyDailySummary } from "@/lib/api";
import { fmtDateShort, fmtCrore, fmtPct } from "@/lib/format";

export interface Corridor { repo: number | null; sdf: number | null; slf: number | null }

const SERIES = [
  { key: "avg",      label: "Avg Rate",  color: "var(--s-amber)" },
  { key: "high",     label: "High Rate", color: "var(--s-red)" },
  { key: "low",      label: "Low Rate",  color: "var(--s-green)" },
  { key: "volume",   label: "Volume",    color: "var(--fg-mute)" },
  { key: "corridor", label: "Corridor",  color: "var(--s-teal)" },
];

interface TooltipProps { active?: boolean; payload?: { value: number; name: string; color: string }[]; label?: string }

function CMTooltip({ active, payload, label }: TooltipProps) {
  if (!active || !payload?.length) return null;
  return (
    <div style={{ background: "var(--tip-bg)", border: "1px solid var(--border)", borderRadius: 8, padding: "8px 12px", fontSize: 12 }}>
      <div style={{ color: "var(--fg)", marginBottom: 6, fontWeight: 600 }}>{label}</div>
      {payload.map((p, i) => (
        <div key={i} style={{ color: p.color, fontFamily: "monospace" }}>
          {p.name}: {p.name.includes("Volume") ? fmtCrore(Number(p.value)) : fmtPct(Number(p.value))}
        </div>
      ))}
    </div>
  );
}

export default function CallMoneyChart({ data, corridor }: { data: CallMoneyDailySummary[]; corridor?: Corridor }) {
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

  const showCorridor = !!corridor && !hidden.has("corridor");
  const corridorVals = corridor
    ? [corridor.repo, corridor.sdf, corridor.slf].filter((v): v is number => v != null)
    : [];

  // Include corridor levels in the domain so reference lines aren't clipped.
  const rates = chartData.map(d => d.avg).filter((v): v is number => v !== null);
  const domainVals = showCorridor ? [...rates, ...corridorVals] : rates;
  const yMin  = domainVals.length ? Math.max(0, Math.min(...domainVals) - 0.5) : 0;
  const yMax  = domainVals.length ? Math.max(...domainVals) + 0.5 : 15;

  return (
    <div>
      <div className="flex flex-wrap gap-1.5 mb-3">
        {SERIES.filter(s => s.key !== "corridor" || corridor).map(s => {
          const active = !hidden.has(s.key);
          return (
            <button key={s.key} onClick={() => toggle(s.key)}
              className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs border transition-all"
              style={{ background: active ? `color-mix(in oklab, ${s.color} 14%, transparent)` : "transparent", borderColor: active ? `color-mix(in oklab, ${s.color} 55%, transparent)` : "var(--border)", color: active ? s.color : "var(--fg-mute)" }}>
              <span className="w-2 h-2 rounded-full inline-block" style={{ background: active ? s.color : "var(--fg-mute)" }} />
              {s.label}
            </button>
          );
        })}
      </div>
      <ResponsiveContainer width="100%" height={280}>
        <ComposedChart data={chartData} margin={{ top: 4, right: 48, bottom: 4, left: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--grid)" />
          <XAxis dataKey="date" tick={{ fill: "var(--fg-mute)", fontSize: 10 }} interval="preserveStartEnd" />
          <YAxis yAxisId="rate" domain={[yMin, yMax]} tick={{ fill: "var(--fg-mute)", fontSize: 10 }} tickFormatter={v => `${v}%`} width={42} />
          <YAxis yAxisId="vol" orientation="right" tick={{ fill: "var(--border)", fontSize: 10 }} tickFormatter={v => `${(v/1000).toFixed(0)}k`} width={40} />
          <Tooltip content={<CMTooltip />} />
          {showCorridor && corridor!.slf != null && (
            <ReferenceLine yAxisId="rate" y={corridor!.slf} stroke="var(--s-red)" strokeDasharray="5 3" strokeOpacity={0.7}
              label={{ value: `SLF ${corridor!.slf}%`, position: "insideTopRight", fill: "var(--s-red)", fontSize: 9 }} />
          )}
          {showCorridor && corridor!.repo != null && (
            <ReferenceLine yAxisId="rate" y={corridor!.repo} stroke="var(--s-teal)" strokeDasharray="5 3" strokeOpacity={0.8}
              label={{ value: `Repo ${corridor!.repo}%`, position: "insideTopRight", fill: "var(--s-teal)", fontSize: 9 }} />
          )}
          {showCorridor && corridor!.sdf != null && (
            <ReferenceLine yAxisId="rate" y={corridor!.sdf} stroke="var(--s-green)" strokeDasharray="5 3" strokeOpacity={0.7}
              label={{ value: `SDF ${corridor!.sdf}%`, position: "insideBottomRight", fill: "var(--s-green)", fontSize: 9 }} />
          )}
          <Area yAxisId="vol" type="monotone" dataKey="volume" name="Overnight Volume"
            fill="var(--grid)" stroke="var(--border)" fillOpacity={0.4} strokeWidth={1} dot={false}
            hide={hidden.has("volume")} />
          <Line yAxisId="rate" type="monotone" dataKey="high" name="High rate"
            stroke="var(--s-red)" strokeWidth={1} strokeDasharray="3 2" dot={false} connectNulls
            hide={hidden.has("high")} />
          <Line yAxisId="rate" type="monotone" dataKey="low" name="Low rate"
            stroke="var(--s-green)" strokeWidth={1} strokeDasharray="3 2" dot={false} connectNulls
            hide={hidden.has("low")} />
          <Line yAxisId="rate" type="monotone" dataKey="avg" name="Avg rate"
            stroke="var(--s-amber)" strokeWidth={2.5}
            dot={{ r: 3, fill: "var(--s-amber)", stroke: "var(--panel)", strokeWidth: 1 }} connectNulls
            hide={hidden.has("avg")} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
