"use client";
import {
  Bar, BarChart, CartesianGrid, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import type { AuctionRow } from "@/lib/api";
import { OMO_INSTRUMENTS } from "@/lib/terminal";

/**
 * Monthly auction volume stacked by product, with the accepted-weighted cut-off
 * on a second axis.
 *
 * Accepted amount is plotted, not notified: notified is BB's offer, and on a
 * devolved or under-subscribed auction the two differ materially. Plotting the
 * offer would show borrowing that did not happen.
 *
 * Planned months are included — they are real calendar entries — but the yield
 * line simply stops where results stop, rather than dropping to zero.
 */
const COLORS: Record<string, string> = {
  T_BOND: "var(--s-blue)", T_BILL: "var(--s-teal)",
  FRTB: "var(--s-violet)", OTHER: "var(--s-grey)",
};
const LABEL: Record<string, string> = {
  T_BOND: "T-Bond", T_BILL: "T-Bill", FRTB: "FRTB", OTHER: "Other",
};
void OMO_INSTRUMENTS;   // products here are security types, not OMO instruments

export default function AuctionVolumeChart({ auctions, products }: {
  auctions: AuctionRow[]; products: string[];
}) {
  const byMonth = new Map<string, Record<string, number>>();
  const yieldAcc = new Map<string, { w: number; y: number }>();
  for (const a of auctions) {
    if (!a.month) continue;
    const m = byMonth.get(a.month) ?? {};
    const amt = a.accepted_crore ?? 0;
    m[a.product] = (m[a.product] ?? 0) + amt;
    byMonth.set(a.month, m);
    if (a.cutoff_yield_pct != null && amt > 0) {
      const s = yieldAcc.get(a.month) ?? { w: 0, y: 0 };
      s.w += amt; s.y += amt * a.cutoff_yield_pct;
      yieldAcc.set(a.month, s);
    }
  }

  const rows = [...byMonth.keys()].sort().map((m) => {
    const y = yieldAcc.get(m);
    return {
      label: m,
      ...Object.fromEntries(products.map((p) => [p, byMonth.get(m)?.[p] ?? 0])),
      cutoff: y && y.w ? Number((y.y / y.w).toFixed(4)) : null,
    } as Record<string, string | number | null>;
  });

  const shown = products.filter((p) => rows.some((r) => Number(r[p]) !== 0));
  const fmt = (v: number) => (Math.abs(v) >= 1000 ? `${(v / 1000).toFixed(1)}k` : v.toFixed(0));

  if (!rows.length) {
    return <p style={{ margin: 0, fontSize: 12.5, color: "var(--fg-mute)" }}>
      No auctions in this window.
    </p>;
  }

  return (
    <ResponsiveContainer width="100%" height={300}>
      <BarChart data={rows} margin={{ top: 4, right: 8, left: 4, bottom: 4 }}>
        <CartesianGrid strokeDasharray="2 4" stroke="var(--border)" vertical={false} />
        <XAxis dataKey="label" tick={{ fontSize: 10, fill: "var(--fg-mute)" }}
               interval="preserveStartEnd" minTickGap={14} />
        <YAxis yAxisId="l" tickFormatter={fmt} width={46}
               tick={{ fontSize: 10, fill: "var(--fg-mute)" }} />
        <YAxis yAxisId="r" orientation="right" width={44} domain={["auto", "auto"]}
               tickFormatter={(v) => `${Number(v).toFixed(1)}%`}
               tick={{ fontSize: 10, fill: "var(--fg-mute)" }} />
        <Tooltip
          contentStyle={{ background: "var(--tip-bg)", border: "1px solid var(--tip-border)",
                          borderRadius: 8, fontSize: 11.5, color: "var(--fg)" }}
          formatter={(v, name) => name === "Cut-off"
            ? [`${Number(v).toFixed(4)}%`, "Cut-off (wtd)"]
            : [`${Number(v).toLocaleString()} cr`, String(name)]}
        />
        <Legend wrapperStyle={{ fontSize: 11 }} />
        {shown.map((p) => (
          <Bar key={p} yAxisId="l" dataKey={p} name={LABEL[p] ?? p}
               stackId="a" fill={COLORS[p] ?? "var(--s-grey)"} />
        ))}
        <Line yAxisId="r" type="monotone" dataKey="cutoff" name="Cut-off"
              stroke="var(--s-amber)" strokeWidth={1.6} dot={false} connectNulls={false} />
      </BarChart>
    </ResponsiveContainer>
  );
}
