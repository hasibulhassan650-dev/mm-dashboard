"use client";
import { useState } from "react";
import {
  ComposedChart, Bar, Line, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, Legend,
} from "recharts";
import { InterbankRepoRow } from "@/lib/api";
import { fmtDateShort } from "@/lib/format";

/** Secured (repo) against unsecured (overnight call), with repo turnover behind
 *  them. The gap between the two lines is the point of the chart. */
const SERIES = [
  { key: "amount", label: "Turnover (cr)", color: "var(--s-grey)" },
  { key: "war",    label: "Repo WAR",      color: "var(--s-blue)" },
  { key: "call",   label: "Call O/N",      color: "var(--s-amber)" },
];

interface TipProps { active?: boolean; payload?: { value: number; name: string; color: string }[]; label?: string }

function RepoTip({ active, payload, label }: TipProps) {
  if (!active || !payload?.length) return null;
  return (
    <div style={{ background: "var(--tip-bg)", border: "1px solid var(--tip-border)", borderRadius: 8, padding: "8px 12px", fontSize: 12 }}>
      <div style={{ color: "var(--fg)", marginBottom: 6, fontWeight: 600 }}>{label}</div>
      {payload.map((p, i) => (
        <div key={i} style={{ color: p.color, fontFamily: "monospace" }}>
          {p.name}: {p.name.includes("Turnover") ? `৳${Math.round(p.value).toLocaleString()}cr` : `${p.value?.toFixed(2)}%`}
        </div>
      ))}
    </div>
  );
}

export default function RepoChart({ data, call }: { data: InterbankRepoRow[]; call: Record<string, number> }) {
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const toggle = (k: string) => setHidden((prev) => {
    const next = new Set(prev); next.has(k) ? next.delete(k) : next.add(k); return next;
  });

  const chartData = [...data]
    .sort((a, b) => a.trade_date.localeCompare(b.trade_date))
    .map((r) => ({
      date: fmtDateShort(r.trade_date),
      amount: r.amount_crore,
      war: r.war_pct,          // null on a genuine no-trade day — connectNulls is off
      call: call[r.trade_date] ?? null,
    }));

  const rates = chartData.flatMap((d) => [d.war, d.call]).filter((v): v is number => v != null);
  const yMin = rates.length ? Math.min(...rates) - 0.4 : 0;
  const yMax = rates.length ? Math.max(...rates) + 0.4 : 15;

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
          <YAxis yAxisId="rate" domain={[yMin, yMax]} tick={{ fill: "var(--fg-mute)", fontSize: 10 }} tickFormatter={(v) => `${Number(v).toFixed(1)}%`} width={48} />
          <YAxis yAxisId="vol" orientation="right" tick={{ fill: "var(--fg-mute)", fontSize: 10 }} tickFormatter={(v) => `${(v / 1000).toFixed(0)}k`} width={44} />
          <Tooltip content={<RepoTip />} />
          <Legend wrapperStyle={{ fontSize: 11, color: "var(--fg-mute)" }} />
          <Bar yAxisId="vol" dataKey="amount" name="Turnover (cr)" fill="var(--s-grey)" fillOpacity={0.3} hide={hidden.has("amount")} />
          {/* connectNulls stays OFF: a no-trade day is a real gap, not a straight line through it. */}
          <Line yAxisId="rate" type="monotone" dataKey="war" name="Repo WAR" stroke="var(--s-blue)" strokeWidth={2.4} dot={false} hide={hidden.has("war")} />
          <Line yAxisId="rate" type="monotone" dataKey="call" name="Call O/N" stroke="var(--s-amber)" strokeWidth={2} strokeDasharray="4 3" dot={false} connectNulls hide={hidden.has("call")} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
