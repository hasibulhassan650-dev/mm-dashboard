"use client";
import { useState } from "react";
import { ScatterChart, Scatter, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";
import { YieldRow, SecondaryYieldRow } from "@/lib/api";

const TYPE_COLOR: Record<string, string> = {
  T_BILL: "var(--s-teal)",
  T_BOND: "var(--s-blue)",
  FRTB:   "var(--s-orange)",
};

const CURVE_SERIES = [
  { key: "secondary", label: "Secondary (MTM)",    color: "var(--fg-dim)" },
  { key: "primary",   label: "Primary (Auction)",  color: "var(--fg)" },
];

interface DotProps { cx?: number; cy?: number; fill?: string }

function SecondaryDot({ cx, cy, fill }: DotProps) {
  if (cx === undefined || cy === undefined) return null;
  return <circle cx={cx} cy={cy} r={3} fill={fill} fillOpacity={0.65} stroke="none" />;
}

function PrimaryDiamond({ cx, cy, fill }: DotProps) {
  if (cx === undefined || cy === undefined) return null;
  const s = 6;
  return (
    <polygon
      points={`${cx},${cy - s} ${cx + s},${cy} ${cx},${cy + s} ${cx - s},${cy}`}
      fill={fill} stroke="white" strokeWidth={1}
    />
  );
}

interface TooltipPayloadItem {
  payload: { x: number; y: number; label: string; series: string };
}
interface TooltipProps { active?: boolean; payload?: TooltipPayloadItem[] }

function YieldTooltip({ active, payload }: TooltipProps) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  const isPrimary = p.series.endsWith("_pri");
  return (
    <div style={{ background: "var(--tip-bg)", border: "1px solid var(--border)", borderRadius: 8, padding: "8px 12px", fontSize: 12 }}>
      <div style={{ color: "var(--fg)", marginBottom: 4, maxWidth: 220 }}>{p.label}</div>
      <div style={{ color: "var(--fg-mute)", marginBottom: 2 }}>{isPrimary ? "Primary — BB Treasury" : "Secondary — GSOM MTM"}</div>
      <div style={{ color: "var(--fg)", fontFamily: "monospace" }}>
        {isPrimary ? `Tenor: ${p.x.toFixed(2)} yr` : `Rem. maturity: ${p.x.toFixed(2)} yr`}
      </div>
      <div style={{ color: "var(--fg)", fontFamily: "monospace" }}>Yield: {p.y.toFixed(4)}%</div>
    </div>
  );
}

function groupByType<T>(arr: T[], key: keyof T): Record<string, T[]> {
  const out: Record<string, T[]> = {};
  for (const item of arr) {
    const k = String(item[key]);
    if (!out[k]) out[k] = [];
    out[k].push(item);
  }
  return out;
}

interface Props { primary: YieldRow[]; secondary: SecondaryYieldRow[] }

export default function YieldCurveChartFull({ primary, secondary }: Props) {
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const toggle = (key: string) => setHidden(prev => {
    const next = new Set(prev); next.has(key) ? next.delete(key) : next.add(key); return next;
  });

  const types = [...new Set([
    ...primary.map(r => r.security_type),
    ...secondary.map(r => r.security_type),
  ])].sort();

  const priByType = groupByType(primary,   "security_type");
  const secByType = groupByType(secondary, "security_type");

  const showPrimary   = !hidden.has("primary");
  const showSecondary = !hidden.has("secondary");

  return (
    <div>
      <div className="flex flex-wrap items-center gap-3 mb-3">
        <div className="flex flex-wrap gap-1.5">
          {CURVE_SERIES.map(s => {
            const active = !hidden.has(s.key);
            return (
              <button key={s.key} onClick={() => toggle(s.key)}
                className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs border transition-all"
                style={{ background: active ? `color-mix(in oklab, ${s.color} 14%, transparent)` : "transparent", borderColor: active ? `color-mix(in oklab, ${s.color} 55%, transparent)` : "var(--border)", color: active ? s.color : "var(--fg-mute)" }}>
                <span className="inline-block" style={{ width: 14, height: 2, background: active ? s.color : "var(--fg-mute)", marginBottom: 1 }} />
                {s.label}
              </button>
            );
          })}
        </div>
        <div className="flex flex-wrap gap-3 text-xs t-mute ml-auto">
          {types.map(t => (
            <span key={t} className="flex items-center gap-1.5">
              <span style={{ background: TYPE_COLOR[t] ?? "var(--fg-mute)", width: 16, height: 2, display: "inline-block" }} />
              <span style={{ color: TYPE_COLOR[t] ?? "var(--fg-mute)" }}>{t}</span>
            </span>
          ))}
        </div>
      </div>

      {types.map(t => {
        const pri = [...(priByType[t] ?? [])].sort((a, b) => (a.tenor_years ?? 0) - (b.tenor_years ?? 0));
        const sec = [...(secByType[t] ?? [])].sort((a, b) => a.remaining_years - b.remaining_years);
        if (!pri.length && !sec.length) return null;
        const color = TYPE_COLOR[t] ?? "var(--fg-mute)";

        const priData = pri.map(r => ({ x: r.tenor_years ?? 0, y: r.cutoff_yield_pct, label: r.tenor_label, series: `${t}_pri` }));
        const secData = sec.map(r => ({ x: r.remaining_years, y: r.market_yield_pct, label: r.security_name_norm ?? r.isin, series: `${t}_sec` }));

        const visibleData = [
          ...(showPrimary   ? priData : []),
          ...(showSecondary ? secData : []),
        ];
        if (!visibleData.length) return null;

        const allX = visibleData.map(d => d.x);
        const allY = visibleData.map(d => d.y);
        const xMin = Math.max(0, Math.min(...allX) - 0.2);
        const xMax = Math.max(...allX) + 0.5;
        const yMin = Math.max(0, Math.min(...allY) - 0.5);
        const yMax = Math.max(...allY) + 0.5;

        return (
          <div key={t} className="mb-4">
            <div className="text-xs font-medium mb-1 ml-1" style={{ color }}>{t}</div>
            <ResponsiveContainer width="100%" height={180}>
              <ScatterChart margin={{ top: 4, right: 16, bottom: 8, left: 4 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--grid)" />
                <XAxis type="number" dataKey="x" name="Maturity (yr)" domain={[xMin, xMax]}
                  tick={{ fill: "var(--fg-mute)", fontSize: 10 }} tickFormatter={v => `${Number(v).toFixed(1)}yr`} />
                <YAxis type="number" dataKey="y" name="Yield (%)" domain={[yMin, yMax]}
                  tick={{ fill: "var(--fg-mute)", fontSize: 10 }} tickFormatter={v => `${Number(v).toFixed(1)}%`} width={42} />
                <Tooltip content={<YieldTooltip />} />
                {showSecondary && secData.length > 0 && (
                  <Scatter name={`${t} Secondary`} data={secData} fill={color}
                    shape={<SecondaryDot fill={color} />}
                    line={{ stroke: color, strokeWidth: 1.5, strokeOpacity: 0.4 }} />
                )}
                {showPrimary && priData.length > 0 && (
                  <Scatter name={`${t} Primary`} data={priData} fill={color}
                    shape={<PrimaryDiamond fill={color} />}
                    line={{ stroke: color, strokeWidth: 2, strokeDasharray: "6 3" }} />
                )}
              </ScatterChart>
            </ResponsiveContainer>
          </div>
        );
      })}
    </div>
  );
}
