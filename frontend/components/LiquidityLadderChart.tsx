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
// Cash INTO banks is teal/blue/green; cash OUT is red/orange/maroon. Both OMO
// legs appear: a tranche injects or absorbs on its deal date and reverses at
// maturity, and showing only the maturity half made the ladder wrong.
const SERIES = [
  { key: "govt",      label: "Govt Inflow",      color: "var(--s-teal)" },
  { key: "omoNew",    label: "OMO Dealt (new injection)", color: "var(--s-green)" },
  { key: "omoReturn", label: "OMO Maturing (SDF returns)", color: "var(--s-blue)" },
  { key: "auction",   label: "Auction Settle",   color: "var(--s-red)" },
  { key: "omoRepay",  label: "OMO Maturing (banks repay)", color: "var(--s-orange)" },
  { key: "omoAbsorb", label: "OMO Dealt (new SDF)", color: "var(--s-maroon)" },
  { key: "cum",       label: "Cumulative Net",   color: "var(--fg)" },
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
    omoNew:    d.omo_new_inflow_crore ?? 0,
    auction:   -d.auction_out_crore,
    omoRepay:  -d.omo_repay_crore,
    omoAbsorb: -(d.omo_new_outflow_crore ?? 0),
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
              style={{ background: active ? `color-mix(in oklab, ${s.color} 14%, transparent)` : "transparent", borderColor: active ? `color-mix(in oklab, ${s.color} 55%, transparent)` : "var(--border)", color: active ? s.color : "var(--fg-mute)" }}>
              <span className="w-2 h-2 rounded-full inline-block" style={{ background: active ? s.color : "var(--fg-mute)" }} />
              {s.label}
            </button>
          );
        })}
      </div>
      <ResponsiveContainer width="100%" height={320}>
        <ComposedChart data={chartData} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--grid)" />
          <XAxis dataKey="date" tick={{ fill: "var(--fg-mute)", fontSize: 10 }} interval="preserveStartEnd" />
          <YAxis tick={{ fill: "var(--fg-mute)", fontSize: 11 }} tickFormatter={v => `${(v / 1000).toFixed(0)}k`} width={48} />
          <Tooltip
            contentStyle={{ backgroundColor: "var(--tip-bg)", border: "1px solid var(--border)", borderRadius: 8 }}
            labelStyle={{ color: "var(--fg)", fontSize: 11 }}
            formatter={(v, name) => [fmtCrore(Math.abs(Number(v))), String(name)]}
          />
          <ReferenceLine y={0} stroke="var(--border)" />
          <Bar dataKey="govt"      name="Govt Inflow (coupon+maturity)" stackId="a" fill="var(--s-teal)" fillOpacity={0.8} hide={hidden.has("govt")} />
          <Bar dataKey="omoReturn" name="OMO Return (SDF maturing)"     stackId="a" fill="var(--s-blue)" fillOpacity={0.8} hide={hidden.has("omoReturn")} />
          <Bar dataKey="auction"   name="Auction Settlement"            stackId="a" fill="var(--s-red)" fillOpacity={0.75} hide={hidden.has("auction")} />
          <Bar dataKey="omoRepay"  name="OMO Repayment to BB"           stackId="a" fill="var(--s-orange)" fillOpacity={0.75} hide={hidden.has("omoRepay")} />
          <Line type="monotone" dataKey="cum" name="Cumulative Net"
            stroke="var(--fg)" strokeWidth={2} dot={false} hide={hidden.has("cum")} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
