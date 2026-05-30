"use client";
import { useState } from "react";
import {
  ComposedChart, Line, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, ReferenceLine,
} from "recharts";
import { CurveSlopeRow } from "@/lib/api";
import { fmtDateShort } from "@/lib/format";

const SERIES = [
  { key: "two_ten",   label: "2s10s (10Y−2Y)",  color: "#38bdf8" },
  { key: "short_ten", label: "91D−10Y",         color: "#a78bfa" },
];

interface TipProps { active?: boolean; payload?: { value: number; name: string; color: string }[]; label?: string }
function SlopeTooltip({ active, payload, label }: TipProps) {
  if (!active || !payload?.length) return null;
  return (
    <div style={{ background: "#111827", border: "1px solid #374151", borderRadius: 8, padding: "8px 12px", fontSize: 12 }}>
      <div style={{ color: "#e5e7eb", marginBottom: 6, fontWeight: 600 }}>{label}</div>
      {payload.map((p, i) => (
        <div key={i} style={{ color: p.color, fontFamily: "monospace" }}>
          {p.name}: {p.value == null ? "—" : `${(p.value * 100).toFixed(0)} bps`}
        </div>
      ))}
    </div>
  );
}

export default function CurveSlopeChart({ data }: { data: CurveSlopeRow[] }) {
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const toggle = (key: string) => setHidden(prev => {
    const next = new Set(prev); next.has(key) ? next.delete(key) : next.add(key); return next;
  });

  const chartData = data.map(r => ({ date: fmtDateShort(r.date), two_ten: r.two_ten, short_ten: r.short_ten }));

  if (chartData.length === 0) {
    return <div className="text-sm text-gray-500 py-8 text-center">Curve-slope data not available yet.</div>;
  }

  return (
    <div>
      <div className="flex flex-wrap gap-1.5 mb-3">
        {SERIES.map(s => {
          const active = !hidden.has(s.key);
          return (
            <button key={s.key} onClick={() => toggle(s.key)}
              className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs border transition-all"
              style={{ background: active ? s.color + "22" : "transparent", borderColor: active ? s.color + "88" : "#374151", color: active ? s.color : "#4b5563" }}>
              <span className="w-2 h-2 rounded-full inline-block" style={{ background: active ? s.color : "#4b5563" }} />
              {s.label}
            </button>
          );
        })}
      </div>
      <ResponsiveContainer width="100%" height={260}>
        <ComposedChart data={chartData} margin={{ top: 4, right: 12, bottom: 4, left: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
          <XAxis dataKey="date" tick={{ fill: "#9ca3af", fontSize: 10 }} interval="preserveStartEnd" />
          <YAxis tick={{ fill: "#9ca3af", fontSize: 10 }} tickFormatter={v => `${(v * 100).toFixed(0)}`} width={40}
            label={{ value: "bps", angle: -90, position: "insideLeft", fill: "#6b7280", fontSize: 10 }} />
          <Tooltip content={<SlopeTooltip />} />
          <ReferenceLine y={0} stroke="#6b7280" strokeDasharray="4 2" />
          <Line type="monotone" dataKey="two_ten" name="2s10s (10Y−2Y)" stroke="#38bdf8" strokeWidth={2} dot={false} connectNulls hide={hidden.has("two_ten")} />
          <Line type="monotone" dataKey="short_ten" name="91D−10Y" stroke="#a78bfa" strokeWidth={2} dot={false} connectNulls hide={hidden.has("short_ten")} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
