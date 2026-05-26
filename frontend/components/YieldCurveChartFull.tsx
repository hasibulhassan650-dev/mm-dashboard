"use client";
import {
  ScatterChart, Scatter, Line, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, Legend, ComposedChart,
  ReferenceLine,
} from "recharts";
import { YieldRow, SecondaryYieldRow } from "@/lib/api";

const TYPE_COLOR: Record<string, string> = {
  T_BILL: "#0d9488",
  T_BOND: "#1f6feb",
  FRTB:   "#d1780f",
};

interface Props {
  primary: YieldRow[];
  secondary: SecondaryYieldRow[];
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

export default function YieldCurveChartFull({ primary, secondary }: Props) {
  const types = [...new Set([
    ...primary.map(r => r.security_type),
    ...secondary.map(r => r.security_type),
  ])].sort();

  const priByType  = groupByType(primary,   "security_type");
  const secByType  = groupByType(secondary, "security_type");

  // Build unified dataset: each point has x = tenor/remaining years, y = yield, series key
  // Recharts ComposedChart: one Line per primary series, one Scatter per secondary series
  const priSeries = types.flatMap(t =>
    (priByType[t] ?? [])
      .sort((a, b) => (a.tenor_years ?? 0) - (b.tenor_years ?? 0))
      .map(r => ({ x: r.tenor_years ?? 0, y: r.cutoff_yield_pct, label: r.tenor_label, series: `${t}_pri` }))
  );

  const secSeries = types.flatMap(t =>
    (secByType[t] ?? [])
      .sort((a, b) => a.remaining_years - b.remaining_years)
      .map(r => ({ x: r.remaining_years, y: r.market_yield_pct, label: r.security_name_norm ?? r.isin, series: `${t}_sec` }))
  );

  const CustomDot = (props: { cx?: number; cy?: number; fill?: string }) => {
    const { cx, cy, fill } = props;
    if (cx === undefined || cy === undefined) return null;
    return <circle cx={cx} cy={cy} r={3} fill={fill} fillOpacity={0.7} stroke="none" />;
  };

  const PrimaryDiamond = (props: { cx?: number; cy?: number; fill?: string }) => {
    const { cx, cy, fill } = props;
    if (cx === undefined || cy === undefined) return null;
    const s = 6;
    return (
      <polygon
        points={`${cx},${cy-s} ${cx+s},${cy} ${cx},${cy+s} ${cx-s},${cy}`}
        fill={fill}
        stroke="white"
        strokeWidth={1}
      />
    );
  };

  const CustomTooltip = ({ active, payload }: { active?: boolean; payload?: { name: string; payload: { x: number; y: number; label: string; series: string } }[] }) => {
    if (!active || !payload?.length) return null;
    const p = payload[0].payload;
    const isPrimary = p.series.endsWith("_pri");
    return (
      <div style={{ background: "#111827", border: "1px solid #374151", borderRadius: 8, padding: "8px 12px", fontSize: 12 }}>
        <div style={{ color: "#e5e7eb", marginBottom: 4 }}>{p.label}</div>
        <div style={{ color: "#9ca3af" }}>{isPrimary ? "Primary (BB Treasury)" : "Secondary (GSOM)"}</div>
        <div style={{ color: "#fff", fontFamily: "monospace" }}>
          {isPrimary ? `Tenor: ${p.x.toFixed(2)} yr` : `Rem. maturity: ${p.x.toFixed(2)} yr`}
        </div>
        <div style={{ color: "#fff", fontFamily: "monospace" }}>Yield: {p.y.toFixed(4)}%</div>
      </div>
    );
  };

  return (
    <div>
      {/* Legend */}
      <div className="flex flex-wrap gap-4 mb-3 text-xs text-gray-400">
        {types.map(t => (
          <span key={t} className="flex items-center gap-1.5">
            <span style={{ background: TYPE_COLOR[t] ?? "#888", width: 24, height: 2, display: "inline-block", verticalAlign: "middle" }} />
            <span className="border-b border-dashed border-current" style={{ color: TYPE_COLOR[t] ?? "#888" }}>
              {t} primary
            </span>
            <span style={{ color: TYPE_COLOR[t] ?? "#888" }}>· {t} secondary</span>
          </span>
        ))}
        <span className="ml-auto text-gray-500">◆ primary (auction)  ● secondary (MTM)</span>
      </div>

      {/* One scatter chart per type group */}
      {types.map(t => {
        const pri = (priByType[t] ?? []).sort((a, b) => (a.tenor_years ?? 0) - (b.tenor_years ?? 0));
        const sec = (secByType[t] ?? []).sort((a, b) => a.remaining_years - b.remaining_years);
        if (!pri.length && !sec.length) return null;
        const color = TYPE_COLOR[t] ?? "#888";

        const priData = pri.map(r => ({ x: r.tenor_years ?? 0, y: r.cutoff_yield_pct, label: r.tenor_label, series: `${t}_pri` }));
        const secData = sec.map(r => ({ x: r.remaining_years, y: r.market_yield_pct, label: r.security_name_norm ?? r.isin, series: `${t}_sec` }));

        const allX = [...priData.map(d => d.x), ...secData.map(d => d.x)];
        const allY = [...priData.map(d => d.y), ...secData.map(d => d.y)];
        const xMin = Math.max(0, Math.min(...allX) - 0.2);
        const xMax = Math.max(...allX) + 0.5;
        const yMin = Math.max(0, Math.min(...allY) - 0.5);
        const yMax = Math.max(...allY) + 0.5;

        return (
          <div key={t} className="mb-2">
            <div className="text-xs text-gray-500 mb-1 ml-1">{t}</div>
            <ResponsiveContainer width="100%" height={180}>
              <ScatterChart margin={{ top: 4, right: 16, bottom: 8, left: 4 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                <XAxis
                  type="number" dataKey="x" name="Maturity (yr)"
                  domain={[xMin, xMax]}
                  tick={{ fill: "#9ca3af", fontSize: 10 }}
                  tickFormatter={v => `${Number(v).toFixed(1)}yr`}
                />
                <YAxis
                  type="number" dataKey="y" name="Yield (%)"
                  domain={[yMin, yMax]}
                  tick={{ fill: "#9ca3af", fontSize: 10 }}
                  tickFormatter={v => `${Number(v).toFixed(1)}%`}
                  width={42}
                />
                <Tooltip content={<CustomTooltip />} />
                {sec.length > 0 && (
                  <Scatter
                    name={`${t} Secondary`} data={secData}
                    fill={color} fillOpacity={0.6}
                    shape={<CustomDot fill={color} />}
                    line={{ stroke: color, strokeWidth: 1.5, strokeOpacity: 0.5 }}
                  />
                )}
                {pri.length > 0 && (
                  <Scatter
                    name={`${t} Primary`} data={priData}
                    fill={color}
                    shape={<PrimaryDiamond fill={color} />}
                    line={{ stroke: color, strokeWidth: 2, strokeDasharray: "5 3" }}
                  />
                )}
              </ScatterChart>
            </ResponsiveContainer>
          </div>
        );
      })}
    </div>
  );
}
