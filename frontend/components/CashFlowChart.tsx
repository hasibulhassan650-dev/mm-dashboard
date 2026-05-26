"use client";
import {
  ComposedChart, Bar, Line, XAxis, YAxis, Tooltip,
  ResponsiveContainer, Legend, CartesianGrid, ReferenceLine
} from "recharts";
import { FlowRow } from "@/lib/api";

export default function CashFlowChart({ data }: { data: FlowRow[] }) {
  const chartData = data.map(r => ({
    date: r.flow_date.slice(5),
    maturity: r.principal_inflow_bdt_mill,
    coupon:   r.coupon_inflow_bdt_mill,
    outflow:  -(r.auction_outflow_confirmed_mill || r.auction_outflow_planned_mill),
    net:      r.net_borrowing_bdt_mill,
  }));

  return (
    <ResponsiveContainer width="100%" height={300}>
      <ComposedChart data={chartData} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
        <XAxis dataKey="date" tick={{ fill: "#9ca3af", fontSize: 10 }} interval="preserveStartEnd" />
        <YAxis yAxisId="left" tick={{ fill: "#9ca3af", fontSize: 11 }}
          tickFormatter={v => `${(v/1000).toFixed(0)}k`} width={44} />
        <YAxis yAxisId="right" orientation="right" tick={{ fill: "#d1780f", fontSize: 11 }}
          tickFormatter={v => `${(v/1000).toFixed(0)}k`} width={44} />
        <Tooltip
          contentStyle={{ backgroundColor: "#111827", border: "1px solid #374151", borderRadius: 8 }}
          labelStyle={{ color: "#e5e7eb", fontSize: 11 }}
          formatter={(v, name) => [`${Number(v).toLocaleString()} mn`, String(name)]}
        />
        <Legend wrapperStyle={{ fontSize: 11, color: "#9ca3af" }} />
        <ReferenceLine yAxisId="left" y={0} stroke="#374151" />
        <Bar yAxisId="left" dataKey="maturity" name="Maturity Inflow" stackId="a" fill="#1d4ed8" fillOpacity={0.8} />
        <Bar yAxisId="left" dataKey="coupon"   name="Coupon Inflow"   stackId="a" fill="#0d9488" fillOpacity={0.8} />
        <Bar yAxisId="left" dataKey="outflow"  name="Auction Outflow" stackId="a" fill="#dc2626" fillOpacity={0.7} />
        <Line yAxisId="right" type="monotone" dataKey="net" name="Net Borrowing"
          stroke="#f59e0b" strokeWidth={2} dot={false} />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
