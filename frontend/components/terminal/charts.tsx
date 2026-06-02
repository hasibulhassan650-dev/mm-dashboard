"use client";
import * as React from "react";

// measure container width for crisp responsive SVG
export function useSize(): [React.RefObject<HTMLDivElement | null>, number] {
  const ref = React.useRef<HTMLDivElement | null>(null);
  const [w, setW] = React.useState(640);
  React.useLayoutEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) setW(Math.max(120, Math.round(e.contentRect.width)));
    });
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

export function niceTicks(min: number, max: number, count: number): number[] {
  const span = max - min || 1;
  const step0 = span / count;
  const mag = Math.pow(10, Math.floor(Math.log10(step0)));
  const norm = step0 / mag;
  let step;
  if (norm < 1.5) step = 1; else if (norm < 3) step = 2; else if (norm < 7) step = 5; else step = 10;
  step *= mag;
  const lo = Math.floor(min / step) * step;
  const hi = Math.ceil(max / step) * step;
  const out: number[] = [];
  for (let v = lo; v <= hi + step / 2; v += step) out.push(+v.toFixed(6));
  return out;
}

type Pt = [number, number];

export function smoothPath(pts: Pt[]): string {
  if (pts.length < 2) return "";
  let d = `M ${pts[0][0]},${pts[0][1]}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i === 0 ? 0 : i - 1];
    const p1 = pts[i];
    const p2 = pts[i + 1];
    const p3 = pts[i + 2] || p2;
    const t = 0.18;
    const c1x = p1[0] + (p2[0] - p0[0]) * t;
    const c1y = p1[1] + (p2[1] - p0[1]) * t;
    const c2x = p2[0] - (p3[0] - p1[0]) * t;
    const c2y = p2[1] - (p3[1] - p1[1]) * t;
    d += ` C ${c1x},${c1y} ${c2x},${c2y} ${p2[0]},${p2[1]}`;
  }
  return d;
}

// ---------------- Sparkline ----------------
export function Sparkline({ data, color = "var(--accent)", h = 36, fill = true }: { data: number[]; color?: string; h?: number; fill?: boolean }) {
  const [ref, w] = useSize();
  const gid = React.useId();
  if (!data.length) return <div ref={ref} style={{ width: "100%", height: h }} />;
  const min = Math.min(...data), max = Math.max(...data);
  const pad = 2;
  const xs = (i: number) => pad + (data.length === 1 ? 0 : (i / (data.length - 1)) * (w - pad * 2));
  const ys = (v: number) => h - pad - ((v - min) / (max - min || 1)) * (h - pad * 2);
  const pts: Pt[] = data.map((v, i) => [xs(i), ys(v)] as Pt);
  const line = smoothPath(pts);
  const area = line + ` L ${xs(data.length - 1)},${h} L ${xs(0)},${h} Z`;
  return (
    <div ref={ref} style={{ width: "100%", height: h }}>
      <svg width={w} height={h} style={{ display: "block" }}>
        <defs>
          <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity="0.28" />
            <stop offset="100%" stopColor={color} stopOpacity="0" />
          </linearGradient>
        </defs>
        {fill && <path d={area} fill={`url(#${gid})`} />}
        <path d={line} fill="none" stroke={color} strokeWidth="1.6" strokeLinecap="round" />
        {pts.length > 0 && <circle cx={pts[pts.length - 1][0]} cy={pts[pts.length - 1][1]} r="2.4" fill={color} />}
      </svg>
    </div>
  );
}

