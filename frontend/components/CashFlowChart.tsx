"use client";
import { useState } from "react";
import {
  ComposedChart, Bar, Line, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, ReferenceLine
} from "recharts";
import { FlowRow } from "@/lib/api";
import { fmtDateShort, fmtBDTmn } from "@/lib/format";

const SERIES = [
  { key: "maturity", label: "Maturity",   color: "var(--s-blue)" },
  { key: "coupon",   label: "Coupon",     color: "var(--s-teal)" },
  { key: "outflow",  label: "Outflow",    color: "var(--s-red)" },
  { key: "net",      label: "Net Borrow", color: "var(--s-amber)" },
];

export default function CashFlowChart({ data }: { data: FlowRow[] }) {
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const toggle = (key: string) => setHidden(prev => {
    const next = new Set(prev); next.has(key) ? next.delete(key) : next.add(key); return next;
  });

  const chartData = data.map(r => ({
    date:     fmtDateShort(r.flow_date),
    maturity: r.principal_inflow_bdt_mill,
    coupon:   r.coupon_inflow_bdt_mill,
    outflow:  -(r.auction_outflow_confirmed_mill || r.auction_outflow_planned_mill),
    net:      r.net_borrowing_bdt_mill,
  }));

  return (
    <div>
      <div className="flex flex-wrap gap-1.5 mb-3">
        {SERIES.map(s => {
          const active = !hidden.has(s.key);
          return (
            <button key={s.key} onClick={() => toggle(s.key)}
              className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs border transition-all"
              style={{ background: active ? `color-mix(in oklab, ${s.color} 14%, transparent)` : "transparent", borderColor: active ? `color-mix(in oklab, ${s.color} 55%, transparent)` : "var(--border)", color: active ? s.color : "var(--fg-mute)" }}>
              <span className="w-2 h-2 rounded-full inline-block" style={{ background: active ? s.color : "var(--fg-mute)" }} />
              {s.label}
            </button>
          );
        })}
      </div>
      <ResponsiveContainer width="100%" height={300}>
        <ComposedChart data={chartData} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--grid)" />
          <XAxis dataKey="date" tick={{ fill: "var(--fg-mute)", fontSize: 10 }} interval="preserveStartEnd" />
          <YAxis yAxisId="left" tick={{ fill: "var(--fg-mute)", fontSize: 11 }} tickFormatter={v => `${(v/1000).toFixed(0)}k`} width={44} />
          <YAxis yAxisId="right" orientation="right" tick={{ fill: "var(--s-orange)", fontSize: 11 }} tickFormatter={v => `${(v/1000).toFixed(0)}k`} width={44} />
          <Tooltip
            contentStyle={{ backgroundColor: "var(--tip-bg)", border: "1px solid var(--border)", borderRadius: 8 }}
            labelStyle={{ color: "var(--fg)", fontSize: 11 }}
            formatter={(v, name) => [fmtBDTmn(Number(v)), String(name)]}
          />
          <ReferenceLine yAxisId="left" y={0} stroke="var(--border)" />
          <Bar yAxisId="left" dataKey="maturity" name="Maturity Inflow" stackId="a" fill="var(--s-blue)" fillOpacity={0.8} hide={hidden.has("maturity")} />
          <Bar yAxisId="left" dataKey="coupon"   name="Coupon Inflow"   stackId="a" fill="var(--s-teal)" fillOpacity={0.8} hide={hidden.has("coupon")} />
          <Bar yAxisId="left" dataKey="outflow"  name="Auction Outflow" stackId="a" fill="var(--s-red)" fillOpacity={0.7} hide={hidden.has("outflow")} />
          <Line yAxisId="right" type="monotone" dataKey="net" name="Net Borrowing"
            stroke="var(--s-amber)" strokeWidth={2} dot={false} hide={hidden.has("net")} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
