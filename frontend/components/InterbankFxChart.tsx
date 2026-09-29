"use client";
import { useState } from "react";
import {
  ComposedChart, Bar, Line, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, Legend,
} from "recharts";
import { InterbankFxRow } from "@/lib/api";
import { fmtDateShort } from "@/lib/format";

/** Turnover by segment (bars, stacked) against the spot weighted-average rate
 *  (line, right axis). Only SPOT has a rate — BB publishes none for forward or
 *  swap — so the line is drawn from spot alone, never an average across them. */
const SERIES = [
  { key: "spot",    label: "Spot $m",    color: "var(--s-blue)" },
  { key: "forward", label: "Forward $m", color: "var(--s-teal)" },
  { key: "swap",    label: "Swap $m",    color: "var(--s-violet)" },
  { key: "war",     label: "Spot WAR",   color: "var(--s-amber)" },
];

interface TipProps { active?: boolean; payload?: { value: number; name: string; color: string }[]; label?: string }

function FxTip({ active, payload, label }: TipProps) {
  if (!active || !payload?.length) return null;
  return (
    <div style={{ background: "var(--tip-bg)", border: "1px solid var(--tip-border)", borderRadius: 8, padding: "8px 12px", fontSize: 12 }}>
      <div style={{ color: "var(--fg)", marginBottom: 6, fontWeight: 600 }}>{label}</div>
      {payload.map((p, i) => (
        <div key={i} style={{ color: p.color, fontFamily: "monospace" }}>
          {p.name}: {p.name.includes("WAR") ? p.value?.toFixed(4) : `$${p.value?.toFixed(1)}m`}
        </div>
      ))}
    </div>
  );
}

export default function InterbankFxChart({ data }: { data: InterbankFxRow[] }) {
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const toggle = (k: string) => setHidden((prev) => {
    const next = new Set(prev); next.has(k) ? next.delete(k) : next.add(k); return next;
  });

  // One point per trading day, with each segment's turnover beside it.
  const byDate = new Map<string, { date: string; spot: number | null; forward: number | null; swap: number | null; war: number | null }>();
  for (const r of data) {
    const cur = byDate.get(r.trade_date) ?? { date: r.trade_date, spot: null, forward: null, swap: null, war: null };
    if (r.segment === "SPOT") { cur.spot = r.volume_usd_mn; cur.war = r.war_rate; }
    if (r.segment === "FORWARD") cur.forward = r.volume_usd_mn;
    if (r.segment === "SWAP") cur.swap = r.volume_usd_mn;
    byDate.set(r.trade_date, cur);
  }
  const chartData = [...byDate.values()]
    .sort((a, b) => a.date.localeCompare(b.date))
    .map((d) => ({ ...d, date: fmtDateShort(d.date) }));

  const rates = chartData.map((d) => d.war).filter((v): v is number => v != null);
  const yMin = rates.length ? Math.min(...rates) - 0.15 : 0;
  const yMax = rates.length ? Math.max(...rates) + 0.15 : 1;

  return (
    <div>
      <div className="flex flex-wrap gap-1.5" style={{ marginBottom: 8 }}>
        {SERIES.map((s) => {
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
      <ResponsiveContainer width="100%" height={320}>
        <ComposedChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--grid)" />
          <XAxis dataKey="date" tick={{ fill: "var(--fg-mute)", fontSize: 10 }} interval="preserveStartEnd" />
          <YAxis yAxisId="vol" tick={{ fill: "var(--fg-mute)", fontSize: 10 }} tickFormatter={(v) => `$${v}m`} width={52} />
          <YAxis yAxisId="rate" orientation="right" domain={[yMin, yMax]} tick={{ fill: "var(--fg-mute)", fontSize: 10 }} tickFormatter={(v) => Number(v).toFixed(2)} width={52} />
          <Tooltip content={<FxTip />} />
          <Legend wrapperStyle={{ fontSize: 11, color: "var(--fg-mute)" }} />
          <Bar yAxisId="vol" dataKey="spot" name="Spot $m" stackId="v" fill="var(--s-blue)" fillOpacity={0.8} hide={hidden.has("spot")} />
          <Bar yAxisId="vol" dataKey="forward" name="Forward $m" stackId="v" fill="var(--s-teal)" fillOpacity={0.8} hide={hidden.has("forward")} />
          <Bar yAxisId="vol" dataKey="swap" name="Swap $m" stackId="v" fill="var(--s-violet)" fillOpacity={0.8} hide={hidden.has("swap")} />
          <Line yAxisId="rate" type="monotone" dataKey="war" name="Spot WAR" stroke="var(--s-amber)" strokeWidth={2.4} dot={false} connectNulls hide={hidden.has("war")} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
