"use client";
import * as React from "react";
import { useSize, niceTicks } from "./terminal/charts";
import { ForecastRow, ForecastModel } from "@/lib/api";
import { fmtPct, fmtDate } from "@/lib/format";

/**
 * Next-auction cutoff forecast per tenor, plotted as CHANGE FROM THE LAST PRINT
 * in basis points rather than as a yield level.
 *
 * Levels would put a 91D bill at 9.30% next to a 20Y bond at 10.41% and make
 * the interesting number — how far the model thinks the rate moves, and how
 * wide the honest error band around that is — invisible. In change space every
 * tenor shares one axis, zero means "same as last time" (that IS the naive
 * forecast), and the band width is directly comparable bill-to-bond.
 *
 * Band = ±1.96 × the model's own out-of-sample RMSE. It is measured forecast
 * error, not an assumption about how yields are distributed.
 */

// Only the three competitive models are plotted. Five models x nine tenors is
// 45 bands — unreadable, and it would need a five-hue categorical palette to
// stay colourblind-safe. OLS and Blend are still fully visible in the scoreboard
// table below; this chart is for the comparison you actually act on.
const MODELS: { key: ForecastModel; label: string; color: string }[] = [
  { key: "curve",    label: "Curve carry", color: "#1f9e6e" },  // --accent
  { key: "naive",    label: "Naive",       color: "#4f8ff7" },  // --info
  { key: "momentum", label: "Momentum",    color: "#c2703c" },  // validated 3rd hue
];

const ROW_H = 58;
const PAD = { t: 26, r: 18, b: 30, l: 62 };

interface Band { tenor: string; model: ForecastModel; mid: number; lo: number; hi: number; row: number }

