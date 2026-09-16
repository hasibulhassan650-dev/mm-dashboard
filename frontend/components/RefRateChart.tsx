"use client";
import { useState } from "react";
import {
  LineChart, Line, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, ReferenceLine,
} from "recharts";
import { RefRateRow } from "@/lib/api";
import { fmtDateShort, fmtPct } from "@/lib/format";

const PRODUCT_COLORS: Record<string, string> = {
  Overnight: "var(--s-sky)",
  "1W":       "var(--s-violet)",
  "1M":       "var(--s-green)",
  "3M":       "var(--s-orange)",
};

interface TooltipProps { active?: boolean; payload?: { value: number; name: string; color: string }[]; label?: string }

function RRTooltip({ active, payload, label }: TooltipProps) {
  if (!active || !payload?.length) return null;
  return (
    <div style={{ background: "var(--tip-bg)", border: "1px solid var(--border)", borderRadius: 8, padding: "8px 12px", fontSize: 12 }}>
      <div style={{ color: "var(--fg)", marginBottom: 6, fontWeight: 600 }}>{label}</div>
      {payload.map((p, i) => (
        <div key={i} style={{ color: p.color, fontFamily: "monospace" }}>
          {p.name}: {fmtPct(p.value)}
        </div>
      ))}
    </div>
  );
}

interface Props { rows: RefRateRow[]; rateType: "DOMMR" | "BOFR"; repoRate?: number | null }

export default function RefRateChart({ rows, rateType, repoRate }: Props) {
  const filtered = rows.filter(r => r.rate_type === rateType);
  const products = [...new Set(filtered.map(r => r.product))].sort();

  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const toggle = (key: string) => setHidden(prev => {
    const next = new Set(prev); next.has(key) ? next.delete(key) : next.add(key); return next;
  });

  const dateMap: Record<string, Record<string, number | null>> = {};
  for (const r of filtered) {
    const d = r.trade_date.slice(0, 10);
    if (!dateMap[d]) dateMap[d] = {};
    dateMap[d][r.product] = r.rate_pct;
  }

  const chartData = Object.entries(dateMap)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, vals]) => ({ date: fmtDateShort(date), ...vals }));

  const allRates = filtered.map(r => r.rate_pct).filter((v): v is number => v !== null);
  const domainVals = repoRate != null ? [...allRates, repoRate] : allRates;
  const yMin = domainVals.length ? Math.max(0, Math.min(...domainVals) - 0.2) : 8;
  const yMax = domainVals.length ? Math.max(...domainVals) + 0.2 : 12;

  return (
    <div>
      <div className="flex flex-wrap gap-1.5 mb-3">
        {products.map(p => {
          const active = !hidden.has(p);
          const color = PRODUCT_COLORS[p] ?? "var(--fg-dim)";
          return (
            <button key={p} onClick={() => toggle(p)}
              className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs border transition-all"
              style={{ background: active ? `color-mix(in oklab, ${color} 14%, transparent)` : "transparent", borderColor: active ? `color-mix(in oklab, ${color} 55%, transparent)` : "var(--border)", color: active ? color : "var(--fg-mute)" }}>
              <span className="w-2 h-2 rounded-full inline-block" style={{ background: active ? color : "var(--fg-mute)" }} />
              {p}
            </button>
          );
        })}
      </div>
      <ResponsiveContainer width="100%" height={260}>
        <LineChart data={chartData} margin={{ top: 4, right: 16, bottom: 4, left: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--grid)" />
          <XAxis dataKey="date" tick={{ fill: "var(--fg-mute)", fontSize: 10 }} interval="preserveStartEnd" />
          <YAxis domain={[yMin, yMax]} tick={{ fill: "var(--fg-mute)", fontSize: 10 }} tickFormatter={v => `${v}%`} width={42} />
          <Tooltip content={<RRTooltip />} />
          {repoRate != null && (
            <ReferenceLine y={repoRate} stroke="var(--s-teal)" strokeDasharray="5 3" strokeOpacity={0.7}
              label={{ value: `Repo ${repoRate}%`, position: "insideTopRight", fill: "var(--s-teal)", fontSize: 9 }} />
          )}
          {products.map(p => (
            <Line key={p} type="monotone" dataKey={p} name={p}
              stroke={PRODUCT_COLORS[p] ?? "var(--fg-dim)"} strokeWidth={2}
              dot={false} connectNulls hide={hidden.has(p)} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
