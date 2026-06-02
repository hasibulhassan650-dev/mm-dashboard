"use client";
import * as React from "react";
import { Sparkline } from "./charts";
import { Icon } from "./Icon";

// ---------------- Delta chip ----------------
export function Delta({ value, suffix = "", invert = false }: { value: number; suffix?: string; invert?: boolean }) {
  const up = value >= 0;
  const good = invert ? !up : up;
  return (
    <span className={"delta " + (good ? "pos" : "neg")}>
      <svg viewBox="0 0 12 12" width="9" height="9" style={{ transform: up ? "none" : "rotate(180deg)" }}>
        <path d="M6 2 L10 8 L2 8 Z" fill="currentColor" />
      </svg>
      {up ? "+" : ""}{value.toFixed(2)}{suffix}
    </span>
  );
}

// ---------------- Panel ----------------
export function Panel({ title, sub, right, children, span, pad = true, className = "" }: {
  title?: string; sub?: string; right?: React.ReactNode; children: React.ReactNode; span?: number; pad?: boolean; className?: string;
}) {
  return (
    <section className={"panel " + className} style={span ? { gridColumn: `span ${span}` } : undefined}>
      {(title || right) && (
        <header className="panel-head">
          <div className="panel-titles">
            {title && <h3 className="panel-title">{title}</h3>}
            {sub && <span className="panel-sub">{sub}</span>}
          </div>
          {right && <div className="panel-right">{right}</div>}
        </header>
      )}
      <div className={pad ? "panel-body" : "panel-body nopad"}>{children}</div>
    </section>
  );
}

// ---------------- KPI card ----------------
export interface Kpi {
  id: string; label: string; value: string; unit?: string; sub?: string;
  delta?: number | null; dir?: "up" | "down" | "flat"; series?: number[]; tone?: "accent" | "neutral";
}
export function KpiCard({ k }: { k: Kpi }) {
  const sparkColor = k.dir === "up" ? "var(--pos)" : k.dir === "down" ? "var(--neg)" : "var(--accent)";
  const cur = k.value.startsWith("৳");
  return (
    <div className="kpi">
      <div className="kpi-top"><span className="kpi-label">{k.label}</span></div>
      <div className="kpi-val">
        {cur
          ? <><span className="kpi-cur">৳</span><span className="kpi-num">{k.value.slice(1)}</span></>
          : <span className="kpi-num">{k.value}</span>}
        {k.unit && <span className="kpi-unit">{k.unit}</span>}
        {k.delta != null && <span className="kpi-delta"><Delta value={k.delta} /></span>}
      </div>
      {k.series && k.series.length > 1 && <div className="kpi-spark"><Sparkline data={k.series} color={sparkColor} h={30} /></div>}
      {k.sub && <div className="kpi-sub">{k.sub}</div>}
    </div>
  );
}

// ---------------- Data table ----------------
export interface Col<T> {
  key: string; label: string; align?: "r"; mono?: boolean;
  render?: (r: T) => React.ReactNode; cls?: (r: T) => string;
}
export function DataTable<T>({ cols, rows }: { cols: Col<T>[]; rows: T[] }) {
  return (
    <div className="table-wrap">
      <table className="dt">
        <thead>
          <tr>{cols.map((c, i) => <th key={i} className={c.align === "r" ? "r" : ""}>{c.label}</th>)}</tr>
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr><td colSpan={cols.length} style={{ textAlign: "center", color: "var(--fg-mute)", height: 80 }}>No data for this period</td></tr>
          )}
          {rows.map((r, ri) => (
            <tr key={ri}>
              {cols.map((c, ci) => (
                <td key={ci} className={(c.align === "r" ? "r " : "") + (c.mono ? "mono " : "") + (c.cls ? c.cls(r) : "")}>
                  {c.render ? c.render(r) : ((r as Record<string, unknown>)[c.key] as React.ReactNode)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------- Segmented tabs ----------------
export function SegTabs({ tabs, value, onChange }: { tabs: string[]; value: string; onChange: (v: string) => void }) {
  return (
    <div className="seg">
      {tabs.map((t) => (
        <button key={t} className={"seg-b" + (value === t ? " on" : "")} onClick={() => onChange(t)}>{t}</button>
      ))}
    </div>
  );
}

// ---------------- Legend toggle ----------------
export interface LegendCat { key: string; label: string; color: string; desc?: string }
export function LegendToggle({ cats, active, onToggle }: { cats: LegendCat[]; active: Record<string, boolean>; onToggle: (k: string) => void }) {
  return (
    <div className="legend">
      {cats.map((c) => (
        <button key={c.key} className={"leg" + (active[c.key] ? "" : " off")} onClick={() => onToggle(c.key)} title={c.desc}>
          <span className="leg-sw" style={{ background: c.color }} />
          {c.label}
        </button>
      ))}
    </div>
  );
}

// ---------------- Metric list ----------------
export function MetricList({ items }: { items: { k: React.ReactNode; v: React.ReactNode }[] }) {
  return (
    <div className="metric-list">
      {items.map((m, i) => (
        <div className="metric" key={i}><span>{m.k}</span><b>{m.v}</b></div>
      ))}
    </div>
  );
}

// ---------------- Policy rate corridor ----------------
export interface CorridorData { sdf: number; repo: number; slf: number; war: number }
export function CorridorViz({ c }: { c: CorridorData }) {
  const lo = 8.0, hi = 12.0;
  const pct = (v: number) => Math.max(0, Math.min(100, ((hi - v) / (hi - lo)) * 100));
  const rows = [
    { k: "SLF", label: "Standing Lending (ceiling)", v: c.slf, color: "var(--neg)" },
    { k: "REPO", label: "Policy Repo Rate", v: c.repo, color: "var(--accent)" },
    { k: "SDF", label: "Standing Deposit (floor)", v: c.sdf, color: "var(--info)" },
  ];
  return (
    <div className="corridor">
      <div className="corr-track">
        <div className="corr-band" style={{ top: pct(c.slf) + "%", bottom: (100 - pct(c.sdf)) + "%" }} />
        {rows.map((r) => (
          <div key={r.k} className="corr-line" style={{ top: pct(r.v) + "%" }}>
            <span className="corr-dot" style={{ background: r.color }} />
            <span className="corr-tick">{r.v.toFixed(2)}%</span>
          </div>
        ))}
        <div className="corr-war" style={{ top: pct(c.war) + "%" }}>
          <span className="corr-war-lab">WAR {c.war.toFixed(2)}%</span>
        </div>
      </div>
      <div className="corr-legend">
        {rows.map((r) => (
          <div key={r.k} className="corr-leg">
            <span className="corr-sw" style={{ background: r.color }} />
            <div>
              <div className="corr-leg-k">{r.k} · <b>{r.v.toFixed(2)}%</b></div>
              <div className="corr-leg-l">{r.label}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------- Stub / empty state ----------------
export function Stub({ icon, title, children }: { icon: string; title: string; children?: React.ReactNode }) {
  return (
    <div className="stub">
      <div className="stub-ic"><Icon name={icon} size={26} /></div>
      <h2>{title}</h2>
      {children}
      <span className="stub-badge"><span className="status-dot" /> Feed connected</span>
    </div>
  );
}
