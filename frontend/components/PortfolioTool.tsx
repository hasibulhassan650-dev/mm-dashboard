"use client";
import { useMemo, useState } from "react";
import { SecondaryYieldRow, Security } from "@/lib/api";
import { fmtNum, fmtPct } from "@/lib/format";

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
    <div className="space-y-6">
      <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
        <label className="text-sm font-medium text-gray-300 block mb-2">
          Holdings — one per line: <span className="font-mono text-gray-400">ISIN faceValue(mn)</span>
        </label>
        <textarea
          value={text}
          onChange={e => setText(e.target.value)}
          rows={6}
          spellCheck={false}
          placeholder={"BD0123456789  500\nBD0987654321, 1200"}
          className="w-full rounded-lg bg-gray-950 border border-gray-800 p-3 font-mono text-sm text-gray-200 placeholder-gray-600 focus:border-teal-700 focus:outline-none"
        />
        <p className="text-xs text-gray-500 mt-2">
          Valued against the latest GSOM secondary curve: exact ISIN where available, otherwise interpolated by remaining
          maturity. Prices assume annual coupons and par redemption — an approximation, not a settlement price.
        </p>
      </div>

      {priced.length > 0 && (
        <>
          {/* Summary */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <div className="rounded-lg border border-gray-800 bg-gray-900 p-3">
              <div className="text-xs text-gray-400 mb-1">Total Face</div>
              <div className="text-xl font-mono text-gray-200">{fmtNum(totalFace)} <span className="text-xs text-gray-500">mn</span></div>
            </div>
            <div className="rounded-lg border border-gray-800 bg-gray-900 p-3">
              <div className="text-xs text-gray-400 mb-1">Market Value (MTM)</div>
              <div className="text-xl font-mono text-teal-400">{fmtNum(totalValue)} <span className="text-xs text-gray-500">mn</span></div>
            </div>
            <div className="rounded-lg border border-gray-800 bg-gray-900 p-3">
              <div className="text-xs text-gray-400 mb-1">Wtd Avg Yield</div>
              <div className="text-xl font-mono text-sky-400">{valuedRows.length ? fmtPct(wYield) : "—"}</div>
            </div>
            <div className="rounded-lg border border-gray-800 bg-gray-900 p-3">
              <div className="text-xs text-gray-400 mb-1">±100bp Value Impact</div>
              <div className="text-sm font-mono">
                <span className="text-red-400">{fmtNum(up - totalValue, 1)}</span>
                <span className="text-gray-600 mx-1">/</span>
                <span className="text-green-400">+{fmtNum(down - totalValue, 1)}</span>
              </div>
              <div className="text-xs text-gray-500 mt-1">mn, on +/−100bp shift</div>
            </div>
          </div>

          {/* Detail table */}
          <div className="rounded-xl border border-gray-800 bg-gray-900 p-4 overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-gray-400 border-b border-gray-800">
                  <th className="pb-2 pr-4">ISIN</th>
                  <th className="pb-2 pr-4">Name</th>
                  <th className="pb-2 pr-4">Type</th>
                  <th className="pb-2 pr-4 text-right">Rem (yr)</th>
                  <th className="pb-2 pr-4 text-right">Coupon</th>
                  <th className="pb-2 pr-4 text-right">Mkt Yield</th>
                  <th className="pb-2 pr-4 text-right">Price</th>
                  <th className="pb-2 pr-4 text-right">Face (mn)</th>
                  <th className="pb-2 pr-4 text-right">Value (mn)</th>
                  <th className="pb-2 text-center">Source</th>
                </tr>
              </thead>
              <tbody>
                {priced.map((p, i) => (
                  <tr key={i} className="border-b border-gray-800/50">
                    <td className="py-1.5 pr-4 font-mono text-xs text-gray-300">{p.isin}</td>
                    <td className="py-1.5 pr-4 text-gray-300 max-w-[180px] truncate">{p.name}</td>
                    <td className="py-1.5 pr-4 text-gray-400 text-xs">{p.type}</td>
                    <td className="py-1.5 pr-4 text-right font-mono text-gray-300">{p.years != null ? p.years.toFixed(2) : "—"}</td>
                    <td className="py-1.5 pr-4 text-right font-mono text-gray-400">{p.couponPct != null ? fmtPct(p.couponPct) : "—"}</td>
                    <td className="py-1.5 pr-4 text-right font-mono text-sky-400">{fmtPct(p.yieldPct, 4)}</td>
                    <td className="py-1.5 pr-4 text-right font-mono text-gray-300">{p.price != null ? p.price.toFixed(3) : "—"}</td>
                    <td className="py-1.5 pr-4 text-right font-mono text-gray-300">{fmtNum(p.faceMn)}</td>
                    <td className="py-1.5 pr-4 text-right font-mono text-teal-400">{p.valueMn != null ? fmtNum(p.valueMn) : "—"}</td>
                    <td className="py-1.5 text-center text-xs">
                      {p.source === "exact"  && <span className="text-green-400">exact</span>}
                      {p.source === "interp" && <span className="text-amber-400">interp</span>}
                      {p.source === "none"   && <span className="text-red-400">no match</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
