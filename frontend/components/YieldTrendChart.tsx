"use client";
import { useState } from "react";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Legend } from "recharts";
import { YieldRow } from "@/lib/api";
import { byTenor as compareTenor } from "@/lib/tenor";

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

// A tenor with no assigned colour still gets a distinct one (28D, a future 5Y_FRTB ...), not the same grey.
const FALLBACK = Object.values(TENOR_COLORS);
const colorOf = (t: string) => TENOR_COLORS[t] ?? FALLBACK[[...t].reduce((s, c) => s + c.charCodeAt(0), 0) % FALLBACK.length];

const N_OPTIONS = [6, 10, 20, 50, 0] as const;

export default function YieldTrendChart({ data }: { data: YieldRow[] }) {
  const available = [...new Set(data.map(r => r.tenor_label))].sort(compareTenor);   // every tenor present, shortest first
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
              style={selected.includes(t) ? { background: colorOf(t) } : {}}
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
              stroke={colorOf(t)} strokeWidth={2}
              dot={{ r: 4, fill: colorOf(t), stroke: "var(--panel)", strokeWidth: 1 }}
              connectNulls
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
