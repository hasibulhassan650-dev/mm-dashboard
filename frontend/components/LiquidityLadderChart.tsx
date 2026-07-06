"use client";
import { useState } from "react";
import {
  ComposedChart, Bar, Line, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, ReferenceLine
} from "recharts";
import { LiquidityForecastDay } from "@/lib/api";
import { fmtDateShort, fmtCrore } from "@/lib/format";

// Palette validated (dataviz six-checks, dark surface): teal/blue = cash to
// banks, red/amber = cash out of banks, neutral line = cumulative.
const SERIES = [
  { key: "govt",      label: "Govt Inflow",     color: "#0d9488" },
  { key: "omoReturn", label: "OMO Return (SDF)", color: "#3b82f6" },
  { key: "auction",   label: "Auction Settle",  color: "#dc2626" },
  { key: "omoRepay",  label: "OMO Repayment",   color: "#d97706" },
  { key: "cum",       label: "Cumulative Net",  color: "#e5e7eb" },
];

export default function LiquidityLadderChart({ data }: { data: LiquidityForecastDay[] }) {
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const toggle = (key: string) => setHidden(prev => {
    const next = new Set(prev); next.has(key) ? next.delete(key) : next.add(key); return next;
  });

  const chartData = data.map(d => ({
    date:      fmtDateShort(d.date),
    govt:      d.govt_inflow_crore,
    omoReturn: d.omo_return_crore,
    auction:   -d.auction_out_crore,
    omoRepay:  -d.omo_repay_crore,
    cum:       d.cum_net_crore,
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
      <ResponsiveContainer width="100%" height={320}>
        <ComposedChart data={chartData} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
          <XAxis dataKey="date" tick={{ fill: "#9ca3af", fontSize: 10 }} interval="preserveStartEnd" />
          <YAxis tick={{ fill: "#9ca3af", fontSize: 11 }} tickFormatter={v => `${(v / 1000).toFixed(0)}k`} width={48} />
          <Tooltip
            contentStyle={{ backgroundColor: "#111827", border: "1px solid #374151", borderRadius: 8 }}
            labelStyle={{ color: "#e5e7eb", fontSize: 11 }}
            formatter={(v, name) => [fmtCrore(Math.abs(Number(v))), String(name)]}
          />
          <ReferenceLine y={0} stroke="#374151" />
          <Bar dataKey="govt"      name="Govt Inflow (coupon+maturity)" stackId="a" fill="#0d9488" fillOpacity={0.8} hide={hidden.has("govt")} />
          <Bar dataKey="omoReturn" name="OMO Return (SDF maturing)"     stackId="a" fill="#3b82f6" fillOpacity={0.8} hide={hidden.has("omoReturn")} />
          <Bar dataKey="auction"   name="Auction Settlement"            stackId="a" fill="#dc2626" fillOpacity={0.75} hide={hidden.has("auction")} />
          <Bar dataKey="omoRepay"  name="OMO Repayment to BB"           stackId="a" fill="#d97706" fillOpacity={0.75} hide={hidden.has("omoRepay")} />
          <Line type="monotone" dataKey="cum" name="Cumulative Net"
            stroke="#e5e7eb" strokeWidth={2} dot={false} hide={hidden.has("cum")} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
