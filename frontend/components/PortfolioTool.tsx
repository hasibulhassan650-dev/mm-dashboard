"use client";
import { useMemo, useState, useRef, useEffect } from "react";
import { SecondaryYieldRow, Security } from "@/lib/api";
import { fmtNum, fmtPct } from "@/lib/format";
import { Panel } from "@/components/terminal/ui";

interface Props { secondary: SecondaryYieldRow[]; securities: Security[] }
interface Holding { isin: string; faceMn: number }
interface Priced {
  isin: string; name: string; type: string;
  years: number | null; couponPct: number | null;
  yieldPct: number | null; source: "exact" | "interp" | "none";
  price: number | null; faceMn: number; valueMn: number | null;
  modDur: number | null; dv01Mn: number | null;
}

const DAY = 86400000;

/** Clean price per 100 face: annual coupons, redemption at par. Fractional years ok. */
function pricePer100(couponPct: number | null, years: number, yieldPct: number): number {
  const y = yieldPct / 100;
  const c = couponPct ?? 0;
  if (y <= 0) return 100 + c * years;
  const disc = Math.pow(1 + y, -years);
  if (c === 0) return 100 * disc;
  return c * (1 - disc) / y + 100 * disc;
}
/** Modified duration (years) via a 1bp central difference. */
function modDuration(couponPct: number | null, years: number, yieldPct: number): number | null {
  if (years <= 0) return null;
  const p0 = pricePer100(couponPct, years, yieldPct);
  if (p0 <= 0) return null;
  const pu = pricePer100(couponPct, years, yieldPct + 0.01);
  const pd = pricePer100(couponPct, years, yieldPct - 0.01);
  return (pd - pu) / (2 * 0.0001 * p0);
}
function interpYield(points: { x: number; y: number }[], t: number): number | null {
  if (points.length === 0) return null;
  if (t <= points[0].x) return points[0].y;
  const last = points[points.length - 1];
  if (t >= last.x) return last.y;
  for (let i = 1; i < points.length; i++) {
    if (t <= points[i].x) {
      const a = points[i - 1], b = points[i];
      return a.y + (t - a.x) / (b.x - a.x) * (b.y - a.y);
    }
  }
  return last.y;
}

