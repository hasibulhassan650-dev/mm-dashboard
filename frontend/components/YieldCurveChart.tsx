"use client";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";
import { YieldRow } from "@/lib/api";

export default function YieldCurveChart({ data }: { data: YieldRow[] }) {
  const sorted = [...data].sort((a, b) => (a.tenor_years ?? 0) - (b.tenor_years ?? 0));
  return (
    <ResponsiveContainer width="100%" height={220}>
      <LineChart data={sorted} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--grid)" />
        <XAxis dataKey="tenor_label" tick={{ fill: "var(--fg-mute)", fontSize: 11 }} />
        <YAxis domain={["auto", "auto"]} tick={{ fill: "var(--fg-mute)", fontSize: 11 }} tickFormatter={v => `${v}%`} width={42} />
        <Tooltip
          contentStyle={{ backgroundColor: "var(--tip-bg)", border: "1px solid var(--border)", borderRadius: 8 }}
          labelStyle={{ color: "var(--fg)" }}
          formatter={(v) => [`${Number(v).toFixed(4)}%`, "Yield"]}
        />
        <Line type="monotone" dataKey="cutoff_yield_pct" stroke="var(--s-blue)" strokeWidth={2} dot={{ r: 4, fill: "var(--s-blue)" }} />
      </LineChart>
    </ResponsiveContainer>
  );
}