export default function ForecastIntervalChart({ rows }: { rows: ForecastRow[] }) {
  const [ref, w] = useSize();
  const [hover, setHover] = React.useState<Band | null>(null);
  const [on, setOn] = React.useState<Partial<Record<ForecastModel, boolean>>>(
    { curve: true, naive: true, momentum: true });

  // Order tenors short → long so the plot reads like a curve.
  const tenors = React.useMemo(() => {
    const order = (t: string) => {
      const m = /^(\d+(?:\.\d+)?)(D|Y)/.exec(t);
      if (!m) return 999;
      return parseFloat(m[1]) * (m[2] === "D" ? 1 / 365 : 1);
    };
    return [...new Set(rows.map(r => r.tenor))].sort((a, b) => order(a) - order(b));
  }, [rows]);

  // bps change vs the last actual print
  const bands = React.useMemo(() => {
    const out: Band[] = [];
    rows.forEach(r => {
      if (r.last_actual_yield == null || !on[r.model]) return;
      const base = r.last_actual_yield;
      out.push({
        tenor: r.tenor, model: r.model, row: tenors.indexOf(r.tenor),
        mid: (r.point_yield - base) * 100,
        lo: (r.lo_yield - base) * 100,
        hi: (r.hi_yield - base) * 100,
      });
    });
    return out;
  }, [rows, tenors, on]);

  if (rows.length === 0) {
    return <div style={{ padding: 28, textAlign: "center", color: "var(--fg-mute)", fontSize: 12.5 }}>
      No forecast run yet — the weekly job populates this.
    </div>;
  }

  const h = PAD.t + PAD.b + tenors.length * ROW_H;
  const lo = Math.min(...bands.map(b => b.lo), 0);
  const hi = Math.max(...bands.map(b => b.hi), 0);
  const ticks = niceTicks(lo, hi, 6);
  const xMin = Math.min(...ticks), xMax = Math.max(...ticks);
  const px = (v: number) => PAD.l + ((v - xMin) / (xMax - xMin || 1)) * (w - PAD.l - PAD.r);
  // Stack the models within each tenor's band so they never overlap. Curve does
  // not exist on the anchor tenor, so that slot is simply left empty.
  const OFFSET: Partial<Record<ForecastModel, number>> = { curve: -14, naive: 0, momentum: 14 };
  const py = (row: number, model: ForecastModel) =>
    PAD.t + row * ROW_H + ROW_H / 2 + (OFFSET[model] ?? 0);

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <div className="legend" style={{ marginBottom: 6 }}>
        {MODELS.map(m => (
          <button key={m.key} className={"leg" + (on[m.key] ? "" : " off")}
            onClick={() => setOn(p => ({ ...p, [m.key]: !p[m.key] }))}
            title={m.key === "naive" ? "Last cutoff, unchanged — the benchmark"
              : m.key === "momentum" ? "Last cutoff + β × last change"
              : "Last cutoff + λ × how far the 364D bill has moved since this tenor last auctioned"}>
            <span className="leg-sw" style={{ background: m.color }} />
            {m.label}
          </button>
        ))}
        <span style={{ fontSize: 11, color: "var(--fg-mute)", marginLeft: 6, alignSelf: "center" }}>
          bar = 95% band (±1.96 × out-of-sample RMSE)
        </span>
      </div>

      <svg width={w} height={h} role="img"
        aria-label="Forecast change from last auction cutoff, in basis points, per tenor, with 95% error bands">
        {/* vertical grid + axis */}
        {ticks.map(t => (
          <g key={t}>
            <line x1={px(t)} x2={px(t)} y1={PAD.t - 8} y2={h - PAD.b}
              stroke={t === 0 ? "var(--crosshair)" : "var(--grid)"} strokeWidth={1}
              strokeDasharray={t === 0 ? "3 3" : undefined} />
            <text className="chart-axis" x={px(t)} y={h - PAD.b + 16} textAnchor="middle">{t > 0 ? `+${t}` : t}</text>
          </g>
        ))}
        <text className="chart-axis" x={w - PAD.r} y={h - PAD.b + 28} textAnchor="end">bps vs last print</text>
        {/* labels the zero line itself — zero change IS the naive forecast */}
        <text className="chart-axis" x={px(0)} y={PAD.t - 14} textAnchor="middle" style={{ fontSize: 10 }}>
          unchanged
        </text>

        {tenors.map((t, i) => (
          <g key={t}>
            <text className="chart-axis" x={PAD.l - 12} y={PAD.t + i * ROW_H + ROW_H / 2 + 4}
              textAnchor="end" style={{ fontSize: 11.5, fill: "var(--fg-dim)" }}>{t}</text>
            {i > 0 && <line x1={PAD.l} x2={w - PAD.r} y1={PAD.t + i * ROW_H} y2={PAD.t + i * ROW_H}
              stroke="var(--border-soft)" strokeWidth={1} />}
          </g>
        ))}

        {bands.map((b, i) => {
          const color = MODELS.find(m => m.key === b.model)!.color;
          const y = py(b.row, b.model);
          const active = hover?.tenor === b.tenor && hover?.model === b.model;
          return (
            <g key={i} onMouseEnter={() => setHover(b)} onMouseLeave={() => setHover(null)}>
              {/* generous invisible hit target — the band itself is only 6px tall */}
              <rect x={PAD.l} y={y - 11} width={w - PAD.l - PAD.r} height={22} fill="transparent" />
              <rect x={px(b.lo)} y={y - 3} width={Math.max(2, px(b.hi) - px(b.lo))} height={6} rx={3}
                fill={color} opacity={active ? 0.42 : 0.24} />
              <circle cx={px(b.mid)} cy={y} r={active ? 6 : 4.5} fill={color}
                stroke="var(--panel)" strokeWidth={2} />
            </g>
          );
        })}
      </svg>

      {hover && (
        <div style={{
          position: "absolute", left: Math.min(px(hover.mid) + 12, w - 210), top: py(hover.row, hover.model) + 10,
          background: "var(--panel-2)", border: "1px solid var(--border)", borderRadius: "var(--radius-sm)",
          padding: "8px 10px", fontSize: 11.5, pointerEvents: "none", zIndex: 5, minWidth: 190,
          boxShadow: "0 8px 24px rgba(0,0,0,.35)",
        }}>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>
            {hover.tenor} · {MODELS.find(m => m.key === hover.model)!.label}
          </div>
          <TipRow k="Forecast change" v={`${hover.mid >= 0 ? "+" : ""}${hover.mid.toFixed(1)} bps`} />
          <TipRow k="95% band" v={`${hover.lo.toFixed(0)} to ${hover.hi > 0 ? "+" : ""}${hover.hi.toFixed(0)} bps`} />
          {(() => {
            const r = rows.find(x => x.tenor === hover.tenor && x.model === hover.model)!;
            return <>
              <TipRow k="Point yield" v={fmtPct(r.point_yield, 4)} />
              <TipRow k="Last print" v={`${fmtPct(r.last_actual_yield, 4)} · ${fmtDate(r.last_auction_date)}`} />
              <TipRow k="Target auction" v={fmtDate(r.target_auction_date)} />
            </>;
          })()}
        </div>
      )}
    </div>
  );
}

function TipRow({ k, v }: { k: string; v: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 12, color: "var(--fg-dim)" }}>
      <span>{k}</span><b className="mono" style={{ color: "var(--fg)" }}>{v}</b>
    </div>
  );
}
