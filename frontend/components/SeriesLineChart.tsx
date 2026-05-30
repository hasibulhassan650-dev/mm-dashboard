"use client";
import { useState } from "react";
import {
  LineChart, Line, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, ReferenceLine,
} from "recharts";

export interface SeriesDef { key: string; label: string; color: string; dashed?: boolean }

interface TipProps {
  active?: boolean;
  payload?: { value: number; name: string; color: string }[];
  label?: string;
  unit?: string;
  dp?: number;
}

function SeriesTooltip({ active, payload, label, unit = "", dp = 1 }: TipProps) {
  if (!active || !payload?.length) return null;
  return (
    <div style={{ background: "#111827", border: "1px solid #374151", borderRadius: 8, padding: "8px 12px", fontSize: 12 }}>
      <div style={{ color: "#e5e7eb", marginBottom: 6, fontWeight: 600 }}>{label}</div>
      {payload.map((p, i) => (
        <div key={i} style={{ color: p.color, fontFamily: "monospace" }}>
          {p.name}: {p.value == null ? "—" : `${Number(p.value).toFixed(dp)}${unit}`}
        </div>
      ))}
    </div>
  );
}

interface Props {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  data: Record<string, any>[];
  xKey: string;
  series: SeriesDef[];
  /** suffix appended to values, e.g. "%" */
  unit?: string;
  /** decimal places in tooltip/axis */
  dp?: number;
  height?: number;
  zeroLine?: boolean;
}

export default function SeriesLineChart({ data, xKey, series, unit = "", dp = 1, height = 240, zeroLine = false }: Props) {
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const toggle = (key: string) => setHidden(prev => {
    const next = new Set(prev); next.has(key) ? next.delete(key) : next.add(key); return next;
  });

  if (data.length === 0) {
    return <div className="text-sm text-gray-500 py-8 text-center">No data yet.</div>;
  }

  return (
    <div>
      <div className="flex flex-wrap gap-1.5 mb-3">
        {series.map(s => {
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
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={data} margin={{ top: 4, right: 12, bottom: 4, left: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
          <XAxis dataKey={xKey} tick={{ fill: "#9ca3af", fontSize: 10 }} interval="preserveStartEnd" />
          <YAxis tick={{ fill: "#9ca3af", fontSize: 10 }} tickFormatter={v => `${v}${unit}`} width={42} />
          <Tooltip content={<SeriesTooltip unit={unit} dp={dp} />} />
          {zeroLine && <ReferenceLine y={0} stroke="#6b7280" strokeDasharray="4 2" />}
          {series.map(s => (
            <Line key={s.key} type="monotone" dataKey={s.key} name={s.label}
              stroke={s.color} strokeWidth={2} strokeDasharray={s.dashed ? "4 2" : undefined}
              dot={false} connectNulls hide={hidden.has(s.key)} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
