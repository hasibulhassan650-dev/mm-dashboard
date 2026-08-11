"use client";
import { useMemo, useState, useRef, useEffect } from "react";
import { SecondaryYieldRow, Security } from "@/lib/api";
import { fmtNum, fmtPct } from "@/lib/format";
import { Panel } from "@/components/terminal/ui";

interface Props { secondary: SecondaryYieldRow[]; securities: Security[]; repoDefault: number }
interface Holding { isin: string; faceMn: number; costPx: number | null }

const DAY = 86400000;
const addMonths = (d: Date, m: number) => { const x = new Date(d); x.setMonth(x.getMonth() + m); return x; };
const yearsBetween = (a: Date, b: Date) => (b.getTime() - a.getTime()) / (365.25 * DAY);

// BD convention: BGTBs pay semi-annual, FRTBs quarterly, T-bills are zero-coupon
// (handled by the discount path). Inferred from security type — the Security API
// row doesn't carry coupon_frequency.
function freqOf(securityType: string | null | undefined): number { return securityType === "FRTB" ? 4 : 2; }

/** Coupon dates anchored on maturity, stepped back to issue (ascending). */
function couponDates(maturity: Date, freq: number, issue: Date): Date[] {
  const step = 12 / freq, out: Date[] = [];
  let d = new Date(maturity);
  while (d.getTime() > issue.getTime() && out.length < 400) { out.push(new Date(d)); d = addMonths(d, -step); }
  return out.sort((a, b) => a.getTime() - b.getTime());
}

interface Priced { dirty: number; clean: number; accrued: number }
/** Cashflow price per 100 face at a given settlement date. Semi-annual for BGTBs
 *  (HFLY), quarterly (QRTY); zero-coupon compound discount for T-bills. */
function priceBond(couponRate: number | null, freq: number, maturity: Date, issue: Date, yieldPct: number, settle: Date): Priced {
  const y = yieldPct / 100;
  const zero = !couponRate || couponRate <= 0;
  if (zero) {
    const t = yearsBetween(settle, maturity);
    const dirty = t <= 0 ? 100 : 100 * Math.pow(1 + y, -t);
    return { dirty, clean: dirty, accrued: 0 };
  }
  const cpn = couponRate / freq;                                   // coupon per 100 per period
  const dates = couponDates(maturity, freq, issue);
  const future = dates.filter((d) => d.getTime() > settle.getTime());
  const nextC = future[0] ?? maturity;
  const prevC = [...dates].reverse().find((d) => d.getTime() <= settle.getTime()) ?? issue;
  const periodDays = (nextC.getTime() - prevC.getTime()) / DAY;
  const accrDays = (settle.getTime() - prevC.getTime()) / DAY;
  const accrued = periodDays > 0 ? cpn * Math.max(0, Math.min(1, accrDays / periodDays)) : 0;
  let dirty = 0;
  for (const d of future) dirty += cpn * Math.pow(1 + y / freq, -yearsBetween(settle, d) * freq);
  dirty += 100 * Math.pow(1 + y / freq, -yearsBetween(settle, maturity) * freq);
  return { dirty, clean: dirty - accrued, accrued };
}

function interpYield(points: { x: number; y: number }[], t: number): number | null {
  if (!points.length) return null;
  if (t <= points[0].x) return points[0].y;
  const last = points[points.length - 1];
  if (t >= last.x) return last.y;
  for (let i = 1; i < points.length; i++) if (t <= points[i].x) {
    const a = points[i - 1], b = points[i];
    return a.y + (t - a.x) / (b.x - a.x) * (b.y - a.y);
  }
  return last.y;
}

interface Analytics {
  isin: string; name: string; type: string; years: number | null; couponPct: number | null;
  yieldPct: number | null; source: "exact" | "interp" | "none";
  dirty: number | null; clean: number | null; accrued: number | null;
  faceMn: number; valueMn: number | null; costMn: number | null; pnlMn: number | null;
  modDur: number | null; convexity: number | null; dv01Mn: number | null;
  carryRollMn: number | null; carryRollAnnPct: number | null; breakevenBp: number | null;
}

