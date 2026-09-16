"use client";
import { useState } from "react";
import {
  ComposedChart, Area, Line, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid,
} from "recharts";
import { FxAuctionRow } from "@/lib/api";
import { fmtDateShort, fmtUSDmn, fmtRate } from "@/lib/format";

const SERIES = [
  { key: "wavg",     label: "Wtd Avg Rate", color: "var(--s-sky)" },
  { key: "cutoff",   label: "Cutoff Rate",  color: "var(--s-orange)" },
  { key: "accepted", label: "Accepted ($m)", color: "var(--s-blue)" },
];

interface TooltipProps { active?: boolean; payload?: { value: number; name: string; color: string }[]; label?: string }

function FxTooltip({ active, payload, label }: TooltipProps) {
  if (!active || !payload?.length) return null;
  return (
    <div style={{ background: "var(--tip-bg)", border: "1px solid var(--border)", borderRadius: 8, padding: "8px 12px", fontSize: 12 }}>
      <div style={{ color: "var(--fg)", marginBottom: 6, fontWeight: 600 }}>{label}</div>
      {payload.map((p, i) => (
        <div key={i} style={{ color: p.color, fontFamily: "monospace" }}>
          {p.name}: {p.name === "Accepted ($m)" ? fmtUSDmn(Number(p.value)) : fmtRate(Number(p.value))}
        </div>
      ))}
    </div>
  );
}

export default function FxChart({ data }: { data: FxAuctionRow[] }) {
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const toggle = (key: string) => setHidden(prev => {
    const next = new Set(prev); next.has(key) ? next.delete(key) : next.add(key); return next;
  });

  const chartData = [...data].reverse().map(r => ({
    date:     fmtDateShort(r.auction_date),
    wavg:     r.weighted_avg_rate ?? null,
    cutoff:   r.cutoff_rate ?? null,
    accepted: r.accepted_amount_usd_mill ?? null,
  }));

  const rates = chartData.flatMap(d => [d.wavg, d.cutoff]).filter((v): v is number => v !== null);
  const yMin  = rates.length ? Math.floor((Math.min(...rates) - 0.1) * 10) / 10 : 120;
  const yMax  = rates.length ? Math.ceil((Math.max(...rates) + 0.1) * 10) / 10 : 125;

  return (
    <div>
      <div className="flex flex-wrap gap-1.5 mb-3">
        {SERIES.map(s => {
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
      <ResponsiveContainer width="100%" height={300}>
        <ComposedChart data={chartData} margin={{ top: 4, right: 52, bottom: 4, left: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--grid)" />
          <XAxis dataKey="date" tick={{ fill: "var(--fg-mute)", fontSize: 10 }} interval="preserveStartEnd" />
          <YAxis yAxisId="rate" domain={[yMin, yMax]} tick={{ fill: "var(--fg-mute)", fontSize: 10 }} tickFormatter={v => v.toFixed(2)} width={50} />
          <YAxis yAxisId="vol" orientation="right" tick={{ fill: "var(--border)", fontSize: 10 }} tickFormatter={v => `$${v}m`} width={46} />
          <Tooltip content={<FxTooltip />} />
          <Area yAxisId="vol" type="monotone" dataKey="accepted" name="Accepted ($m)"
            fill="var(--s-blue)" stroke="var(--s-blue)" fillOpacity={0.35} strokeWidth={1} dot={false}
            hide={hidden.has("accepted")} />
          <Line yAxisId="rate" type="monotone" dataKey="cutoff" name="Cutoff rate"
            stroke="var(--s-orange)" strokeWidth={1.5} strokeDasharray="4 2" dot={false} connectNulls
            hide={hidden.has("cutoff")} />
          <Line yAxisId="rate" type="monotone" dataKey="wavg" name="Wtd avg rate"
            stroke="var(--s-sky)" strokeWidth={2.5}
            dot={{ r: 2.5, fill: "var(--s-sky)", stroke: "var(--panel)", strokeWidth: 1 }} connectNulls
            hide={hidden.has("wavg")} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