// ---------------- Multi-series line chart ----------------
export interface LineSeries { name: string; data: number[]; color: string; dashed?: boolean }
export function LineChart({ labels, series, height = 300, yUnit = "%", showGrid = true, onPointClick }: { labels: string[]; series: LineSeries[]; height?: number; yUnit?: string; showGrid?: boolean; onPointClick?: (i: number) => void }) {
  const [ref, w] = useSize();
  const [hover, setHover] = React.useState<number | null>(null);
  const m = { t: 16, r: 16, b: 30, l: 46 };
  const iw = Math.max(40, w - m.l - m.r);
  const ih = height - m.t - m.b;
  const all = series.flatMap((s) => s.data);
  if (!all.length || !labels.length) return <div ref={ref} style={{ width: "100%", height }} />;
  const dmin = Math.min(...all), dmax = Math.max(...all);
  const ticks = niceTicks(dmin - (dmax - dmin) * 0.08, dmax + (dmax - dmin) * 0.08, 5);
  const ymin = ticks[0], ymax = ticks[ticks.length - 1];
  const xs = (i: number) => m.l + (labels.length === 1 ? iw / 2 : (i / (labels.length - 1)) * iw);
  const ys = (v: number) => m.t + ih - ((v - ymin) / (ymax - ymin || 1)) * ih;

  function idxAt(e: React.MouseEvent<SVGSVGElement>) {
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left;
    let idx = Math.round(((x - m.l) / iw) * (labels.length - 1));
    return Math.max(0, Math.min(labels.length - 1, idx));
  }
  return (
    <div ref={ref} style={{ width: "100%", height }}>
      <svg width={w} height={height} style={{ display: "block", cursor: onPointClick ? "pointer" : "default" }}
        onMouseMove={(e) => setHover(idxAt(e))} onMouseLeave={() => setHover(null)}
        onClick={onPointClick ? (e) => onPointClick(idxAt(e)) : undefined}>
        {showGrid && ticks.map((tk, i) => (
          <g key={i}>
            <line x1={m.l} x2={m.l + iw} y1={ys(tk)} y2={ys(tk)} stroke="var(--grid)" strokeWidth="1" strokeDasharray="2 4" />
            <text x={m.l - 8} y={ys(tk) + 3.5} textAnchor="end" className="chart-axis">{tk.toFixed(1)}{yUnit}</text>
          </g>
        ))}
        {labels.map((lb, i) => (
          <text key={i} x={xs(i)} y={height - 10} textAnchor="middle" className="chart-axis">{lb}</text>
        ))}
        {hover != null && <line x1={xs(hover)} x2={xs(hover)} y1={m.t} y2={m.t + ih} stroke="var(--crosshair)" strokeWidth="1" />}
        {series.map((s, si) => {
          const pts: Pt[] = s.data.map((v, i) => [xs(i), ys(v)] as Pt);
          return (
            <g key={si}>
              <path d={smoothPath(pts)} fill="none" stroke={s.color} strokeWidth={s.dashed ? 1.5 : 2.4} strokeDasharray={s.dashed ? "5 4" : undefined} strokeLinecap="round" opacity={s.dashed ? 0.65 : 1} />
              {!s.dashed && pts.map((p, i) => (
                <circle key={i} cx={p[0]} cy={p[1]} r={hover === i ? 4 : 2.6} fill="var(--panel)" stroke={s.color} strokeWidth="2" />
              ))}
            </g>
          );
        })}
        {hover != null && (() => {
          const lines = series.map((s) => ({ color: s.color, name: s.name, v: s.data[hover] }));
          const bw = 124, bh = 18 + lines.length * 16;
          let bx = xs(hover) + 12;
          if (bx + bw > w) bx = xs(hover) - bw - 12;
          const by = m.t + 6;
          return (
            <g pointerEvents="none">
              <rect x={bx} y={by} width={bw} height={bh} rx="6" className="chart-tip-bg" />
              <text x={bx + 10} y={by + 14} className="chart-tip-title">{labels[hover]}</text>
              {lines.map((l, i) => (
                <g key={i}>
                  <circle cx={bx + 12} cy={by + 30 + i * 16} r="3.5" fill={l.color} />
                  <text x={bx + 22} y={by + 33 + i * 16} className="chart-tip-label">{l.name}</text>
                  <text x={bx + bw - 10} y={by + 33 + i * 16} textAnchor="end" className="chart-tip-val">{l.v.toFixed(2)}{yUnit}</text>
                </g>
              ))}
            </g>
          );
        })()}
      </svg>
    </div>
  );
}

