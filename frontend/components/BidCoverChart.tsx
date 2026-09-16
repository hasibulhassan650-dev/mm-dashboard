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
    <div style={{ background: "var(--tip-bg)", border: "1px solid var(--border)", borderRadius: 8, padding: "8px 12px", fontSize: 12 }}>
      <div style={{ color: "var(--fg)", fontWeight: 600 }}>{d.tenor}</div>
      <div style={{ color: "var(--fg-mute)", fontFamily: "monospace" }}>{d.ratio.toFixed(2)}× cover</div>
      <div style={{ color: d.ratio < 1.1 ? "var(--s-red)" : "var(--s-green)" }}>
        {d.ratio < 1.1 ? "weak demand" : "well covered"}
      </div>
    </div>
  );
}

export default function BidCoverChart({ rows }: { rows: YieldRow[] }) {
  const data = bidCoverByTenor(rows);
  if (data.length === 0) {
    return <div className="text-sm t-mute py-8 text-center">No bid/accept data available.</div>;
  }
  return (
    <ResponsiveContainer width="100%" height={240}>
      <BarChart data={data} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--grid)" />
        <XAxis dataKey="tenor" tick={{ fill: "var(--fg-mute)", fontSize: 10 }} interval={0} angle={-30} textAnchor="end" height={48} />
        <YAxis tick={{ fill: "var(--fg-mute)", fontSize: 11 }} tickFormatter={v => `${v}×`} width={36} />
        <Tooltip content={<BCTooltip />} cursor={{ fill: "var(--grid)", opacity: 0.4 }} />
        <ReferenceLine y={1} stroke="var(--fg-mute)" strokeDasharray="4 2" />
        <Bar dataKey="ratio" radius={[3, 3, 0, 0]}>
          {data.map((d, i) => (
            <Cell key={i} fill={d.ratio < 1.1 ? "var(--s-red)" : "var(--s-teal)"} fillOpacity={0.85} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
