"use client";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from "recharts";
import { MacroRow } from "@/lib/api";
import { fmtMonth } from "@/lib/format";

interface TipProps { active?: boolean; payload?: { value: number }[]; label?: string }
function MTooltip({ active, payload, label }: TipProps) {
  if (!active || !payload?.length) return null;
  const v = payload[0].value;
  return (
    <div style={{ background: "var(--tip-bg)", border: "1px solid var(--border)", borderRadius: 8, padding: "8px 12px", fontSize: 12 }}>
      <div style={{ color: "var(--fg)", fontWeight: 600 }}>{label}</div>
      <div style={{ color: "var(--s-violet)", fontFamily: "monospace" }}>
        {v == null ? "—" : `$${(Number(v) / 1000).toFixed(2)}bn`} <span style={{ color: "var(--fg-mute)" }}>({v == null ? "—" : `$${Number(v).toLocaleString()}mn`})</span>
      </div>
    </div>
  );
}

export default function RemittanceChart({ data }: { data: MacroRow[] }) {
  const chartData = data.map(r => ({ month: fmtMonth(r.month), remittance: r.remittance_usd_mn }));
  if (chartData.length === 0) {
    return <div className="text-sm t-mute py-8 text-center">No remittance data yet.</div>;
  }
  return (
    <ResponsiveContainer width="100%" height={260}>
      <BarChart data={chartData} margin={{ top: 4, right: 12, bottom: 4, left: 4 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--grid)" />
        <XAxis dataKey="month" tick={{ fill: "var(--fg-mute)", fontSize: 10 }} interval="preserveStartEnd" />
        <YAxis tick={{ fill: "var(--fg-mute)", fontSize: 10 }} tickFormatter={v => `$${(v / 1000).toFixed(1)}bn`} width={46} />
        <Tooltip content={<MTooltip />} cursor={{ fill: "var(--grid)", opacity: 0.4 }} />
        <Bar dataKey="remittance" name="Remittance" fill="var(--s-violet)" fillOpacity={0.85} radius={[3, 3, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}
