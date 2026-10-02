"use client";
import Link from "next/link";
import { useState } from "react";
import type { LiquidityForecastDay } from "@/lib/api";
import { fmtCrore, fmtDate } from "@/lib/format";

/**
 * The day-by-day ladder, with each row expandable into the OMO products behind
 * it. The expansion uses `omo_items`, which the ladder payload already carries,
 * so opening a row costs no request.
 *
 * The split shown is by LIQUIDITY EFFECT, not by the instrument's original
 * direction, because at maturity they are opposite: an SDF (absorption)
 * maturing returns cash to banks, while a repo maturing takes cash out. Showing
 * "repo" under injections on its maturity date would invert the reading.
 */
export default function LadderTable({ days, omoDataFrom, auctionHorizon }: {
  days: LiquidityForecastDay[];
  omoDataFrom?: string | null;
  auctionHorizon?: string | null;
}) {
  const [open, setOpen] = useState<Set<string>>(new Set());
  const toggle = (d: string) => setOpen((prev) => {
    const next = new Set(prev);
    if (next.has(d)) next.delete(d); else next.add(d);
    return next;
  });

  if (!days.length) {
    return (
      <div style={{ padding: 16, textAlign: "center", color: "var(--fg-mute)", fontSize: 12.5 }}>
        No dated flows in this window.
      </div>
    );
  }

  const num = (v: number, cls: string) =>
    <td className={`r mono ${cls}`}>{v ? fmtCrore(v) : ""}</td>;

  return (
    <div className="table-wrap" style={{ maxHeight: 560, overflowY: "auto" }}>
      <table className="dt">
        <thead><tr>
          <th style={{ width: 18 }}></th>
          <th>Date</th><th>Day</th>
          <th className="r">OMO Return (cr)</th><th className="r">OMO Repay (cr)</th>
          <th className="r">Govt Inflow (cr)</th><th className="r">Auction Out (cr)</th>
          <th className="r">Net (cr)</th><th className="r">Cumulative (cr)</th>
          <th>Detail</th>
        </tr></thead>
        <tbody>
          {days.map((d) => {
            const isOpen = open.has(d.date);
            const blindOmo = !d.omo_known;
            const blindAuction = auctionHorizon != null && d.date > auctionHorizon;
            const inflows = d.omo_items.filter((i) => i.liquidity_effect === "INFLOW");
            const outflows = d.omo_items.filter((i) => i.liquidity_effect === "OUTFLOW");
            return (
              <>
                <tr key={d.date} onClick={() => d.omo_items.length && toggle(d.date)}
                    style={{ cursor: d.omo_items.length ? "pointer" : "default",
                             background: isOpen ? "var(--accent-soft)" : undefined }}>
                  <td style={{ color: "var(--fg-mute)", fontSize: 10 }}>
                    {d.omo_items.length ? (isOpen ? "▾" : "▸") : ""}
                  </td>
                  <td>
                    <Link href={`/drilldown?date=${d.date}`} style={{ color: "var(--accent)" }}
                          onClick={(e) => e.stopPropagation()}>
                      {fmtDate(d.date)}
                    </Link>
                  </td>
                  <td>{d.weekday}</td>
                  {blindOmo
                    ? <td className="r" colSpan={2} style={{ color: "var(--info)", fontSize: 10.5 }}
                          title="No OMO data this far back — absent, not zero">no OMO data</td>
                    : <>{num(d.omo_return_crore, "pos")}{num(d.omo_repay_crore, "neg")}</>}
                  {num(d.govt_inflow_crore, "pos")}
                  {blindAuction
                    ? <td className="r" style={{ color: "var(--info)", fontSize: 10.5 }}
                          title="BB has not published an auction calendar this far out">not pub.</td>
                    : num(d.auction_out_crore, "neg")}
                  <td className="r mono" style={{ fontWeight: 600,
                        color: d.net_crore < 0 ? "var(--warn)" : "var(--info)" }}>
                    {fmtCrore(d.net_crore)}
                  </td>
                  <td className="r mono" style={{ fontWeight: 600 }}>{fmtCrore(d.cum_net_crore)}</td>
                  <td style={{ fontSize: 11, color: "var(--fg-mute)" }}>
                    {d.omo_items.length
                      ? `${d.omo_items.length} OMO line${d.omo_items.length === 1 ? "" : "s"}`
                      : blindOmo ? "" : "—"}
                  </td>
                </tr>
                {isOpen && (
                  <tr key={`${d.date}-x`}>
                    <td colSpan={10} style={{ background: "var(--bg-elev)", padding: "10px 14px" }}>
                      <div style={{ display: "flex", gap: 28, flexWrap: "wrap", fontSize: 12 }}>
                        <Side title="Injecting — cash returns to banks" items={inflows}
                              tone="var(--pos)"
                              note="absorption (SDF) maturing: BB pays the deposit back" />
                        <Side title="Draining — banks repay BB" items={outflows}
                              tone="var(--neg)"
                              note="injection (repo/AR/IBLF…) maturing: the bank settles up" />
                        <div>
                          <div style={{ fontSize: 10.5, textTransform: "uppercase",
                                        letterSpacing: ".1em", color: "var(--fg-dim)" }}>
                            Day net
                          </div>
                          <div style={{ marginTop: 5, lineHeight: 1.8 }}>
                            <div>OMO net <b style={{ color: d.omo_net_crore < 0 ? "var(--neg)" : "var(--pos)" }}>
                              {fmtCrore(d.omo_net_crore)}</b> cr</div>
                            <div>Govt inflow <b style={{ color: "var(--pos)" }}>
                              {fmtCrore(d.govt_inflow_crore)}</b> cr</div>
                            <div>Auction out <b style={{ color: "var(--neg)" }}>
                              {fmtCrore(d.auction_out_crore)}</b> cr</div>
                            <div style={{ borderTop: "1px solid var(--border)", marginTop: 4,
                                          paddingTop: 4 }}>
                              Total <b style={{ color: d.net_crore < 0 ? "var(--warn)" : "var(--info)" }}>
                                {fmtCrore(d.net_crore)}</b> cr
                            </div>
                          </div>
                          <Link href={`/drilldown?date=${d.date}`}
                                style={{ color: "var(--accent)", fontSize: 11.5 }}>
                            Full day detail — auctions by tenor, coupons by security →
                          </Link>
                        </div>
                      </div>
                    </td>
                  </tr>
                )}
              </>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Side({ title, items, tone, note }: {
  title: string; tone: string; note: string;
  items: { instrument: string; crore: number }[];
}) {
  const total = items.reduce((s, i) => s + i.crore, 0);
  return (
    <div style={{ minWidth: 210 }}>
      <div style={{ fontSize: 10.5, textTransform: "uppercase", letterSpacing: ".1em",
                    color: "var(--fg-dim)" }}>{title}</div>
      {items.length === 0
        ? <div style={{ marginTop: 5, color: "var(--fg-mute)" }}>none</div>
        : <div style={{ marginTop: 5, lineHeight: 1.8 }}>
            {items.map((i) => (
              <div key={i.instrument} style={{ display: "flex", justifyContent: "space-between",
                                               gap: 16 }}>
                <span>{i.instrument}</span>
                <span className="mono" style={{ color: tone }}>{fmtCrore(i.crore)}</span>
              </div>
            ))}
            <div style={{ display: "flex", justifyContent: "space-between", gap: 16,
                          borderTop: "1px solid var(--border)", marginTop: 4, paddingTop: 4,
                          fontWeight: 600 }}>
              <span>Total</span><span className="mono" style={{ color: tone }}>{fmtCrore(total)}</span>
            </div>
          </div>}
      <div style={{ marginTop: 5, fontSize: 10.5, color: "var(--fg-mute)", maxWidth: 210 }}>{note}</div>
    </div>
  );
}
