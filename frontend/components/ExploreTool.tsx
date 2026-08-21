"use client";
import * as React from "react";
import { Panel } from "@/components/terminal/ui";
import DownloadButton from "@/components/DownloadButton";

export interface SeriesDef {
  key: string; label: string; group: string; unit: string;
  points: Record<string, number>;   // dateStr -> value
}

// ── math helpers ──────────────────────────────────────────────────────────────
function fillOnGrid(points: Record<string, number>, grid: string[]): (number | null)[] {
  const entries = Object.entries(points).sort((a, b) => a[0].localeCompare(b[0]));
  const out: (number | null)[] = [];
  let i = 0, last: number | null = null;
  for (const d of grid) {
    while (i < entries.length && entries[i][0] <= d) { last = entries[i][1]; i++; }
    out.push(last);
  }
  return out;
}
function pearson(a: (number | null)[], b: (number | null)[]): { r: number | null; n: number } {
  const xs: number[] = [], ys: number[] = [];
  for (let i = 0; i < a.length; i++) if (a[i] != null && b[i] != null) { xs.push(a[i]!); ys.push(b[i]!); }
  const n = xs.length;
  if (n < 3) return { r: null, n };
  const mx = xs.reduce((s, v) => s + v, 0) / n, my = ys.reduce((s, v) => s + v, 0) / n;
  let sxy = 0, sxx = 0, syy = 0;
  for (let i = 0; i < n; i++) { const dx = xs[i] - mx, dy = ys[i] - my; sxy += dx * dy; sxx += dx * dx; syy += dy * dy; }
  if (sxx === 0 || syy === 0) return { r: null, n };
  return { r: sxy / Math.sqrt(sxx * syy), n };
}
function corrColor(r: number | null): string {
  if (r == null) return "transparent";
  const a = Math.min(1, Math.abs(r));
  const pole = r >= 0 ? [13, 148, 136] : [220, 38, 38];   // teal (+) / red (−), diverging
  return `rgba(${pole[0]},${pole[1]},${pole[2]},${(0.1 + 0.82 * a).toFixed(2)})`;
}

// Opens on a G-sec-centric spread of the curve plus the liquidity and FX
// links, rather than every series at once — a 32x32 grid is unreadable.
// Everything else is one click away in the picker.
const DEFAULT = ["y91", "y364", "y2y", "y10y", "y20y", "s_2s10s",
                 "cover_bill", "call_war", "omo_net", "fx"];

