"use client";
import { useMemo, useState } from "react";
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
}

const DAY = 86400000;

/** Standard clean price per 100 face: annual coupons, redemption at par.
 *  Zero/!coupon → pure discount (T-bill). years may be fractional. */
function pricePer100(couponPct: number | null, years: number, yieldPct: number): number {
  const y = yieldPct / 100;
  const c = couponPct ?? 0;
  if (y <= 0) return 100 + c * years;
  const disc = Math.pow(1 + y, -years);
  if (c === 0) return 100 * disc;
  return c * (1 - disc) / y + 100 * disc;
}

/** Linear interpolation of yield by remaining years across the secondary curve. */
function interpYield(points: { x: number; y: number }[], t: number): number | null {
  if (points.length === 0) return null;
  if (t <= points[0].x) return points[0].y;
  const last = points[points.length - 1];
  if (t >= last.x) return last.y;
  for (let i = 1; i < points.length; i++) {
    if (t <= points[i].x) {
      const a = points[i - 1], b = points[i];
      const w = (t - a.x) / (b.x - a.x);
      return a.y + w * (b.y - a.y);
    }
  }
  return last.y;
}

function parseHoldings(text: string): Holding[] {
  const out: Holding[] = [];
  for (const line of text.split("\n")) {
    const t = line.trim();
    if (!t) continue;
    const parts = t.split(/[,\s]+/);
    if (parts.length < 2) continue;
    const isin = parts[0].toUpperCase();
    const faceMn = parseFloat(parts[1].replace(/,/g, ""));
    if (!Number.isNaN(faceMn)) out.push({ isin, faceMn });
  }
  return out;
}

