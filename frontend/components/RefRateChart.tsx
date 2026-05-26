"use client";
import {
  LineChart, Line, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, Legend,
} from "recharts";
import { RefRateRow } from "@/lib/api";

const PRODUCT_COLORS: Record<string, string> = {
  Overnight: "#38bdf8",
  "1W":       "#a78bfa",
  "1M":       "#34d399",
  "3M":       "#fb923c",
};

interface TooltipProps { active?: boolean; payload?: { value: number; name: string; color: string }[]; label?: string }

function RRTooltip({ active, payload, label }: TooltipProps) {
  if (!active || !payload?.length) return null;
  return (
    <div style={{ background: "#111827", border: "1px solid #374151", borderRadius: 8, padding: "8px 12px", fontSize: 12 }}>
      <div style={{ color: "#e5e7eb", marginBottom: 6, fontWeight: 600 }}>{label}</div>
      {payload.map((p, i) => (
        <div key={i} style={{ color: p.color, fontFamily: "monospace" }}>
          {p.name}: {p.value != null ? `${Number(p.value).toFixed(2)}%` : "—"}
        </div>
      ))}
    </div>
  );
}

interface Props {
  rows: RefRateRow[];
  rateType: "DOMMR" | "BOFR";
}

export default function RefRateChart({ rows, rateType }: Props) {
  const filtered = rows.filter(r => r.rate_type === rateType);

  // Build per-date map: { date -> { product -> rate_pct } }
  const dateMap: Record<string, Record<string, number | null>> = {};
  for (const r of filtered) {
    const d = r.trade_date.slice(0, 10);
    if (!dateMap[d]) dateMap[d] = {};
    dateMap[d][r.product] = r.rate_pct;
  }

  const products = [...new Set(filtered.map(r => r.product))].sort();
  const chartData = Object.entries(dateMap)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, vals]) => ({ date: date.slice(5), ...vals }));

  const allRates = filtered.map(r => r.rate_pct).filter((v): v is number => v !== null);
  const yMin = allRates.length ? Math.max(0, Math.min(...allRates) - 0.2) : 8;
  const yMax = allRates.length ? Math.max(...allRates) + 0.2 : 12;

  return (
    <ResponsiveContainer width="100%" height={260}>
      <LineChart data={chartData} margin={{ top: 4, right: 16, bottom: 4, left: 4 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
        <XAxis dataKey="date" tick={{ fill: "#9ca3af", fontSize: 10 }} interval="preserveStartEnd" />
        <YAxis domain={[yMin, yMax]} tick={{ fill: "#9ca3af", fontSize: 10 }} tickFormatter={v => `${v}%`} width={42} />
        <Tooltip content={<RRTooltip />} />
        <Legend wrapperStyle={{ fontSize: 11, color: "#9ca3af" }} />
        {products.map(p => (
          <Line
            key={p}
            type="monotone"
            dataKey={p}
            name={p}
            stroke={PRODUCT_COLORS[p] ?? "#94a3b8"}
            strokeWidth={2}
            dot={false}
            connectNulls
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
