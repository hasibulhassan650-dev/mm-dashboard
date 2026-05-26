"use client";
import { useState } from "react";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Legend } from "recharts";
import { YieldRow } from "@/lib/api";

const TENOR_ORDER = ["14D","91D","182D","364D","2Y","3Y_FRTB","5Y","10Y","15Y","20Y"];
const TENOR_COLORS: Record<string, string> = {
  "14D":     "#a3e635",
  "91D":     "#0d9488",
  "182D":    "#0891b2",
  "364D":    "#2563eb",
  "2Y":      "#7c3aed",
  "3Y_FRTB": "#d1780f",
  "5Y":      "#1f6feb",
  "10Y":     "#db2777",
  "15Y":     "#dc2626",
  "20Y":     "#7f1d1d",
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
                  ? "border-transparent text-white"
                  : "border-gray-700 text-gray-500 bg-transparent"
              }`}
              style={selected.includes(t) ? { background: TENOR_COLORS[t] ?? "#888" } : {}}
            >
              {t}
            </button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-2 text-xs text-gray-400">
          <span>Last N per tenor:</span>
          {N_OPTIONS.map(n => (
            <button
              key={n}
              onClick={() => setNAuctions(n)}
              className={`px-2 py-0.5 rounded ${nAuctions === n ? "bg-blue-600 text-white" : "text-gray-400 hover:text-white"}`}
            >
              {n === 0 ? "All" : n}
            </button>
          ))}
        </div>
      </div>

      <ResponsiveContainer width="100%" height={320}>
        <LineChart data={chartData} margin={{ top: 8, right: 16, bottom: 4, left: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
          <XAxis dataKey="date" tick={{ fill: "#9ca3af", fontSize: 10 }}
            tickFormatter={d => String(d).slice(2, 7)} interval="preserveStartEnd" />
          <YAxis domain={["auto", "auto"]} tick={{ fill: "#9ca3af", fontSize: 10 }}
            tickFormatter={v => `${v}%`} width={42} />
          <Tooltip
            contentStyle={{ backgroundColor: "#111827", border: "1px solid #374151", borderRadius: 8 }}
            labelStyle={{ color: "#e5e7eb", fontSize: 11 }}
            formatter={(v, name) => [`${Number(v).toFixed(4)}%`, String(name)]}
          />
          <Legend wrapperStyle={{ fontSize: 11, color: "#9ca3af" }} />
          {selected.map(t => (
            <Line
              key={t} type="monotone" dataKey={t}
              stroke={TENOR_COLORS[t] ?? "#6b7280"} strokeWidth={2}
              dot={{ r: 4, fill: TENOR_COLORS[t] ?? "#6b7280", stroke: "white", strokeWidth: 1 }}
              connectNulls
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