// ---------------- Stacked area chart ----------------
export interface StackCat { key: string; label: string; color: string; desc?: string }
export function StackedArea({ data, cats, height = 320, active, onPointClick }: { data: Record<string, number | string>[]; cats: StackCat[]; height?: number; active: Record<string, boolean>; onPointClick?: (i: number) => void }) {
  const [ref, w] = useSize();
  const [hover, setHover] = React.useState<number | null>(null);
  const m = { t: 14, r: 14, b: 28, l: 46 };
  const iw = Math.max(40, w - m.l - m.r);
  const ih = height - m.t - m.b;
  const shown = cats.filter((c) => active[c.key]);
  if (!data.length) return <div ref={ref} style={{ width: "100%", height }} />;
  const totals = data.map((d) => shown.reduce((s, c) => s + (Number(d[c.key]) || 0), 0));
  const dmax = Math.max(1, ...totals);
  const ticks = niceTicks(0, dmax * 1.05, 4);
  const ymax = ticks[ticks.length - 1];
  const xs = (i: number) => m.l + (data.length === 1 ? iw / 2 : (i / (data.length - 1)) * iw);
  const ys = (v: number) => m.t + ih - (v / ymax) * ih;

  const bands: { c: StackCat; d: string; line: string }[] = [];
  let base = data.map(() => 0);
  for (const c of shown) {
    const top = base.map((b, i) => b + (Number(data[i][c.key]) || 0));
    const upper: Pt[] = top.map((v, i) => [xs(i), ys(v)] as Pt);
    const lower: Pt[] = base.map((v, i) => [xs(i), ys(v)] as Pt).reverse();
    bands.push({ c, d: smoothPath(upper) + " L " + lower.map((p) => `${p[0]},${p[1]}`).join(" L ") + " Z", line: smoothPath(upper) });
    base = top;
  }

  function idxAt(e: React.MouseEvent<SVGSVGElement>) {
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left;
    let idx = Math.round(((x - m.l) / iw) * (data.length - 1));
    return Math.max(0, Math.min(data.length - 1, idx));
  }
  return (
    <div ref={ref} style={{ width: "100%", height }}>
      <svg width={w} height={height} style={{ display: "block", cursor: onPointClick ? "pointer" : "default" }}
        onMouseMove={(e) => setHover(idxAt(e))} onMouseLeave={() => setHover(null)}
        onClick={onPointClick ? (e) => onPointClick(idxAt(e)) : undefined}>
        {ticks.map((tk, i) => (
          <g key={i}>
            <line x1={m.l} x2={m.l + iw} y1={ys(tk)} y2={ys(tk)} stroke="var(--grid)" strokeWidth="1" strokeDasharray="2 4" />
            <text x={m.l - 8} y={ys(tk) + 3.5} textAnchor="end" className="chart-axis">{tk >= 1000 ? (tk / 1000) + "k" : tk}</text>
          </g>
        ))}
        {data.map((d, i) => i % 10 === 0 && (
          <text key={i} x={xs(i)} y={height - 9} textAnchor="middle" className="chart-axis">{String(d.label)}</text>
        ))}
        {bands.map((b, i) => <path key={i} d={b.d} fill={b.c.color} fillOpacity="0.42" stroke="none" />)}
        {bands.map((b, i) => <path key={"l" + i} d={b.line} fill="none" stroke={b.c.color} strokeWidth="1.4" opacity="0.9" />)}
        {hover != null && <line x1={xs(hover)} x2={xs(hover)} y1={m.t} y2={m.t + ih} stroke="var(--crosshair)" strokeWidth="1" />}
        {hover != null && (() => {
          const rows = shown.map((c) => ({ c, v: Number(data[hover][c.key]) || 0 }));
          const tot = rows.reduce((s, r) => s + r.v, 0);
          const bw = 150, bh = 22 + (rows.length + 1) * 16;
          let bx = xs(hover) + 12; if (bx + bw > w) bx = xs(hover) - bw - 12;
          const by = m.t + 4;
          return (
            <g pointerEvents="none">
              <rect x={bx} y={by} width={bw} height={bh} rx="6" className="chart-tip-bg" />
              <text x={bx + 10} y={by + 15} className="chart-tip-title">{String(data[hover].label)}</text>
              {rows.map((r, i) => (
                <g key={i}>
                  <rect x={bx + 10} y={by + 25 + i * 16} width="8" height="8" rx="2" fill={r.c.color} />
                  <text x={bx + 24} y={by + 32 + i * 16} className="chart-tip-label">{r.c.label}</text>
                  <text x={bx + bw - 10} y={by + 32 + i * 16} textAnchor="end" className="chart-tip-val">{r.v.toFixed(1)}</text>
                </g>
              ))}
              <line x1={bx + 10} x2={bx + bw - 10} y1={by + 25 + rows.length * 16 + 1} y2={by + 25 + rows.length * 16 + 1} stroke="var(--border)" />
              <text x={bx + 24} y={by + 32 + rows.length * 16 + 3} className="chart-tip-label" style={{ fontWeight: 600 }}>Total</text>
              <text x={bx + bw - 10} y={by + 32 + rows.length * 16 + 3} textAnchor="end" className="chart-tip-val" style={{ fontWeight: 600 }}>{tot.toFixed(1)}</text>
            </g>
          );
        })()}
      </svg>
    </div>
  );
}

