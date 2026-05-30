"use client";
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, ReferenceLine,
} from "recharts";
import { OmoOutstandingRow } from "@/lib/api";
import { netLiquiditySeries } from "@/lib/analytics";
import { fmtDateShort, fmtCrore } from "@/lib/format";

interface TipProps { active?: boolean; payload?: { value: number }[]; label?: string }
function NLTooltip({ active, payload, label }: TipProps) {
  if (!active || !payload?.length) return null;
  const v = payload[0].value;
  return (
    <div style={{ background: "#111827", border: "1px solid #374151", borderRadius: 8, padding: "8px 12px", fontSize: 12 }}>
      <div style={{ color: "#e5e7eb", fontWeight: 600 }}>{label}</div>
      <div style={{ color: v >= 0 ? "#f59e0b" : "#34d399", fontFamily: "monospace" }}>
        net {v >= 0 ? "+" : ""}{fmtCrore(v)}
      </div>
      <div style={{ color: "#9ca3af" }}>{v >= 0 ? "BB injecting (tight)" : "BB absorbing (flush)"}</div>
    </div>
  );
}

export default function OmoNetLiquidityChart({ rows }: { rows: OmoOutstandingRow[] }) {
  const data = netLiquiditySeries(rows);
  return (
    <ResponsiveContainer width="100%" height={220}>
      <AreaChart data={data.map(d => ({ ...d, date: fmtDateShort(d.date) }))} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
        <defs>
          <linearGradient id="netliq" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#f59e0b" stopOpacity={0.5} />
            <stop offset="100%" stopColor="#f59e0b" stopOpacity={0.05} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
        <XAxis dataKey="date" tick={{ fill: "#9ca3af", fontSize: 10 }} interval="preserveStartEnd" />
        <YAxis tick={{ fill: "#9ca3af", fontSize: 11 }} tickFormatter={v => `${(v / 1000).toFixed(0)}k`} width={40} />
        <Tooltip content={<NLTooltip />} />
        <ReferenceLine y={0} stroke="#6b7280" strokeDasharray="4 2" />
        <Area type="monotone" dataKey="net" stroke="#f59e0b" strokeWidth={2} fill="url(#netliq)" dot={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}