// ── ISIN autocomplete: type ISIN or name, ↑/↓ to move, Tab/Enter to fill ──────
function IsinInput({ value, onChange, securities }: {
  value: string; onChange: (isin: string) => void; securities: Security[];
}) {
  const [open, setOpen] = useState(false);
  const [hi, setHi] = useState(0);
  const boxRef = useRef<HTMLDivElement>(null);

  const matches = useMemo(() => {
    const s = value.trim().toUpperCase();
    if (!s) return [];
    const exact = securities.find((x) => x.isin.toUpperCase() === s);
    if (exact && exact.isin.toUpperCase() === s) return [];   // already a full match — no dropdown
    return securities
      .filter((x) => x.isin.toUpperCase().includes(s) || (x.security_name_norm || "").toUpperCase().includes(s))
      .slice(0, 8);
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
    else if (e.key === "Tab" && open) { onChange(matches[hi].isin); setOpen(false); }   // fill AND let focus move on
    else if (e.key === "Escape") { setOpen(false); }
  };

  return (
    <div ref={boxRef} style={{ position: "relative" }}>
      <input
        value={value}
        onChange={(e) => { onChange(e.target.value.toUpperCase()); setOpen(true); setHi(0); }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKey}
        placeholder="type ISIN or name…"
        spellCheck={false}
        style={{ width: "100%", background: "var(--bg-elev)", border: "1px solid var(--border)", borderRadius: 7, padding: "7px 9px", fontFamily: "var(--mono)", fontSize: 12.5, color: "var(--fg)", outline: "none" }}
      />
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

export default function PortfolioTool({ secondary, securities }: Props) {
  const [rows, setRows] = useState<Holding[]>([{ isin: "", faceMn: 0 }]);
  const [mode, setMode] = useState<"value" | "trade">("value");
  const [lowPct, setLowPct] = useState(5);
  const [highPct, setHighPct] = useState(5);
  const [nowMs] = useState(() => Date.now());
  const [bulk, setBulk] = useState(false);

  const secByIsin = useMemo(() => new Map(secondary.map((s) => [s.isin, s])), [secondary]);
  const securityByIsin = useMemo(() => new Map(securities.map((s) => [s.isin, s])), [securities]);
  const curve = useMemo(() => secondary
    .filter((s) => s.remaining_years != null && s.market_yield_pct != null)
    .map((s) => ({ x: s.remaining_years, y: s.market_yield_pct })).sort((a, b) => a.x - b.x), [secondary]);

  const setRow = (i: number, patch: Partial<Holding>) => setRows((r) => r.map((x, j) => j === i ? { ...x, ...patch } : x));
  const addRow = () => setRows((r) => [...r, { isin: "", faceMn: 0 }]);
  const delRow = (i: number) => setRows((r) => r.length > 1 ? r.filter((_, j) => j !== i) : [{ isin: "", faceMn: 0 }]);
  const loadBulk = (text: string) => {
    const out: Holding[] = [];
    for (const line of text.split("\n")) {
      const p = line.trim().split(/[,\s]+/);
      if (p.length < 2) continue;
      const f = parseFloat(p[1].replace(/,/g, ""));
      if (!Number.isNaN(f)) out.push({ isin: p[0].toUpperCase(), faceMn: f });
    }
    if (out.length) { setRows(out); setBulk(false); }
  };

  const holdings = rows.filter((h) => h.isin && h.faceMn > 0);

  const priced: Priced[] = useMemo(() => holdings.map((h) => {
    const sec = securityByIsin.get(h.isin), sy = secByIsin.get(h.isin);
    const name = sec?.security_name_norm ?? sy?.security_name_norm ?? h.isin;
    const type = sec?.security_type ?? sy?.security_type ?? "—";
    const couponPct = sec?.coupon_rate_pct ?? null;
    let years: number | null = sy?.remaining_years ?? null;
    if (years == null && sec?.maturity_date) years = (new Date(sec.maturity_date).getTime() - nowMs) / (365.25 * DAY);
    let yieldPct: number | null = null; let source: Priced["source"] = "none";
    if (sy?.market_yield_pct != null) { yieldPct = sy.market_yield_pct; source = "exact"; }
    else if (years != null) { yieldPct = interpYield(curve, years); if (yieldPct != null) source = "interp"; }
    const price = (years != null && years > 0 && yieldPct != null) ? pricePer100(couponPct, years, yieldPct) : null;
    const valueMn = price != null ? h.faceMn * price / 100 : null;
    const modDur = (years != null && years > 0 && yieldPct != null) ? modDuration(couponPct, years, yieldPct) : null;
    const dv01Mn = (modDur != null && valueMn != null) ? modDur * valueMn * 0.0001 : null;
    return { isin: h.isin, name, type, years, couponPct, yieldPct, source, price, faceMn: h.faceMn, valueMn, modDur, dv01Mn };
  }), [holdings, secByIsin, securityByIsin, curve, nowMs]);

  const totalFace = priced.reduce((s, p) => s + p.faceMn, 0);
  const totalValue = priced.reduce((s, p) => s + (p.valueMn ?? 0), 0);
  const valued = priced.filter((p) => p.valueMn != null && p.yieldPct != null);
  const wYield = valued.reduce((s, p) => s + p.yieldPct! * p.valueMn!, 0) / (totalValue || 1);
  const totalDv01 = priced.reduce((s, p) => s + (p.dv01Mn ?? 0), 0);

  // value of a holding at an arbitrary yield
  const valueAt = (p: Priced, y: number) => (p.years != null && p.years > 0) ? p.faceMn * pricePer100(p.couponPct, p.years, y) / 100 : (p.valueMn ?? 0);
  const scen = (offsetPct: number, sign: 1 | -1) => priced.map((p) => {
    if (p.yieldPct == null) return { p, y: null as number | null, value: p.valueMn ?? 0, delta: 0 };
    const y = p.yieldPct * (1 + sign * offsetPct / 100);
    const value = valueAt(p, y);
    return { p, y, value, delta: value - (p.valueMn ?? 0) };
  });
  const lowScen = scen(lowPct, -1);   // lower yield → richer price
  const highScen = scen(highPct, 1);  // higher yield → cheaper price
  const lowTotal = lowScen.reduce((s, x) => s + x.value, 0);
  const highTotal = highScen.reduce((s, x) => s + x.value, 0);

  return (
    <div className="grid12">
      <Panel title="Holdings" sub="pick each ISIN (type → Tab to fill) and enter face value in mn" span={12}
        right={<button className="exp-chip" onClick={() => setBulk((b) => !b)} style={{ fontSize: 11 }}>{bulk ? "row editor" : "paste bulk"}</button>}>
        {bulk ? (
          <BulkPaste onLoad={loadBulk} />
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
            {rows.map((h, i) => (
              <div key={i} style={{ display: "grid", gridTemplateColumns: "minmax(240px,1fr) 140px 34px", gap: 8, alignItems: "center" }}>
                <IsinInput value={h.isin} onChange={(isin) => setRow(i, { isin })} securities={securities} />
                <input type="number" value={h.faceMn || ""} min={0} step="any" placeholder="face (mn)"
                  onChange={(e) => setRow(i, { faceMn: parseFloat(e.target.value) || 0 })} style={inputStyle} />
                <button onClick={() => delRow(i)} title="remove" style={{ ...inputStyle, width: 34, padding: 0, cursor: "pointer", color: "var(--fg-mute)" }}>✕</button>
              </div>
            ))}
            <button onClick={addRow} className="exp-chip" style={{ alignSelf: "flex-start", marginTop: 3 }}>+ add holding</button>
          </div>
        )}
        <p style={{ fontSize: 11.5, color: "var(--fg-mute)", margin: "10px 0 0", lineHeight: 1.6 }}>
          Valued against the latest GSOM secondary curve (exact ISIN where available, else interpolated by remaining
          maturity). Clean price, annual coupons, par redemption — an approximation, not a settlement price.
        </p>
      </Panel>

      {priced.length > 0 && (
        <>
          <Panel span={12} className="no-head">
            <div style={{ display: "inline-flex", gap: 2, background: "var(--panel)", border: "1px solid var(--border)", borderRadius: 9, padding: 3 }}>
              {(["value", "trade"] as const).map((m) => (
                <button key={m} onClick={() => setMode(m)} className={"seg-b" + (mode === m ? " on" : "")} style={{ padding: "6px 16px" }}>
                  {m === "value" ? "Market Value" : "Trade Planner"}
                </button>
              ))}
            </div>
          </Panel>

          {mode === "value" ? (
            <>
              <div className="kpi-strip four">
                <div className="kpi"><div className="kpi-top"><span className="kpi-label">Total Face</span></div><div className="kpi-val"><span className="kpi-num">{fmtNum(totalFace)}</span><span className="kpi-unit">mn</span></div></div>
                <div className="kpi"><div className="kpi-top"><span className="kpi-label">Market Value (MTM)</span></div><div className="kpi-val"><span className="kpi-num" style={{ color: "var(--accent)" }}>{fmtNum(totalValue)}</span><span className="kpi-unit">mn</span></div></div>
                <div className="kpi"><div className="kpi-top"><span className="kpi-label">Wtd Avg Yield</span></div><div className="kpi-val"><span className="kpi-num" style={{ color: "var(--info)" }}>{valued.length ? fmtPct(wYield) : "—"}</span></div></div>
                <div className="kpi"><div className="kpi-top"><span className="kpi-label">Portfolio DV01</span></div><div className="kpi-val"><span className="kpi-num">{fmtNum(totalDv01, 3)}</span><span className="kpi-unit">mn/bp</span></div><div className="kpi-sub">value change per 1bp</div></div>
              </div>
              <Panel title="Valuation Detail" sub="per-holding mark-to-market + risk" span={12} pad={false}>
                <div className="table-wrap">
                  <table className="dt">
                    <thead><tr>
                      <th>ISIN</th><th>Name</th><th>Type</th><th className="r">Rem (yr)</th><th className="r">Coupon</th>
                      <th className="r">Mkt Yield</th><th className="r">Price</th><th className="r">Face (mn)</th>
                      <th className="r">Value (mn)</th><th className="r">Mod Dur</th><th className="r">DV01 (mn)</th><th>Source</th>
                    </tr></thead>
                    <tbody>
                      {priced.map((p, i) => (
                        <tr key={i}>
                          <td className="mono">{p.isin}</td>
                          <td style={{ maxWidth: 170, overflow: "hidden", textOverflow: "ellipsis" }}>{p.name}</td>
                          <td>{p.type}</td>
                          <td className="r mono">{p.years != null ? p.years.toFixed(2) : "—"}</td>
                          <td className="r mono">{p.couponPct != null ? fmtPct(p.couponPct) : "—"}</td>
                          <td className="r mono" style={{ color: "var(--info)" }}>{fmtPct(p.yieldPct, 4)}</td>
                          <td className="r mono">{p.price != null ? p.price.toFixed(3) : "—"}</td>
                          <td className="r mono">{fmtNum(p.faceMn)}</td>
                          <td className="r mono" style={{ color: "var(--accent)" }}>{p.valueMn != null ? fmtNum(p.valueMn) : "—"}</td>
                          <td className="r mono">{p.modDur != null ? p.modDur.toFixed(2) : "—"}</td>
                          <td className="r mono">{p.dv01Mn != null ? fmtNum(p.dv01Mn, 3) : "—"}</td>
                          <td>{p.source === "exact" ? <span className="pos">exact</span> : p.source === "interp" ? <span style={{ color: "var(--warn)" }}>interp</span> : <span className="neg">no match</span>}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Panel>
            </>
          ) : (
            <>
              <Panel span={12} className="no-head">
                <div style={{ display: "flex", gap: 20, flexWrap: "wrap", alignItems: "center" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{ fontSize: 12, color: "var(--fg-dim)" }}>Low target — yield</span>
                    <input type="number" value={lowPct} step="any" onChange={(e) => setLowPct(parseFloat(e.target.value) || 0)} style={{ ...inputStyle, width: 70 }} />
                    <span style={{ fontSize: 12, color: "var(--fg-mute)" }}>% below market</span>
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{ fontSize: 12, color: "var(--fg-dim)" }}>High target — yield</span>
                    <input type="number" value={highPct} step="any" onChange={(e) => setHighPct(parseFloat(e.target.value) || 0)} style={{ ...inputStyle, width: 70 }} />
                    <span style={{ fontSize: 12, color: "var(--fg-mute)" }}>% above market</span>
                  </div>
                </div>
              </Panel>
              <div className="kpi-strip" style={{ gridTemplateColumns: "repeat(3,1fr)" }}>
                <div className="kpi"><div className="kpi-top"><span className="kpi-label">Value @ Low Yield (−{lowPct}%)</span></div><div className="kpi-val"><span className="kpi-num pos">{fmtNum(lowTotal)}</span><span className="kpi-unit">mn</span></div><div className="kpi-sub">richer market · {fmtNum(lowTotal - totalValue, 1)} mn vs MTM</div></div>
                <div className="kpi"><div className="kpi-top"><span className="kpi-label">Market Value (MTM)</span></div><div className="kpi-val"><span className="kpi-num" style={{ color: "var(--accent)" }}>{fmtNum(totalValue)}</span><span className="kpi-unit">mn</span></div><div className="kpi-sub">at current market yield</div></div>
                <div className="kpi"><div className="kpi-top"><span className="kpi-label">Value @ High Yield (+{highPct}%)</span></div><div className="kpi-val"><span className="kpi-num neg">{fmtNum(highTotal)}</span><span className="kpi-unit">mn</span></div><div className="kpi-sub">cheaper market · {fmtNum(highTotal - totalValue, 1)} mn vs MTM</div></div>
              </div>
              <Panel title="Trade Planner" sub={`price & value at a yield you'd trade — ${lowPct}% below and ${highPct}% above the current market yield`} span={12} pad={false}>
                <div className="table-wrap">
                  <table className="dt">
                    <thead><tr>
                      <th>ISIN</th><th>Name</th><th className="r">Mkt Yield</th><th className="r">Mkt Value</th>
                      <th className="r">Low Yield</th><th className="r">Value</th><th className="r">Δ vs MTM</th>
                      <th className="r">High Yield</th><th className="r">Value</th><th className="r">Δ vs MTM</th>
                    </tr></thead>
                    <tbody>
                      {priced.map((p, i) => {
                        const lo = lowScen[i], hi = highScen[i];
                        return (
                          <tr key={i}>
                            <td className="mono">{p.isin}</td>
                            <td style={{ maxWidth: 150, overflow: "hidden", textOverflow: "ellipsis" }}>{p.name}</td>
                            <td className="r mono" style={{ color: "var(--info)" }}>{fmtPct(p.yieldPct, 4)}</td>
                            <td className="r mono">{p.valueMn != null ? fmtNum(p.valueMn) : "—"}</td>
                            <td className="r mono pos">{lo.y != null ? fmtPct(lo.y, 4) : "—"}</td>
                            <td className="r mono">{fmtNum(lo.value)}</td>
                            <td className="r mono pos">{lo.y != null ? "+" + fmtNum(lo.delta, 2) : "—"}</td>
                            <td className="r mono neg">{hi.y != null ? fmtPct(hi.y, 4) : "—"}</td>
                            <td className="r mono">{fmtNum(hi.value)}</td>
                            <td className="r mono neg">{hi.y != null ? fmtNum(hi.delta, 2) : "—"}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </Panel>
            </>
          )}
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
        placeholder={"BD0123456789  500\nBD0987654321, 1200"}
        style={{ width: "100%", borderRadius: 8, background: "var(--bg-elev)", border: "1px solid var(--border)", padding: 12, fontFamily: "var(--mono)", fontSize: 13, color: "var(--fg)", outline: "none", resize: "vertical" }} />
      <button className="exp-chip" onClick={() => onLoad(t)} style={{ marginTop: 8 }}>load into rows</button>
    </div>
  );
}
