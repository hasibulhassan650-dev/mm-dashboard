"use client";
import {
  ComposedChart, Area, Line, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid,
} from "recharts";
import { FxAuctionRow } from "@/lib/api";

interface TooltipProps { active?: boolean; payload?: { value: number; name: string; color: string }[]; label?: string }

function FxTooltip({ active, payload, label }: TooltipProps) {
  if (!active || !payload?.length) return null;
  return (
    <div style={{ background: "#111827", border: "1px solid #374151", borderRadius: 8, padding: "8px 12px", fontSize: 12 }}>
      <div style={{ color: "#e5e7eb", marginBottom: 6, fontWeight: 600 }}>{label}</div>
      {payload.map((p, i) => (
        <div key={i} style={{ color: p.color, fontFamily: "monospace" }}>
          {p.name}: {
            p.name === "Accepted ($m)"
              ? `$${Number(p.value).toFixed(1)}m`
              : Number(p.value).toFixed(4)
          }
        </div>
      ))}
    </div>
  );
}

export default function FxChart({ data }: { data: FxAuctionRow[] }) {
  const chartData = [...data].reverse().map(r => ({
    date:     r.auction_date.slice(5),
    wavg:     r.weighted_avg_rate ?? null,
    cutoff:   r.cutoff_rate ?? null,
    accepted: r.accepted_amount_usd_mill ?? null,
  }));

  const rates = chartData.flatMap(d => [d.wavg, d.cutoff]).filter((v): v is number => v !== null);
  const yMin  = rates.length ? Math.floor((Math.min(...rates) - 0.1) * 10) / 10 : 120;
  const yMax  = rates.length ? Math.ceil((Math.max(...rates) + 0.1) * 10) / 10 : 125;

  return (
    <ResponsiveContainer width="100%" height={300}>
      <ComposedChart data={chartData} margin={{ top: 4, right: 52, bottom: 4, left: 4 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
        <XAxis dataKey="date" tick={{ fill: "#9ca3af", fontSize: 10 }} interval="preserveStartEnd" />
        <YAxis
          yAxisId="rate" domain={[yMin, yMax]}
          tick={{ fill: "#9ca3af", fontSize: 10 }}
          tickFormatter={v => v.toFixed(2)} width={50}
        />
        <YAxis
          yAxisId="vol" orientation="right"
          tick={{ fill: "#374151", fontSize: 10 }}
          tickFormatter={v => `$${v}m`} width={46}
        />
        <Tooltip content={<FxTooltip />} />
        <Area
          yAxisId="vol" type="monotone" dataKey="accepted"
          name="Accepted ($m)" fill="#1e3a5f" stroke="#1d4ed8"
          fillOpacity={0.35} strokeWidth={1} dot={false}
        />
        <Line
          yAxisId="rate" type="monotone" dataKey="cutoff"
          name="Cutoff rate" stroke="#f97316" strokeWidth={1.5}
          strokeDasharray="4 2" dot={false} connectNulls
        />
        <Line
          yAxisId="rate" type="monotone" dataKey="wavg"
          name="Wtd avg rate" stroke="#38bdf8" strokeWidth={2.5}
          dot={{ r: 2.5, fill: "#38bdf8", stroke: "white", strokeWidth: 1 }}
          connectNulls
        />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