// ── ISIN autocomplete ─────────────────────────────────────────────────────────
function IsinInput({ value, onChange, securities }: { value: string; onChange: (v: string) => void; securities: Security[] }) {
  const [open, setOpen] = useState(false);
  const [hi, setHi] = useState(0);
  const boxRef = useRef<HTMLDivElement>(null);
  const matches = useMemo(() => {
    const s = value.trim().toUpperCase();
    if (!s) return [];
    if (securities.some((x) => x.isin.toUpperCase() === s)) return [];
    return securities.filter((x) => x.isin.toUpperCase().includes(s) || (x.security_name_norm || "").toUpperCase().includes(s)).slice(0, 8);
  }, [value, securities]);
  useEffect(() => {
    const away = (e: MouseEvent) => { if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, []);
  const pick = (isin: string) => { onChange(isin); setOpen(false); };
  const onKey = (e: React.KeyboardEvent) => {
    if (!matches.length) return;
    if (e.key === "ArrowDown") { e.preventDefault(); setOpen(true); setHi((h) => Math.min(h + 1, matches.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setHi((h) => Math.max(h - 1, 0)); }
    else if (e.key === "Enter") { e.preventDefault(); pick(matches[hi].isin); }
    else if (e.key === "Tab" && open) { onChange(matches[hi].isin); setOpen(false); }
    else if (e.key === "Escape") setOpen(false);
  };
  return (
    <div ref={boxRef} style={{ position: "relative" }}>
      <input value={value} onChange={(e) => { onChange(e.target.value.toUpperCase()); setOpen(true); setHi(0); }} onFocus={() => setOpen(true)}
        onKeyDown={onKey} placeholder="type ISIN or name…" spellCheck={false} style={inputStyle} />
      {open && matches.length > 0 && (
        <div style={{ position: "absolute", top: "100%", left: 0, right: 0, zIndex: 40, marginTop: 3, background: "var(--panel)", border: "1px solid var(--border)", borderRadius: 8, boxShadow: "0 10px 30px rgba(0,0,0,.4)", overflow: "hidden", maxHeight: 260, overflowY: "auto" }}>
          {matches.map((m, i) => (
            <button key={m.isin} type="button" onMouseEnter={() => setHi(i)} onClick={() => pick(m.isin)}
              style={{ display: "flex", width: "100%", gap: 8, padding: "7px 10px", border: "none", textAlign: "left", cursor: "pointer", background: i === hi ? "var(--accent-soft)" : "transparent", color: i === hi ? "var(--accent)" : "var(--fg-dim)" }}>
              <span style={{ fontFamily: "var(--mono)", fontSize: 12 }}>{m.isin}</span>
              <span style={{ fontSize: 11.5, color: "var(--fg-mute)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{m.security_name_norm}</span>
              <span style={{ marginLeft: "auto", fontSize: 10.5, color: "var(--fg-mute)" }}>{m.security_type}</span>
            </button>
          ))}
          <div style={{ padding: "5px 10px", fontSize: 10.5, color: "var(--fg-mute)", borderTop: "1px solid var(--border)" }}>↑↓ move · Tab/Enter fill</div>
        </div>
      )}
    </div>
  );
}

const inputStyle: React.CSSProperties = { width: "100%", background: "var(--bg-elev)", border: "1px solid var(--border)", borderRadius: 7, padding: "7px 9px", fontFamily: "var(--mono)", fontSize: 12.5, color: "var(--fg)", outline: "none" };
const HORIZONS: { label: string; y: number }[] = [{ label: "1M", y: 1 / 12 }, { label: "3M", y: 0.25 }, { label: "6M", y: 0.5 }];
const SHIFTS = [-100, -50, -25, 25, 50, 100];

export default function PortfolioTool({ secondary, securities, repoDefault }: Props) {
  const [rows, setRows] = useState<Holding[]>([{ isin: "", faceMn: 0, costPx: null }]);
  const [funding, setFunding] = useState(repoDefault);
  const [horizon, setHorizon] = useState(0.25);
  const [bulk, setBulk] = useState(false);
  const [today] = useState(() => new Date());

  const secByIsin = useMemo(() => new Map(secondary.map((s) => [s.isin, s])), [secondary]);
  const securityByIsin = useMemo(() => new Map(securities.map((s) => [s.isin, s])), [securities]);
  const curve = useMemo(() => secondary.filter((s) => s.remaining_years != null && s.market_yield_pct != null)
    .map((s) => ({ x: s.remaining_years, y: s.market_yield_pct })).sort((a, b) => a.x - b.x), [secondary]);

  const setRow = (i: number, patch: Partial<Holding>) => setRows((r) => r.map((x, j) => j === i ? { ...x, ...patch } : x));
  const addRow = () => setRows((r) => [...r, { isin: "", faceMn: 0, costPx: null }]);
  const delRow = (i: number) => setRows((r) => r.length > 1 ? r.filter((_, j) => j !== i) : [{ isin: "", faceMn: 0, costPx: null }]);
  const loadBulk = (t: string) => {
    const out: Holding[] = [];
    for (const line of t.split("\n")) {
      const p = line.trim().split(/[,\s]+/);
      if (p.length < 2) continue;
      const f = parseFloat(p[1].replace(/,/g, ""));
      if (!Number.isNaN(f)) out.push({ isin: p[0].toUpperCase(), faceMn: f, costPx: p[2] ? parseFloat(p[2]) : null });
    }
    if (out.length) { setRows(out); setBulk(false); }
  };

  const holdings = rows.filter((h) => h.isin && h.faceMn > 0);

  const rowsA: Analytics[] = useMemo(() => holdings.map((h) => {
    const sec = securityByIsin.get(h.isin), sy = secByIsin.get(h.isin);
    const name = sec?.security_name_norm ?? sy?.security_name_norm ?? h.isin;
    const type = sec?.security_type ?? sy?.security_type ?? "—";
    const couponPct = sec?.coupon_rate_pct ?? null;
    const freq = freqOf(type);
    const maturity = sec?.maturity_date ? new Date(sec.maturity_date) : (sy?.maturity_date ? new Date(sy.maturity_date) : null);
    const issue = sec?.issue_date ? new Date(sec.issue_date) : (maturity ? addMonths(maturity, -120) : null);
    let years: number | null = sy?.remaining_years ?? (maturity ? yearsBetween(today, maturity) : null);

    let yieldPct: number | null = null; let source: Analytics["source"] = "none";
    if (sy?.market_yield_pct != null) { yieldPct = sy.market_yield_pct; source = "exact"; }
    else if (years != null) { yieldPct = interpYield(curve, years); if (yieldPct != null) source = "interp"; }

    const blank: Analytics = { isin: h.isin, name, type, years, couponPct, yieldPct, source, dirty: null, clean: null, accrued: null, faceMn: h.faceMn, valueMn: null, costMn: null, pnlMn: null, modDur: null, convexity: null, dv01Mn: null, carryRollMn: null, carryRollAnnPct: null, breakevenBp: null };
    if (years == null || years <= 0 || yieldPct == null || maturity == null || issue == null) return blank;

    const p0 = priceBond(couponPct, freq, maturity, issue, yieldPct, today);
    const valueMn = h.faceMn * p0.dirty / 100;
    const costMn = h.costPx != null ? h.faceMn * h.costPx / 100 : null;
    const pnlMn = costMn != null ? valueMn - costMn : null;

    // duration & convexity — 25bp central difference
    const pu = priceBond(couponPct, freq, maturity, issue, yieldPct + 0.25, today).dirty;
    const pd = priceBond(couponPct, freq, maturity, issue, yieldPct - 0.25, today).dirty;
    const modDur = (pd - pu) / (2 * 0.0025 * p0.dirty);
    const convexity = (pu + pd - 2 * p0.dirty) / (p0.dirty * 0.0025 * 0.0025);
    const dv01Mn = modDur * valueMn * 0.0001;

    // carry & roll over the horizon on an unchanged curve
    const settleEnd = new Date(today.getTime() + horizon * 365.25 * DAY);
    const yearsEnd = yearsBetween(settleEnd, maturity);
    let carryRollMn: number | null = null, carryRollAnnPct: number | null = null, breakevenBp: number | null = null;
    if (yearsEnd > 0) {
      const yEnd = interpYield(curve, yearsEnd) ?? yieldPct;
      const end = priceBond(couponPct, freq, maturity, issue, yEnd, settleEnd);
      const cpn = (couponPct && couponPct > 0) ? couponPct / freq : 0;
      const dates = maturity && issue ? couponDates(maturity, freq, issue) : [];
      const couponsRecv = dates.filter((d) => d.getTime() > today.getTime() && d.getTime() <= settleEnd.getTime()).length * cpn;
      const fundingCost = p0.dirty * (funding / 100) * horizon;
      const pnl100 = (end.dirty - p0.dirty) + couponsRecv - fundingCost;   // per 100 over horizon
      carryRollMn = h.faceMn * pnl100 / 100;
      carryRollAnnPct = (pnl100 / p0.dirty) / horizon * 100;
      const dv01_100 = modDur * p0.dirty * 0.0001;
      breakevenBp = dv01_100 > 0 ? pnl100 / dv01_100 : null;
    }
    return { ...blank, dirty: p0.dirty, clean: p0.clean, accrued: p0.accrued, valueMn, costMn, pnlMn, modDur, convexity, dv01Mn, carryRollMn, carryRollAnnPct, breakevenBp };
  }), [holdings, secByIsin, securityByIsin, curve, today, funding, horizon]);

  // portfolio aggregates
  const V = rowsA.reduce((s, p) => s + (p.valueMn ?? 0), 0);
  const face = rowsA.reduce((s, p) => s + p.faceMn, 0);
  const cost = rowsA.reduce((s, p) => s + (p.costMn ?? 0), 0);
  const hasCost = rowsA.some((p) => p.costMn != null);
  const pnl = rowsA.reduce((s, p) => s + (p.pnlMn ?? 0), 0);
  const dv01 = rowsA.reduce((s, p) => s + (p.dv01Mn ?? 0), 0);
  const carryRoll = rowsA.reduce((s, p) => s + (p.carryRollMn ?? 0), 0);
  const wDur = V ? rowsA.reduce((s, p) => s + (p.modDur ?? 0) * (p.valueMn ?? 0), 0) / V : 0;
  const wConv = V ? rowsA.reduce((s, p) => s + (p.convexity ?? 0) * (p.valueMn ?? 0), 0) / V : 0;
  const wam = V ? rowsA.reduce((s, p) => s + (p.years ?? 0) * (p.valueMn ?? 0), 0) / V : 0;
  const wYield = V ? rowsA.reduce((s, p) => s + (p.yieldPct ?? 0) * (p.valueMn ?? 0), 0) / V : 0;
  const carryRollAnn = V ? carryRoll / V / horizon * 100 : 0;
  const breakeven = dv01 ? carryRoll / dv01 : 0;   // bp over horizon

  // parallel-shift scenarios (full reprice)
  const scenarioValue = (bp: number) => rowsA.reduce((s, p) => {
    if (p.yieldPct == null || p.years == null || p.years <= 0) return s + (p.valueMn ?? 0);
    const sec = securityByIsin.get(p.isin); const sy = secByIsin.get(p.isin);
    const maturity = sec?.maturity_date ? new Date(sec.maturity_date) : (sy?.maturity_date ? new Date(sy.maturity_date) : null);
    const issue = sec?.issue_date ? new Date(sec.issue_date) : (maturity ? addMonths(maturity, -120) : null);
    if (!maturity || !issue) return s + (p.valueMn ?? 0);
    const pr = priceBond(p.couponPct, freqOf(sec?.security_type), maturity, issue, p.yieldPct + bp / 100, today).dirty;
    return s + p.faceMn * pr / 100;
  }, 0);

  const kfmt = (n: number, dp = 1) => (n >= 0 ? "+" : "") + fmtNum(n, dp);

  return (
    <div className="grid12">
      <Panel title="Holdings" sub="type ISIN or name → Tab to fill · face value (mn) · cost price /100 (optional, for P&L)" span={12}
        right={<button className="exp-chip" onClick={() => setBulk((b) => !b)} style={{ fontSize: 11 }}>{bulk ? "row editor" : "paste bulk"}</button>}>
        {bulk ? <BulkPaste onLoad={loadBulk} /> : (
          <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
            <div style={{ display: "grid", gridTemplateColumns: "minmax(220px,1fr) 120px 120px 34px", gap: 8, fontSize: 10.5, color: "var(--fg-mute)", textTransform: "uppercase", letterSpacing: ".3px" }}>
              <span>ISIN / name</span><span>Face (mn)</span><span>Cost px /100</span><span />
            </div>
            {rows.map((h, i) => (
              <div key={i} style={{ display: "grid", gridTemplateColumns: "minmax(220px,1fr) 120px 120px 34px", gap: 8, alignItems: "center" }}>
                <IsinInput value={h.isin} onChange={(isin) => setRow(i, { isin })} securities={securities} />
                <input type="number" value={h.faceMn || ""} min={0} step="any" placeholder="face" onChange={(e) => setRow(i, { faceMn: parseFloat(e.target.value) || 0 })} style={inputStyle} />
                <input type="number" value={h.costPx ?? ""} step="any" placeholder="optional" onChange={(e) => setRow(i, { costPx: e.target.value === "" ? null : parseFloat(e.target.value) })} style={inputStyle} />
                <button onClick={() => delRow(i)} title="remove" style={{ ...inputStyle, width: 34, padding: 0, cursor: "pointer", color: "var(--fg-mute)" }}>✕</button>
              </div>
            ))}
            <button onClick={addRow} className="exp-chip" style={{ alignSelf: "flex-start", marginTop: 3 }}>+ add holding</button>
          </div>
        )}
        <div style={{ display: "flex", gap: 18, alignItems: "center", flexWrap: "wrap", marginTop: 12 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 12, color: "var(--fg-dim)" }}>Funding rate</span>
            <input type="number" value={funding} step="any" onChange={(e) => setFunding(parseFloat(e.target.value) || 0)} style={{ ...inputStyle, width: 80 }} />
            <span style={{ fontSize: 11.5, color: "var(--fg-mute)" }}>% · cost of carry (default BB repo)</span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ fontSize: 12, color: "var(--fg-dim)" }}>Horizon</span>
            <div style={{ display: "inline-flex", gap: 2, background: "var(--panel)", border: "1px solid var(--border)", borderRadius: 8, padding: 2 }}>
              {HORIZONS.map((hz) => <button key={hz.label} onClick={() => setHorizon(hz.y)} className={"seg-b" + (Math.abs(horizon - hz.y) < 1e-6 ? " on" : "")}>{hz.label}</button>)}
            </div>
          </div>
        </div>
      </Panel>

      {rowsA.length > 0 && (
        <>
          {/* P&L block */}
          <div className="kpi-strip" style={{ gridColumn: "span 12", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))" }}>
            <div className="kpi"><div className="kpi-top"><span className="kpi-label">Dirty Market Value</span></div><div className="kpi-val"><span className="kpi-num" style={{ color: "var(--accent)" }}>{fmtNum(V)}</span><span className="kpi-unit">mn</span></div><div className="kpi-sub">incl. accrued · settlement value</div></div>
            {hasCost && <div className="kpi"><div className="kpi-top"><span className="kpi-label">Cost Basis</span></div><div className="kpi-val"><span className="kpi-num">{fmtNum(cost)}</span><span className="kpi-unit">mn</span></div></div>}
            {hasCost && <div className="kpi"><div className="kpi-top"><span className="kpi-label">Unrealized P&amp;L</span></div><div className="kpi-val"><span className="kpi-num" style={{ color: pnl >= 0 ? "var(--pos)" : "var(--neg)" }}>{kfmt(pnl)}</span><span className="kpi-unit">mn</span></div><div className="kpi-sub">{cost ? kfmt(pnl / cost * 100, 2) + "%" : ""}</div></div>}
            <div className="kpi"><div className="kpi-top"><span className="kpi-label">Wtd Avg Yield</span></div><div className="kpi-val"><span className="kpi-num" style={{ color: "var(--info)" }}>{fmtPct(wYield)}</span></div><div className="kpi-sub">face {fmtNum(face)} mn</div></div>
          </div>

          {/* Risk + carry block */}
          <div className="kpi-strip" style={{ gridColumn: "span 12", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))" }}>
            <div className="kpi"><div className="kpi-top"><span className="kpi-label">Mod Duration</span></div><div className="kpi-val"><span className="kpi-num">{wDur.toFixed(2)}</span><span className="kpi-unit">yr</span></div></div>
            <div className="kpi"><div className="kpi-top"><span className="kpi-label">Portfolio DV01</span></div><div className="kpi-val"><span className="kpi-num">{fmtNum(dv01, 3)}</span><span className="kpi-unit">mn/bp</span></div></div>
            <div className="kpi"><div className="kpi-top"><span className="kpi-label">Convexity</span></div><div className="kpi-val"><span className="kpi-num">{wConv.toFixed(1)}</span></div></div>
            <div className="kpi"><div className="kpi-top"><span className="kpi-label">WAM</span></div><div className="kpi-val"><span className="kpi-num">{wam.toFixed(2)}</span><span className="kpi-unit">yr</span></div></div>
            <div className="kpi"><div className="kpi-top"><span className="kpi-label">Carry + Roll</span></div><div className="kpi-val"><span className="kpi-num" style={{ color: carryRoll >= 0 ? "var(--pos)" : "var(--neg)" }}>{kfmt(carryRoll)}</span><span className="kpi-unit">mn</span></div><div className="kpi-sub">{kfmt(carryRollAnn, 2)}%/yr · breakeven {kfmt(breakeven, 0)}bp</div></div>
          </div>

          {/* Scenario strip — parallel bp shifts */}
          <Panel title="Rate Scenarios" sub="portfolio value & P&L under parallel yield shifts (full reprice)" span={12} pad={false}>
            <div className="table-wrap">
              <table className="dt">
                <thead><tr><th>Shift</th>{SHIFTS.slice(0, 3).map((b) => <th key={b} className="r">{b}bp</th>)}<th className="r">0</th>{SHIFTS.slice(3).map((b) => <th key={b} className="r">+{b}bp</th>)}</tr></thead>
                <tbody>
                  <tr><td>Value (mn)</td>{SHIFTS.slice(0, 3).map((b) => <td key={b} className="r mono">{fmtNum(scenarioValue(b))}</td>)}<td className="r mono" style={{ color: "var(--accent)" }}>{fmtNum(V)}</td>{SHIFTS.slice(3).map((b) => <td key={b} className="r mono">{fmtNum(scenarioValue(b))}</td>)}</tr>
                  <tr><td>Δ P&amp;L (mn)</td>{SHIFTS.slice(0, 3).map((b) => { const d = scenarioValue(b) - V; return <td key={b} className="r mono" style={{ color: d >= 0 ? "var(--pos)" : "var(--neg)" }}>{kfmt(d, 2)}</td>; })}<td className="r mono">—</td>{SHIFTS.slice(3).map((b) => { const d = scenarioValue(b) - V; return <td key={b} className="r mono" style={{ color: d >= 0 ? "var(--pos)" : "var(--neg)" }}>{kfmt(d, 2)}</td>; })}</tr>
                </tbody>
              </table>
            </div>
          </Panel>

          {/* Blotter */}
          <Panel title="Position Blotter" sub="per-holding valuation, risk & relative value" span={12} pad={false}>
            <div className="table-wrap">
              <table className="dt">
                <thead><tr>
                  <th>ISIN</th><th>Name</th><th className="r">Rem yr</th><th className="r">Cpn</th><th className="r">Yield</th>
                  <th className="r">Dirty</th><th className="r">Accr</th><th className="r">Value</th>
                  {hasCost && <th className="r">P&amp;L</th>}
                  <th className="r">ModDur</th><th className="r">DV01</th><th className="r">Cvx</th><th className="r">Carry+Roll</th><th>Src</th>
                </tr></thead>
                <tbody>
                  {rowsA.map((p, i) => (
                    <tr key={i}>
                      <td className="mono">{p.isin}</td>
                      <td style={{ maxWidth: 150, overflow: "hidden", textOverflow: "ellipsis" }}>{p.name}</td>
                      <td className="r mono">{p.years != null ? p.years.toFixed(2) : "—"}</td>
                      <td className="r mono">{p.couponPct != null ? fmtPct(p.couponPct) : "—"}</td>
                      <td className="r mono" style={{ color: "var(--info)" }}>{fmtPct(p.yieldPct, 3)}</td>
                      <td className="r mono">{p.dirty != null ? p.dirty.toFixed(3) : "—"}</td>
                      <td className="r mono" style={{ color: "var(--fg-mute)" }}>{p.accrued != null ? p.accrued.toFixed(3) : "—"}</td>
                      <td className="r mono" style={{ color: "var(--accent)" }}>{p.valueMn != null ? fmtNum(p.valueMn) : "—"}</td>
                      {hasCost && <td className="r mono" style={{ color: p.pnlMn == null ? "var(--fg-mute)" : p.pnlMn >= 0 ? "var(--pos)" : "var(--neg)" }}>{p.pnlMn != null ? kfmt(p.pnlMn, 2) : "—"}</td>}
                      <td className="r mono">{p.modDur != null ? p.modDur.toFixed(2) : "—"}</td>
                      <td className="r mono">{p.dv01Mn != null ? fmtNum(p.dv01Mn, 3) : "—"}</td>
                      <td className="r mono">{p.convexity != null ? p.convexity.toFixed(1) : "—"}</td>
                      <td className="r mono" style={{ color: p.carryRollMn == null ? "var(--fg-mute)" : p.carryRollMn >= 0 ? "var(--pos)" : "var(--neg)" }}>{p.carryRollMn != null ? kfmt(p.carryRollMn, 2) : "—"}</td>
                      <td>{p.source === "exact" ? <span className="pos">exact</span> : p.source === "interp" ? <span style={{ color: "var(--warn)" }}>interp</span> : <span className="neg">no match</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p style={{ fontSize: 11, color: "var(--fg-mute)", padding: "10px 14px", lineHeight: 1.6 }}>
              Cashflow-priced (semi-annual for BGTBs, discount for bills) off the latest GSOM secondary curve. Carry+Roll over {HORIZONS.find((h) => Math.abs(h.y - horizon) < 1e-6)?.label} = coupon − funding + roll-down on an unchanged curve; breakeven = bp of parallel sell-off that offsets it. Approximation for analysis, not a settlement confirmation.
            </p>
          </Panel>
        </>
      )}
    </div>
  );
}

function BulkPaste({ onLoad }: { onLoad: (t: string) => void }) {
  const [t, setT] = useState("");
  return (
    <div>
      <textarea value={t} onChange={(e) => setT(e.target.value)} rows={5} spellCheck={false}
        placeholder={"BD0123456789  500  99.20\nBD0987654321, 1200"}
        style={{ width: "100%", borderRadius: 8, background: "var(--bg-elev)", border: "1px solid var(--border)", padding: 12, fontFamily: "var(--mono)", fontSize: 13, color: "var(--fg)", outline: "none", resize: "vertical" }} />
      <p style={{ fontSize: 11, color: "var(--fg-mute)", margin: "6px 0 0" }}>one per line: ISIN  face(mn)  [cost px]</p>
      <button className="exp-chip" onClick={() => onLoad(t)} style={{ marginTop: 8 }}>load into rows</button>
    </div>
  );
}
