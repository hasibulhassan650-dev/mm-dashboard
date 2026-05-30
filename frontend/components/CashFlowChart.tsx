"use client";
import { useState } from "react";
import {
  ComposedChart, Bar, Line, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, ReferenceLine
} from "recharts";
import { FlowRow } from "@/lib/api";
import { fmtDateShort, fmtBDTmn } from "@/lib/format";

const SERIES = [
  { key: "maturity", label: "Maturity",   color: "#1d4ed8" },
  { key: "coupon",   label: "Coupon",     color: "#0d9488" },
  { key: "outflow",  label: "Outflow",    color: "#dc2626" },
  { key: "net",      label: "Net Borrow", color: "#f59e0b" },
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
              style={{ background: active ? s.color + "22" : "transparent", borderColor: active ? s.color + "88" : "#374151", color: active ? s.color : "#4b5563" }}>
              <span className="w-2 h-2 rounded-full inline-block" style={{ background: active ? s.color : "#4b5563" }} />
              {s.label}
            </button>
          );
        })}
      </div>
      <ResponsiveContainer width="100%" height={300}>
        <ComposedChart data={chartData} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
          <XAxis dataKey="date" tick={{ fill: "#9ca3af", fontSize: 10 }} interval="preserveStartEnd" />
          <YAxis yAxisId="left" tick={{ fill: "#9ca3af", fontSize: 11 }} tickFormatter={v => `${(v/1000).toFixed(0)}k`} width={44} />
          <YAxis yAxisId="right" orientation="right" tick={{ fill: "#d1780f", fontSize: 11 }} tickFormatter={v => `${(v/1000).toFixed(0)}k`} width={44} />
          <Tooltip
            contentStyle={{ backgroundColor: "#111827", border: "1px solid #374151", borderRadius: 8 }}
            labelStyle={{ color: "#e5e7eb", fontSize: 11 }}
            formatter={(v, name) => [fmtBDTmn(Number(v)), String(name)]}
          />
          <ReferenceLine yAxisId="left" y={0} stroke="#374151" />
          <Bar yAxisId="left" dataKey="maturity" name="Maturity Inflow" stackId="a" fill="#1d4ed8" fillOpacity={0.8} hide={hidden.has("maturity")} />
          <Bar yAxisId="left" dataKey="coupon"   name="Coupon Inflow"   stackId="a" fill="#0d9488" fillOpacity={0.8} hide={hidden.has("coupon")} />
          <Bar yAxisId="left" dataKey="outflow"  name="Auction Outflow" stackId="a" fill="#dc2626" fillOpacity={0.7} hide={hidden.has("outflow")} />
          <Line yAxisId="right" type="monotone" dataKey="net" name="Net Borrowing"
            stroke="#f59e0b" strokeWidth={2} dot={false} hide={hidden.has("net")} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
