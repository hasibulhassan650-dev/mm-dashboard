"use client";
import { useState } from "react";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Legend } from "recharts";
import { YieldRow } from "@/lib/api";

const TENOR_ORDER = ["14D","91D","182D","364D","2Y","3Y_FRTB","5Y","10Y","15Y","20Y"];
const TENOR_COLORS: Record<string, string> = {
  "14D":     "var(--s-lime)",
  "91D":     "var(--s-teal)",
  "182D":    "var(--s-sky)",
  "364D":    "var(--s-blue)",
  "2Y":      "var(--s-violet)",
  "3Y_FRTB": "var(--s-orange)",
  "5Y":      "var(--s-blue)",
  "10Y":     "var(--s-pink)",
  "15Y":     "var(--s-red)",
  "20Y":     "var(--s-maroon)",
};

const N_OPTIONS = [6, 10, 20, 50, 0] as const;

export default function YieldTrendChart({ data }: { data: YieldRow[] }) {
  const available = TENOR_ORDER.filter(t => data.some(r => r.tenor_label === t));
  const [selected, setSelected] = useState<string[]>(available);
  const [nAuctions, setNAuctions] = useState<number>(6);

  const toggle = (t: string) =>
    setSelected(prev => prev.includes(t) ? prev.filter(x => x !== t) : [...prev, t]);

  const byTenor: Record<string, YieldRow[]> = {};
  for (const r of data) {
    if (!byTenor[r.tenor_label]) byTenor[r.tenor_label] = [];
    byTenor[r.tenor_label].push(r);
  }

  const kept: YieldRow[] = [];
  for (const tenor of selected) {
    const rows = [...(byTenor[tenor] ?? [])].sort((a, b) => a.auction_date.localeCompare(b.auction_date));
    kept.push(...(nAuctions === 0 ? rows : rows.slice(-nAuctions)));
  }

  const dateMap = new Map<string, Record<string, number | string>>();
  for (const r of kept) {
    if (!dateMap.has(r.auction_date)) dateMap.set(r.auction_date, { date: r.auction_date });
    dateMap.get(r.auction_date)![r.tenor_label] = r.cutoff_yield_pct;
  }
  const chartData = Array.from(dateMap.values()).sort((a, b) =>
    String(a.date).localeCompare(String(b.date))
  );

  return (
    <div>
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <div className="flex flex-wrap gap-1.5">
          {available.map(t => (
            <button
              key={t}
              onClick={() => toggle(t)}
              className={`px-2 py-0.5 rounded text-xs border transition-colors ${
                selected.includes(t)
                  ? "border-transparent t-fg"
                  : "bd t-mute bg-transparent"
              }`}
              style={selected.includes(t) ? { background: TENOR_COLORS[t] ?? "var(--fg-mute)" } : {}}
            >
              {t}
            </button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-2 text-xs t-dim">
          <span>Last N per tenor:</span>
          {N_OPTIONS.map(n => (
            <button
              key={n}
              onClick={() => setNAuctions(n)}
              className={`px-2 py-0.5 rounded ${nAuctions === n ? "b-accent t-fg" : "t-dim hover-fg"}`}
            >
              {n === 0 ? "All" : n}
            </button>
          ))}
        </div>
      </div>

      <ResponsiveContainer width="100%" height={320}>
        <LineChart data={chartData} margin={{ top: 8, right: 16, bottom: 4, left: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--grid)" />
          <XAxis dataKey="date" tick={{ fill: "var(--fg-mute)", fontSize: 10 }}
            tickFormatter={d => String(d).slice(2, 7)} interval="preserveStartEnd" />
          <YAxis domain={["auto", "auto"]} tick={{ fill: "var(--fg-mute)", fontSize: 10 }}
            tickFormatter={v => `${v}%`} width={42} />
          <Tooltip
            contentStyle={{ backgroundColor: "var(--tip-bg)", border: "1px solid var(--border)", borderRadius: 8 }}
            labelStyle={{ color: "var(--fg)", fontSize: 11 }}
            formatter={(v, name) => [`${Number(v).toFixed(4)}%`, String(name)]}
          />
          <Legend wrapperStyle={{ fontSize: 11, color: "var(--fg-mute)" }} />
          {selected.map(t => (
            <Line
              key={t} type="monotone" dataKey={t}
              stroke={TENOR_COLORS[t] ?? "var(--fg-mute)"} strokeWidth={2}
              dot={{ r: 4, fill: TENOR_COLORS[t] ?? "var(--fg-mute)", stroke: "var(--panel)", strokeWidth: 1 }}
              connectNulls
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