export default function ExploreTool({ variables, min, max }: { variables: SeriesDef[]; min: string; max: string }) {
  const byKey = React.useMemo(() => Object.fromEntries(variables.map((v) => [v.key, v])), [variables]);
  const [sel, setSel] = React.useState<string[]>(() => {
    const d = DEFAULT.filter((k) => byKey[k]);
    return d.length >= 2 ? d : variables.slice(0, 8).map((v) => v.key);
  });
  const [from, setFrom] = React.useState(min);
  const [to, setTo] = React.useState(max);
  const [pair, setPair] = React.useState<[string, string] | null>(null);

  const toggle = (k: string) => setSel((s) => s.includes(k) ? s.filter((x) => x !== k) : [...s, k]);

  // daily grid over [from,to] from the union of selected series' dates
  const grid = React.useMemo(() => {
    const set = new Set<string>();
    for (const k of sel) for (const d of Object.keys(byKey[k]?.points || {})) if (d >= from && d <= to) set.add(d);
    return [...set].sort();
  }, [sel, from, to, byKey]);

  const filled = React.useMemo(() => {
    const m: Record<string, (number | null)[]> = {};
    for (const k of sel) m[k] = fillOnGrid(byKey[k].points, grid);
    return m;
  }, [sel, grid, byKey]);

  const matrix = React.useMemo(() =>
    sel.map((ki) => sel.map((kj) => ki === kj ? { r: 1, n: grid.length } : pearson(filled[ki], filled[kj]))),
    [sel, filled, grid]);

  // scatter data for the selected pair
  const scatter = React.useMemo(() => {
    if (!pair) return null;
    const [xk, yk] = pair;
    const xa = filled[xk], ya = filled[yk];
    if (!xa || !ya) return null;
    const pts: { x: number; y: number }[] = [];
    for (let i = 0; i < grid.length; i++) if (xa[i] != null && ya[i] != null) pts.push({ x: xa[i]!, y: ya[i]! });
    const { r, n } = pearson(xa, ya);
    // least-squares line
    let slope = 0, intercept = 0;
    if (pts.length >= 2) {
      const mx = pts.reduce((s, p) => s + p.x, 0) / pts.length, my = pts.reduce((s, p) => s + p.y, 0) / pts.length;
      let sxy = 0, sxx = 0;
      for (const p of pts) { sxy += (p.x - mx) * (p.y - my); sxx += (p.x - mx) ** 2; }
      slope = sxx ? sxy / sxx : 0; intercept = my - slope * mx;
    }
    return { pts, r, n, slope, intercept, xk, yk };
  }, [pair, filled, grid]);

  const exportRows = React.useMemo(() =>
    grid.map((d, i) => { const row: Record<string, string | number> = { date: d }; for (const k of sel) row[k] = filled[k][i] ?? ""; return row; }),
    [grid, sel, filled]);

  const groups = [...new Set(variables.map((v) => v.group))];

  return (
    <div className="grid12">
      <Panel title="Variables" sub="pick any series — correlations recompute live" span={12}>
        <div className="exp-vars">
          {groups.map((g) => (
            <div key={g} className="exp-group">
              <span className="exp-group-lb">{g}</span>
              {variables.filter((v) => v.group === g).map((v) => (
                <button key={v.key} className={"exp-chip" + (sel.includes(v.key) ? " on" : "")} onClick={() => toggle(v.key)}>
                  {v.label}
                </button>
              ))}
            </div>
          ))}
        </div>
        <div className="drc" style={{ marginTop: 12 }}>
          <div className="drc-range">
            <input type="date" value={from} min={min} max={to} onChange={(e) => e.target.value && setFrom(e.target.value)} />
            <span className="drc-dash">→</span>
            <input type="date" value={to} min={from} max={max} onChange={(e) => e.target.value && setTo(e.target.value)} />
          </div>
          <span style={{ fontSize: 11.5, color: "var(--fg-mute)" }}>{grid.length} aligned days · sparse series forward-filled</span>
        </div>
      </Panel>

      <Panel title="Correlation Matrix" sub="Pearson r · click any cell to plot that pair" span={pair ? 6 : 12}
        right={<DownloadButton data={exportRows} filename="explore_series" label="Export series" />}>
        <div className="exp-matrix-wrap">
          <table className="exp-matrix">
            <thead>
              <tr><th></th>{sel.map((k) => <th key={k} title={byKey[k].label}>{byKey[k].label}</th>)}</tr>
            </thead>
            <tbody>
              {sel.map((ki, i) => (
                <tr key={ki}>
                  <th title={byKey[ki].label}>{byKey[ki].label}</th>
                  {sel.map((kj, j) => {
                    const c = matrix[i][j];
                    return (
                      <td key={kj} className={"exp-cell" + (ki !== kj ? " click" : "")}
                        style={{ background: corrColor(c.r) }}
                        title={`${byKey[ki].label} vs ${byKey[kj].label}: r=${c.r?.toFixed(2) ?? "n/a"} (n=${c.n})`}
                        onClick={() => ki !== kj && setPair([kj, ki])}>
                        {c.r == null ? "·" : c.r.toFixed(2)}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="exp-legend">
          <span>−1</span><span className="exp-scale" /><span>+1</span>
          <span style={{ marginLeft: "auto", color: "var(--fg-mute)" }}>red = move opposite · teal = move together</span>
        </div>
      </Panel>

      {pair && scatter && (
        <Panel title={`${byKey[scatter.xk].label}  ×  ${byKey[scatter.yk].label}`}
          sub={scatter.r != null ? `r = ${scatter.r.toFixed(3)} · n = ${scatter.n} · ${Math.abs(scatter.r) > 0.7 ? "strong" : Math.abs(scatter.r) > 0.4 ? "moderate" : "weak"} ${scatter.r >= 0 ? "positive" : "negative"}` : "not enough overlap"}
          span={6} right={<button className="exp-chip" onClick={() => setPair(null)}>✕ close</button>}>
          <Scatter s={scatter} xLabel={`${byKey[scatter.xk].label} (${byKey[scatter.xk].unit})`} yLabel={`${byKey[scatter.yk].label} (${byKey[scatter.yk].unit})`} />
        </Panel>
      )}
    </div>
  );
}

// ── scatter (custom SVG: full control over regression line + labels) ──────────
function Scatter({ s, xLabel, yLabel }: {
  s: { pts: { x: number; y: number }[]; slope: number; intercept: number }; xLabel: string; yLabel: string;
}) {
  const W = 520, H = 340, P = 44;
  if (s.pts.length < 2) return <div style={{ padding: 30, color: "var(--fg-mute)", textAlign: "center" }}>Not enough overlapping observations.</div>;
  const xs = s.pts.map((p) => p.x), ys = s.pts.map((p) => p.y);
  const xmin = Math.min(...xs), xmax = Math.max(...xs), ymin = Math.min(...ys), ymax = Math.max(...ys);
  const sx = (x: number) => P + (xmax === xmin ? 0.5 : (x - xmin) / (xmax - xmin)) * (W - 2 * P);
  const sy = (y: number) => H - P - (ymax === ymin ? 0.5 : (y - ymin) / (ymax - ymin)) * (H - 2 * P);
  const lineX1 = xmin, lineX2 = xmax;
  const lineY1 = s.slope * lineX1 + s.intercept, lineY2 = s.slope * lineX2 + s.intercept;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ maxHeight: 360 }}>
      <line x1={P} y1={H - P} x2={W - P} y2={H - P} stroke="var(--border)" />
      <line x1={P} y1={P} x2={P} y2={H - P} stroke="var(--border)" />
      <text x={W / 2} y={H - 8} textAnchor="middle" fontSize="11" fill="var(--fg-mute)">{xLabel}</text>
      <text x={14} y={H / 2} textAnchor="middle" fontSize="11" fill="var(--fg-mute)" transform={`rotate(-90 14 ${H / 2})`}>{yLabel}</text>
      <text x={P} y={H - P + 14} fontSize="10" fill="var(--fg-mute)">{xmin.toFixed(2)}</text>
      <text x={W - P} y={H - P + 14} fontSize="10" fill="var(--fg-mute)" textAnchor="end">{xmax.toFixed(2)}</text>
      <text x={P - 6} y={H - P} fontSize="10" fill="var(--fg-mute)" textAnchor="end">{ymin.toFixed(2)}</text>
      <text x={P - 6} y={P + 4} fontSize="10" fill="var(--fg-mute)" textAnchor="end">{ymax.toFixed(2)}</text>
      <line x1={sx(lineX1)} y1={sy(lineY1)} x2={sx(lineX2)} y2={sy(lineY2)} stroke="var(--accent)" strokeWidth="2" strokeDasharray="5 4" opacity="0.8" />
      {s.pts.map((p, i) => <circle key={i} cx={sx(p.x)} cy={sy(p.y)} r="3" fill="var(--accent)" fillOpacity="0.5" />)}
    </svg>
  );
}