// ---------------- Combo: bars (volume) + line (rate) ----------------
export interface ComboRow { label: string; vol: number; war: number; hi: number; lo: number }
export function ComboChart({ data, height = 300, onPointClick }: { data: ComboRow[]; height?: number; onPointClick?: (i: number) => void }) {
  const [ref, w] = useSize();
  const [hover, setHover] = React.useState<number | null>(null);
  const m = { t: 16, r: 46, b: 28, l: 46 };
  const iw = Math.max(40, w - m.l - m.r);
  const ih = height - m.t - m.b;
  if (!data.length) return <div ref={ref} style={{ width: "100%", height }} />;
  const vmax = Math.max(...data.map((d) => d.vol)) * 1.1 || 1;
  const rmin = Math.min(...data.map((d) => d.lo)) - 0.1;
  const rmax = Math.max(...data.map((d) => d.hi)) + 0.1;
  const bw = (iw / data.length) * 0.55;
  const xs = (i: number) => m.l + (i + 0.5) * (iw / data.length);
  const yv = (v: number) => m.t + ih - (v / vmax) * ih;
  const yr = (v: number) => m.t + ih - ((v - rmin) / (rmax - rmin || 1)) * ih;
  const rticks = niceTicks(rmin, rmax, 4);

  function idxAt(e: React.MouseEvent<SVGSVGElement>) {
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left;
    let idx = Math.floor(((x - m.l) / iw) * data.length);
    return Math.max(0, Math.min(data.length - 1, idx));
  }
  const warPts: Pt[] = data.map((d, i) => [xs(i), yr(d.war)] as Pt);
  const step = Math.max(1, Math.round(data.length / 7));
  return (
    <div ref={ref} style={{ width: "100%", height }}>
      <svg width={w} height={height} style={{ display: "block", cursor: onPointClick ? "pointer" : "default" }}
        onMouseMove={(e) => setHover(idxAt(e))} onMouseLeave={() => setHover(null)}
        onClick={onPointClick ? (e) => onPointClick(idxAt(e)) : undefined}>
        {rticks.map((tk, i) => (
          <g key={i}>
            <line x1={m.l} x2={m.l + iw} y1={yr(tk)} y2={yr(tk)} stroke="var(--grid)" strokeWidth="1" strokeDasharray="2 4" />
            <text x={m.l + iw + 8} y={yr(tk) + 3.5} textAnchor="start" className="chart-axis">{tk.toFixed(1)}%</text>
          </g>
        ))}
        {data.map((d, i) => i % step === 0 && (
          <text key={i} x={xs(i)} y={height - 9} textAnchor="middle" className="chart-axis">{d.label}</text>
        ))}
        {data.map((d, i) => (
          <rect key={i} x={xs(i) - bw / 2} y={yv(d.vol)} width={bw} height={m.t + ih - yv(d.vol)} rx="1.5" fill="var(--accent)" fillOpacity={hover === i ? 0.55 : 0.28} />
        ))}
        <path d={smoothPath(warPts)} fill="none" stroke="var(--warline)" strokeWidth="2.2" strokeLinecap="round" />
        {hover != null && (
          <g pointerEvents="none">
            <line x1={xs(hover)} x2={xs(hover)} y1={m.t} y2={m.t + ih} stroke="var(--crosshair)" strokeWidth="1" />
            <circle cx={warPts[hover][0]} cy={warPts[hover][1]} r="3.5" fill="var(--warline)" />
            {(() => {
              const bwd = 140, bhd = 60; let bx = xs(hover) + 12; if (bx + bwd > w) bx = xs(hover) - bwd - 12;
              return (
                <g>
                  <rect x={bx} y={m.t + 4} width={bwd} height={bhd} rx="6" className="chart-tip-bg" />
                  <text x={bx + 10} y={m.t + 19} className="chart-tip-title">{data[hover].label}</text>
                  <text x={bx + 10} y={m.t + 36} className="chart-tip-label">WAR</text>
                  <text x={bx + bwd - 10} y={m.t + 36} textAnchor="end" className="chart-tip-val">{data[hover].war.toFixed(2)}%</text>
                  <text x={bx + 10} y={m.t + 52} className="chart-tip-label">Volume</text>
                  <text x={bx + bwd - 10} y={m.t + 52} textAnchor="end" className="chart-tip-val">৳{Math.round(data[hover].vol).toLocaleString()} cr</text>
                </g>
              );
            })()}
          </g>
        )}
      </svg>
    </div>
  );
}
