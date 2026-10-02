"use client";
import { useState } from "react";
import {
  Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import type { ScheduleByProduct } from "@/lib/api";

/**
 * Redemptions and coupons stacked by product.
 *
 * Over a long horizon the monthly grain is unreadable — 240 bars — so anything
 * past three years is shown per fiscal year instead, which is also the grain a
 * funding plan is actually built on. Auctions are deliberately NOT plotted:
 * they exist only to the end of BB's published calendar, and a bar series that
 * simply stops would read as "borrowing falls to zero".
 */
const SERIES = [
  { key: "T_BOND", label: "T-Bond", color: "var(--s-blue)" },
  { key: "T_BILL", label: "T-Bill", color: "var(--s-teal)" },
  { key: "FRTB", label: "FRTB", color: "var(--s-violet)" },
  { key: "OTHER", label: "Other", color: "var(--s-grey)" },
] as const;

type Kind = "redemption" | "coupon";

export default function ScheduleChart({ data }: { data: ScheduleByProduct }) {
  const [kind, setKind] = useState<Kind>("redemption");
  const byFy = data.months.length > 36;

  type Row = { label: string } & Record<string, string | number>;
  const rows: Row[] = byFy
    ? data.fy_subtotals.map((f) => {
        const r: Row = { label: f.fiscal_year ?? "" };
        for (const s of SERIES) r[s.key] = f[kind][s.key];
        return r;
      })
    : data.months.map((m) => {
        const r: Row = { label: m.month };
        for (const s of SERIES) r[s.key] = m[kind][s.key];
        return r;
      });

  // Only plot a product that actually appears, so an empty legend entry never
  // implies a product we failed to load.
  const shown = SERIES.filter((s) => rows.some((r) => Number(r[s.key]) !== 0));
  const fmt = (v: number) =>
    Math.abs(v) >= 1000 ? `${(v / 1000).toFixed(1)}k` : v.toFixed(0);

  return (
    <>
      <div className="drc-presets" style={{ marginBottom: 10 }}>
        <button className={"seg-b" + (kind === "redemption" ? " on" : "")} onClick={() => setKind("redemption")}>
          Redemptions
        </button>
        <button className={"seg-b" + (kind === "coupon" ? " on" : "")} onClick={() => setKind("coupon")}>
          Coupons
        </button>
        <span className="panel-sub" style={{ marginLeft: 8 }}>
          BDT crore · {byFy ? "per fiscal year (Jul–Jun)" : "per month"}
        </span>
      </div>
      <ResponsiveContainer width="100%" height={300}>
        <BarChart data={rows} margin={{ top: 4, right: 8, left: 4, bottom: 4 }}>
          <CartesianGrid strokeDasharray="2 4" stroke="var(--border)" vertical={false} />
          <XAxis dataKey="label" tick={{ fontSize: 10, fill: "var(--fg-mute)" }}
                 interval="preserveStartEnd" minTickGap={14} />
          <YAxis tickFormatter={fmt} tick={{ fontSize: 10, fill: "var(--fg-mute)" }} width={46} />
          <Tooltip
            contentStyle={{ background: "var(--bg-panel)", border: "1px solid var(--border)",
                            borderRadius: 8, fontSize: 11.5, color: "var(--fg)" }}
            formatter={(v, name) => [`${Number(v).toLocaleString()} cr`, String(name)]}
          />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          {shown.map((s) => (
            <Bar key={s.key} dataKey={s.key} name={s.label} stackId="p" fill={s.color} />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </>
  );
}
