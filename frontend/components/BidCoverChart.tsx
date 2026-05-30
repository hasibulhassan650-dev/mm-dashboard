"use client";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, ReferenceLine, Cell,
} from "recharts";
import { YieldRow } from "@/lib/api";
import { bidCoverByTenor } from "@/lib/analytics";

interface TipProps { active?: boolean; payload?: { payload: { tenor: string; ratio: number } }[] }
function BCTooltip({ active, payload }: TipProps) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  return (
    <div style={{ background: "#111827", border: "1px solid #374151", borderRadius: 8, padding: "8px 12px", fontSize: 12 }}>
      <div style={{ color: "#e5e7eb", fontWeight: 600 }}>{d.tenor}</div>
      <div style={{ color: "#9ca3af", fontFamily: "monospace" }}>{d.ratio.toFixed(2)}× cover</div>
      <div style={{ color: d.ratio < 1.1 ? "#f87171" : "#34d399" }}>
        {d.ratio < 1.1 ? "weak demand" : "well covered"}
      </div>
    </div>
  );
}

export default function BidCoverChart({ rows }: { rows: YieldRow[] }) {
  const data = bidCoverByTenor(rows);
  if (data.length === 0) {
    return <div className="text-sm text-gray-500 py-8 text-center">No bid/accept data available.</div>;
  }
  return (
    <ResponsiveContainer width="100%" height={240}>
      <BarChart data={data} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
        <XAxis dataKey="tenor" tick={{ fill: "#9ca3af", fontSize: 10 }} interval={0} angle={-30} textAnchor="end" height={48} />
        <YAxis tick={{ fill: "#9ca3af", fontSize: 11 }} tickFormatter={v => `${v}×`} width={36} />
        <Tooltip content={<BCTooltip />} cursor={{ fill: "#1f2937", opacity: 0.4 }} />
        <ReferenceLine y={1} stroke="#6b7280" strokeDasharray="4 2" />
        <Bar dataKey="ratio" radius={[3, 3, 0, 0]}>
          {data.map((d, i) => (
            <Cell key={i} fill={d.ratio < 1.1 ? "#dc2626" : "#0d9488"} fillOpacity={0.85} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
