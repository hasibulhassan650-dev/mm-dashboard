"use client";
import { useState } from "react";
import {
  ComposedChart, Line, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid,
} from "recharts";
import { MacroRow } from "@/lib/api";
import { fmtMonth } from "@/lib/format";

const SERIES = [
  { key: "gross", label: "Gross reserves", color: "var(--s-sky)" },
  { key: "net",   label: "Net (BPM6)",     color: "var(--s-teal)" },
];

interface TipProps { active?: boolean; payload?: { value: number; name: string; color: string }[]; label?: string }
function RTooltip({ active, payload, label }: TipProps) {
  if (!active || !payload?.length) return null;
  return (
    <div style={{ background: "var(--tip-bg)", border: "1px solid var(--border)", borderRadius: 8, padding: "8px 12px", fontSize: 12 }}>
      <div style={{ color: "var(--fg)", marginBottom: 6, fontWeight: 600 }}>{label}</div>
      {payload.map((p, i) => (
        <div key={i} style={{ color: p.color, fontFamily: "monospace" }}>
          {p.name}: {p.value == null ? "—" : `$${Number(p.value).toFixed(1)}bn`}
        </div>
      ))}
    </div>
  );
}

export default function ReservesChart({ data }: { data: MacroRow[] }) {
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const toggle = (key: string) => setHidden(prev => {
    const next = new Set(prev); next.has(key) ? next.delete(key) : next.add(key); return next;
  });

  const chartData = data.map(r => ({
    month: fmtMonth(r.month),
    gross: r.gross_reserves_usd_bn,
    net:   r.net_reserves_bpm6_usd_bn,
  }));

  if (chartData.length === 0) {
    return <div className="text-sm t-mute py-8 text-center">No reserves data yet.</div>;
  }

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
      <ResponsiveContainer width="100%" height={260}>
        <ComposedChart data={chartData} margin={{ top: 4, right: 12, bottom: 4, left: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--grid)" />
          <XAxis dataKey="month" tick={{ fill: "var(--fg-mute)", fontSize: 10 }} interval="preserveStartEnd" />
          <YAxis tick={{ fill: "var(--fg-mute)", fontSize: 10 }} tickFormatter={v => `$${v}bn`} width={46} />
          <Tooltip content={<RTooltip />} />
          <Line type="monotone" dataKey="gross" name="Gross reserves" stroke="var(--s-sky)" strokeWidth={2.5}
            dot={{ r: 2.5, fill: "var(--s-sky)" }} connectNulls hide={hidden.has("gross")} />
          <Line type="monotone" dataKey="net" name="Net (BPM6)" stroke="var(--s-teal)" strokeWidth={2} strokeDasharray="4 2"
            dot={false} connectNulls hide={hidden.has("net")} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