export default function PortfolioTool({ secondary, securities }: Props) {
  const [text, setText] = useState("");
  // Capture "now" once at mount (lazy initializer is pure for render purposes).
  const [nowMs] = useState(() => Date.now());

  const secByIsin = useMemo(() => new Map(secondary.map(s => [s.isin, s])), [secondary]);
  const securityByIsin = useMemo(() => new Map(securities.map(s => [s.isin, s])), [securities]);
  const curve = useMemo(
    () => secondary
      .filter(s => s.remaining_years != null && s.market_yield_pct != null)
      .map(s => ({ x: s.remaining_years, y: s.market_yield_pct }))
      .sort((a, b) => a.x - b.x),
    [secondary],
  );

  const holdings = useMemo(() => parseHoldings(text), [text]);

  const priced: Priced[] = useMemo(() => holdings.map(h => {
    const sec = securityByIsin.get(h.isin);
    const sy  = secByIsin.get(h.isin);
    const name = sec?.security_name_norm ?? sy?.security_name_norm ?? h.isin;
    const type = sec?.security_type ?? sy?.security_type ?? "—";
    const couponPct = sec?.coupon_rate_pct ?? null;

    let years: number | null = sy?.remaining_years ?? null;
    if (years == null && sec?.maturity_date) {
      years = (new Date(sec.maturity_date).getTime() - nowMs) / (365.25 * DAY);
    }

    let yieldPct: number | null = null;
    let source: Priced["source"] = "none";
    if (sy?.market_yield_pct != null) { yieldPct = sy.market_yield_pct; source = "exact"; }
    else if (years != null) { yieldPct = interpYield(curve, years); if (yieldPct != null) source = "interp"; }

    const price = (years != null && years > 0 && yieldPct != null)
      ? pricePer100(couponPct, years, yieldPct) : null;
    const valueMn = price != null ? h.faceMn * price / 100 : null;

    return { isin: h.isin, name, type, years, couponPct, yieldPct, source, price, faceMn: h.faceMn, valueMn };
  }), [holdings, secByIsin, securityByIsin, curve, nowMs]);

  // Portfolio aggregates
  const totalFace = priced.reduce((s, p) => s + p.faceMn, 0);
  const totalValue = priced.reduce((s, p) => s + (p.valueMn ?? 0), 0);
  const valuedRows = priced.filter(p => p.valueMn != null && p.yieldPct != null);
  const wYield = valuedRows.reduce((s, p) => s + p.yieldPct! * p.valueMn!, 0) / (totalValue || 1);

  // ±100bp parallel shift on valued rows
  function shiftedValue(bp: number): number {
    return priced.reduce((s, p) => {
      if (p.years == null || p.years <= 0 || p.yieldPct == null) return s + (p.valueMn ?? 0);
      const pr = pricePer100(p.couponPct, p.years, p.yieldPct + bp / 100);
      return s + p.faceMn * pr / 100;
    }, 0);
  }
  const up = shiftedValue(100), down = shiftedValue(-100);

  return (
    <div className="grid12">
      <Panel title="Holdings" sub="one per line: ISIN  faceValue(mn)" span={12}>
        <textarea
          value={text}
          onChange={e => setText(e.target.value)}
          rows={6}
          spellCheck={false}
          placeholder={"BD0123456789  500\nBD0987654321, 1200"}
          style={{
            width: "100%", borderRadius: 8, background: "var(--bg-elev)", border: "1px solid var(--border)",
            padding: 12, fontFamily: "var(--mono)", fontSize: 13, color: "var(--fg)", outline: "none", resize: "vertical",
          }}
        />
        <p style={{ fontSize: 11.5, color: "var(--fg-mute)", margin: "10px 0 0", lineHeight: 1.6 }}>
          Valued against the latest GSOM secondary curve: exact ISIN where available, otherwise interpolated by remaining
          maturity. Prices assume annual coupons and par redemption — an approximation, not a settlement price.
        </p>
      </Panel>

      {priced.length > 0 && (
        <>
          <div className="kpi-strip four">
            <div className="kpi"><div className="kpi-top"><span className="kpi-label">Total Face</span></div><div className="kpi-val"><span className="kpi-num">{fmtNum(totalFace)}</span><span className="kpi-unit">mn</span></div></div>
            <div className="kpi"><div className="kpi-top"><span className="kpi-label">Market Value (MTM)</span></div><div className="kpi-val"><span className="kpi-num" style={{ color: "var(--accent)" }}>{fmtNum(totalValue)}</span><span className="kpi-unit">mn</span></div></div>
            <div className="kpi"><div className="kpi-top"><span className="kpi-label">Wtd Avg Yield</span></div><div className="kpi-val"><span className="kpi-num" style={{ color: "var(--info)" }}>{valuedRows.length ? fmtPct(wYield) : "—"}</span></div></div>
            <div className="kpi">
              <div className="kpi-top"><span className="kpi-label">±100bp Impact</span></div>
              <div className="kpi-val" style={{ fontSize: 15 }}>
                <span className="mono neg">{fmtNum(up - totalValue, 1)}</span>
                <span style={{ color: "var(--fg-mute)", margin: "0 5px" }}>/</span>
                <span className="mono pos">+{fmtNum(down - totalValue, 1)}</span>
              </div>
              <div className="kpi-sub">mn · ±100bp parallel shift</div>
            </div>
          </div>

          <Panel title="Valuation Detail" sub="per-holding mark-to-market" span={12} pad={false}>
            <div className="table-wrap">
              <table className="dt">
                <thead>
                  <tr>
                    <th>ISIN</th><th>Name</th><th>Type</th>
                    <th className="r">Rem (yr)</th><th className="r">Coupon</th><th className="r">Mkt Yield</th>
                    <th className="r">Price</th><th className="r">Face (mn)</th><th className="r">Value (mn)</th><th>Source</th>
                  </tr>
                </thead>
                <tbody>
                  {priced.map((p, i) => (
                    <tr key={i}>
                      <td className="mono">{p.isin}</td>
                      <td style={{ maxWidth: 180, overflow: "hidden", textOverflow: "ellipsis" }}>{p.name}</td>
                      <td>{p.type}</td>
                      <td className="r mono">{p.years != null ? p.years.toFixed(2) : "—"}</td>
                      <td className="r mono">{p.couponPct != null ? fmtPct(p.couponPct) : "—"}</td>
                      <td className="r mono" style={{ color: "var(--info)" }}>{fmtPct(p.yieldPct, 4)}</td>
                      <td className="r mono">{p.price != null ? p.price.toFixed(3) : "—"}</td>
                      <td className="r mono">{fmtNum(p.faceMn)}</td>
                      <td className="r mono" style={{ color: "var(--accent)" }}>{p.valueMn != null ? fmtNum(p.valueMn) : "—"}</td>
                      <td>
                        {p.source === "exact" && <span className="pos">exact</span>}
                        {p.source === "interp" && <span style={{ color: "var(--warn)" }}>interp</span>}
                        {p.source === "none" && <span className="neg">no match</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
        </>
      )}
    </div>
  );
}
