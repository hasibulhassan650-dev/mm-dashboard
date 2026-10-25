import { api } from "@/lib/api";
import type { CumulativeBlock } from "@/lib/api";
import StatCard from "@/components/StatCard";
import DownloadButton from "@/components/DownloadButton";
import Link from "next/link";

export const revalidate = 60;

function prevWorkday(date: string): string {
  const d = new Date(date);
  do { d.setDate(d.getDate() - 1); } while (d.getDay() === 5 || d.getDay() === 6); // skip Fri+Sat
  return d.toISOString().slice(0, 10);
}
function nextWorkday(date: string): string {
  const d = new Date(date);
  do { d.setDate(d.getDate() + 1); } while (d.getDay() === 5 || d.getDay() === 6);
  return d.toISOString().slice(0, 10);
}

export default async function DrilldownPage({
  searchParams,
}: {
  searchParams: Promise<{ date?: string; cum_from?: string }>;
}) {
  const params = await searchParams;
  const date = params.date ?? new Date().toISOString().slice(0, 10);
  const cumFrom = params.cum_from && /^\d{4}-\d{2}-\d{2}$/.test(params.cum_from)
    && params.cum_from <= date ? params.cum_from : undefined;
  const data = await api.drilldown(date, cumFrom);
  const cum = data.cumulative;
  const s = data.summary;
  // Optional: an older deployed API has no liquidity block, and the page should
  // degrade rather than crash while the API catches up.
  const liq = data.liquidity;
  const omo = data.omo ?? [];
  // Optional on purpose: an older deployed API build omits it entirely.
  const buybacks = data.buybacks ?? [];
  // A buyback the desk entered that BB has not yet resulted: listed, never summed.
  const deskBuybacks = data.desk_buybacks ?? [];
  // Events the PRESS reported for this day. Context only - never in a total.
  const newsEvents = data.news_events ?? [];

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-4">
        <Link href={`/drilldown?date=${prevWorkday(date)}`}
          className="px-3 py-1.5 rounded b-panel2 hover-b-panel2 text-sm t-dim">◀ Prev</Link>
        <div>
          <h1 className="text-xl font-semibold t-fg">Date Drilldown — {date}</h1>
          <p className="text-sm t-dim">All cash flow events on this date</p>
        </div>
        <Link href={`/drilldown?date=${nextWorkday(date)}`}
          className="px-3 py-1.5 rounded b-panel2 hover-b-panel2 text-sm t-dim">Next ▶</Link>
        <Link href="/cashflows" className="ml-auto text-xs t-info hover:underline">← Back to Cash Flows</Link>
      </div>

      {/* The whole liquidity picture for the day, in the order cash moves.
          OMO first because it is the biggest mover and was, until now, missing
          from this page entirely -- the ladder's own drilldown showed every
          G-sec event and none of the operations driving the swings. */}
      {liq && (
        <div className="rounded-xl border bd b-panel p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-medium t-dim">Net Liquidity — {date} (BDT crore)</h2>
            <span className="text-xs t-mute">
              {liq.omo_known ? "" : "no OMO data this far back · "}
              {liq.auction_known ? "" : "auction calendar not published this far out"}
            </span>
          </div>
          <div className="flex flex-wrap items-stretch gap-3 text-xs">
            <Line label="OMO dealt — injected" hint="new repo / AR / IBLF struck today — cash out to banks"
                  value={liq.omo_new_inflow_crore} tone="pos" known={liq.omo_complete} />
            <Line label="OMO dealt — absorbed" hint="new SDF taken today — banks park cash at BB"
                  value={-liq.omo_new_outflow_crore} tone="neg" known={liq.omo_complete} />
            <Line label="OMO maturing — returns" hint="absorption (SDF) maturing — BB pays banks back"
                  value={liq.omo_inflow_crore} tone="pos" known={liq.omo_known} />
            <Line label="OMO maturing — repaid" hint="injection maturing — banks repay BB"
                  value={-liq.omo_outflow_crore} tone="neg" known={liq.omo_known} />
            <Line label="OMO NET" value={liq.omo_net_crore} tone="auto" known={liq.omo_known} strong />
            <Line label="Govt inflow" hint="coupons + principal + buyback + sukuk"
                  value={liq.govt_inflow_crore} tone="pos" known />
            <Line label="Auction outflow" hint="banks pay for new issuance"
                  value={-liq.auction_outflow_crore} tone="neg" known={liq.auction_known} />
            <Line label="TOTAL NET" value={liq.total_net_crore} tone="auto"
                  known={liq.omo_known} strong big />
          </div>
          {liq.omo_known && !liq.omo_complete && (
            <p className="t-warn" style={{ fontSize: 11, marginTop: 8, marginBottom: 0 }}>
              {liq.omo_coverage === "future"
                ? "BB has not acted on this date yet, so only the maturity legs are known — this is a roll-off figure, not a liquidity net."
                : "BB has not published this date's operations yet, so only the maturity legs are known — this is a roll-off figure, not a liquidity net. It will fill in."}
            </p>
          )}
        </div>
      )}


      {/* Cumulative from the same ladder as Cash Flows. A total over days whose
          OMO BB has not published is NOT shown as a number: the known part is
          shown instead, with the day it is known through. */}
      {cum && (
        <div className="rounded-xl border bd b-panel p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-medium t-dim">Cumulative net liquidity (BDT crore)</h2>
            <Link href="/cashflows" className="text-xs t-info hover:underline">Same ladder as Cash Flows →</Link>
          </div>
          <div className="flex flex-wrap items-stretch gap-3 text-xs">
            <CumLine label={`Month to date · ${cum.mtd.from} → ${cum.mtd.to}`} b={cum.mtd} />
            {cum.window && (
              <CumLine label={`From ${cum.window.from} → ${cum.window.to}`} b={cum.window} />
            )}
          </div>
          <form method="get" className="flex flex-wrap items-center gap-2 mt-3 text-xs t-dim">
            <input type="hidden" name="date" value={date} />
            <label htmlFor="cum_from">Accumulate from</label>
            <input id="cum_from" name="cum_from" type="date" defaultValue={cumFrom ?? ""} max={date}
                   className="px-2 py-1 rounded b-panel2 t-fg" />
            <button type="submit" className="px-3 py-1 rounded b-panel2 hover-b-panel2">Apply</button>
          </form>
        </div>
      )}

      <div className={`grid grid-cols-2 gap-4 ${(buybacks.length > 0) !== ((s.sukuk_inflow_mill ?? 0) > 0) ? "md:grid-cols-6" : (buybacks.length > 0 && (s.sukuk_inflow_mill ?? 0) > 0) ? "md:grid-cols-7" : "md:grid-cols-5"}`}>
        <StatCard label="Maturity Inflow" value={`${s.maturity_inflow_mill.toLocaleString()}`} sub="BDT million" color="green" />
        <StatCard label="Coupon Inflow"   value={`${s.coupon_inflow_mill.toLocaleString()}`}   sub="BDT million" color="green" />
        {buybacks.length > 0 && (
          <StatCard label="Buyback Inflow"
            value={`${(s.buyback_inflow_mill ?? 0).toLocaleString()}`}
            sub="early principal · BDT million" color="green" />
        )}
        {(s.sukuk_inflow_mill ?? 0) > 0 && (
          <StatCard label="Sukuk Inflow"
            value={`${(s.sukuk_inflow_mill ?? 0).toLocaleString()}`}
            sub="BGIS principal + profit · BDT million" color="green" />
        )}
        <StatCard label="Total Inflow"    value={`${s.total_inflow_mill.toLocaleString()}`}    sub="BDT million" color="blue" />
        <StatCard label="Auction Outflow" value={`${s.auction_outflow_mill.toLocaleString()}`} sub="BDT million" color="red" />
        <StatCard label="Net Borrowing"   value={`${s.net_borrowing_mill > 0 ? "▲" : "▼"} ${Math.abs(s.net_borrowing_mill).toLocaleString()}`}
          sub={s.net_borrowing_mill > 0 ? "net borrower" : "net repayer"}
          color={s.net_borrowing_mill > 0 ? "amber" : "green"} />
      </div>

      {newsEvents.length > 0 && (
        <div className="rounded-xl border bd b-panel p-4" style={{ borderColor: "var(--info)" }}>
          <h2 className="text-sm font-medium t-info mb-2">
            From the news — not Bangladesh Bank data, NOT in any total above ({newsEvents.length})
          </h2>
          {newsEvents.map((e) => (
            <div key={e.id} className="text-xs t-dim" style={{ lineHeight: 1.8 }}>
              <b className="t-fg">{e.kind.replace(/_/g, " ").toLowerCase()}</b> · {e.direction.toLowerCase()} ·{" "}
              {e.amount_crore == null ? "amount not stated" : <b className="mono">{e.amount_crore.toLocaleString()} cr</b>}
              {e.status ? ` · ${e.status.toLowerCase()}` : ""} · {e.counterparty ?? "counterparty not stated"} — {e.summary}{" "}
              {e.sources[0] && <a href={e.sources[0].url} target="_blank" rel="noreferrer" className="t-info">source</a>}
            </div>
          ))}
          <Link href="/liquidity-watch" className="text-xs t-info hover:underline">All liquidity news →</Link>
        </div>
      )}

      {deskBuybacks.length > 0 && (
        <div className="rounded-xl border bd b-panel p-4" style={{ borderColor: "var(--warn)" }}>
          <h2 className="text-sm font-medium t-warn mb-2">
            Desk buyback figure — provisional, NOT in any total above ({deskBuybacks.length})
          </h2>
          {deskBuybacks.map((b) => (
            <div key={b.isin} className="text-xs t-dim" style={{ lineHeight: 1.8 }}>
              <span className="mono t-fg">{b.isin}</span> · accepted face{" "}
              <b className="mono">{b.accepted_face_crore.toLocaleString(undefined, { minimumFractionDigits: 2 })}</b> cr
              {b.cash_paid_crore != null && <> · cash <b className="mono">{b.cash_paid_crore.toLocaleString()}</b> cr</>}
              {b.entered_by ? ` · entered by ${b.entered_by}` : ""}. BB has not published this buyback yet, so the
              ladder does not include it.
            </div>
          ))}
        </div>
      )}

      {/* Buybacks settling today — shown only when there are any, because
          Bangladesh's first was 12-Oct-2026 and they remain occasional. */}
      {buybacks.length > 0 && (
        <div className="rounded-xl border bd b-panel p-4">
          <h2 className="text-sm font-medium t-dim mb-3">
            Bond buyback — early principal repaid ({buybacks.length})
          </h2>
          <table className="w-full text-xs">
            <thead><tr className="t-dim border-b bd">
              <th className="pb-1 pr-2 text-left">ISIN</th>
              <th className="pb-1 pr-2 text-left">Security</th>
              <th className="pb-1 pr-2 text-right">Bids</th>
              <th className="pb-1 pr-2 text-right">Offered (cr)</th>
              <th className="pb-1 pr-2 text-right">Accepted face (cr)</th>
              <th className="pb-1 pr-2 text-right">Cash paid (cr)</th>
              <th className="pb-1 text-left">Auction</th>
            </tr></thead>
            <tbody>
              {buybacks.map((b) => (
                <tr key={b.isin + b.settlement_date} className="border-b bd">
                  <td className="py-1 pr-2 mono">{b.isin}</td>
                  <td className="py-1 pr-2">{b.security_name_norm ?? "—"}</td>
                  <td className="py-1 pr-2 text-right mono">{b.bids_received_count ?? "—"}</td>
                  <td className="py-1 pr-2 text-right mono">
                    {b.offered_face_crore?.toLocaleString() ?? "—"}
                  </td>
                  <td className="py-1 pr-2 text-right mono">
                    {b.accepted_face_crore?.toLocaleString() ?? "—"}
                  </td>
                  <td className="py-1 pr-2 text-right mono t-mute"
                      title="BB publishes no price for a buyback, so the ladder uses the face value. This bond traded at a premium, so the true cash was higher.">
                    {b.cash_paid_crore?.toLocaleString() ?? "not published"}
                  </td>
                  <td className="py-1 mono">{b.auction_date ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="text-xs t-mute mt-2">
            A buyback is the government repaying principal early, so the cash
            arrives in the market on settlement day and the bond goes on maturing
            for less. Where <b>cash paid</b> is not published, the ladder uses the
            accepted face value — understating the cash by the premium rather than
            inventing a price.
            {buybacks[0]?.source && (
              <> <a href={buybacks[0].source} target="_blank" rel="noreferrer"
                    className="underline">BB press release</a>.</>
            )}
          </p>
        </div>
      )}

      {/* OMO maturing today */}
      <div className="rounded-xl border bd b-panel p-4">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-medium t-dim">OMO Operations — dealt &amp; maturing ({omo.length})</h2>
          {omo.length > 0 && <DownloadButton data={omo} filename={`omo_${date}`} label="Excel" />}
        </div>
        {omo.length === 0
          ? <p className="text-xs t-mute">
              {liq && !liq.omo_known
                ? "No OMO data this far back — absent, not zero. BB operations are recorded from a later date."
                : "No OMO dealt or maturing on this date"}
            </p>
          : <table className="w-full text-xs">
              <thead><tr className="t-dim border-b bd">
                <th className="pb-1 pr-2 text-left">Leg</th>
                <th className="pb-1 pr-2 text-left">Instrument</th>
                <th className="pb-1 pr-2 text-left">Tenor</th>
                <th className="pb-1 pr-2 text-right">Rate</th>
                <th className="pb-1 pr-2 text-left">Other date</th>
                <th className="pb-1 pr-2 text-left">Effect today</th>
                <th className="pb-1 text-right">Amount (cr)</th>
              </tr></thead>
              <tbody>
                {omo.map((o, i) => (
                  <tr key={i} className="border-b bd">
                    <td className="py-1 pr-2 t-dim">{o.leg === "DEALT" ? "dealt" : "maturing"}</td>
                    <td className="py-1 pr-2 t-fg">{o.instrument}</td>
                    <td className="py-1 pr-2 t-dim">{o.tenor_label ?? "—"}</td>
                    <td className="py-1 pr-2 text-right t-dim">{o.rate_pct != null ? `${o.rate_pct}%` : "—"}</td>
                    <td className="py-1 pr-2 t-mute font-mono">{o.transacted_from ?? "—"}</td>
                    <td className="py-1 pr-2">
                      <span className={`px-1 py-0.5 rounded text-xs ${o.liquidity_effect === "INFLOW" ? "b-pos-soft t-pos" : "b-warn-soft t-warn"}`}>
                        {o.liquidity_effect === "INFLOW" ? "injects" : "drains"}
                      </span>
                      <span className="t-mute ml-1">({o.direction.toLowerCase()} maturing)</span>
                    </td>
                    <td className="py-1 text-right t-fg font-mono">{o.crore.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
        }
      </div>

      <div className="grid md:grid-cols-3 gap-6">
        {/* Maturities */}
        <div className="rounded-xl border bd b-panel p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-medium t-dim">Maturities ({data.maturities.length})</h2>
            {data.maturities.length > 0 && <DownloadButton data={data.maturities} filename={`maturities_${date}`} label="Excel" />}
          </div>
          {data.maturities.length === 0
            ? <p className="text-xs t-mute">No maturities on this date</p>
            : <table className="w-full text-xs">
                <thead><tr className="t-dim border-b bd">
                  <th className="pb-1 pr-2 text-left">ISIN</th>
                  <th className="pb-1 pr-2 text-left">Name</th>
                  <th className="pb-1 text-right">Principal (mn)</th>
                </tr></thead>
                <tbody>
                  {data.maturities.map((m, i) => (
                    <tr key={i} className="border-b bd">
                      <td className="py-1 pr-2 font-mono t-dim">{m.isin}</td>
                      <td className="py-1 pr-2 t-dim truncate max-w-[120px]">{m.security_name_norm ?? m.isin}</td>
                      <td className="py-1 text-right t-fg font-mono">{(m.principal_bdt_mill ?? 0).toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
          }
        </div>

        {/* Coupons */}
        <div className="rounded-xl border bd b-panel p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-medium t-dim">Coupons ({data.coupons.length})</h2>
            {data.coupons.length > 0 && <DownloadButton data={data.coupons} filename={`coupons_${date}`} label="Excel" />}
          </div>
          {data.coupons.length === 0
            ? <p className="text-xs t-mute">No coupon payments on this date</p>
            : <table className="w-full text-xs">
                <thead><tr className="t-dim border-b bd">
                  <th className="pb-1 pr-2 text-left">ISIN</th>
                  <th className="pb-1 pr-2 text-right">Rate</th>
                  <th className="pb-1 text-right">Amount (mn)</th>
                </tr></thead>
                <tbody>
                  {data.coupons.map((c, i) => (
                    <tr key={i} className="border-b bd">
                      <td className="py-1 pr-2 font-mono t-dim">{c.isin}</td>
                      <td className="py-1 pr-2 text-right t-dim">{c.coupon_rate_used_pct}%</td>
                      <td className="py-1 text-right t-fg font-mono">{(c.amount_bdt_mill ?? 0).toFixed(4)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
          }
        </div>

        {/* Auctions */}
        <div className="rounded-xl border bd b-panel p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-medium t-dim">Auction Settlements ({data.auctions.length})</h2>
            {data.auctions.length > 0 && <DownloadButton data={data.auctions} filename={`auctions_${date}`} label="Excel" />}
          </div>
          {data.auctions.length === 0
            ? <p className="text-xs t-mute">No auction settlements on this date</p>
            : <table className="w-full text-xs">
                <thead><tr className="t-dim border-b bd">
                  <th className="pb-1 pr-2 text-left">Type</th>
                  <th className="pb-1 pr-2 text-left">Tenor</th>
                  <th className="pb-1 pr-2 text-right">Accepted (mn)</th>
                  <th className="pb-1 text-left">Status</th>
                </tr></thead>
                <tbody>
                  {data.auctions.map((a, i) => (
                    <tr key={i} className="border-b bd">
                      <td className="py-1 pr-2 t-dim">{a.security_type}</td>
                      <td className="py-1 pr-2 t-dim">{a.tenor_label}</td>
                      <td className="py-1 pr-2 text-right t-fg font-mono">
                        {a.accepted_amount_bdt_mill ? a.accepted_amount_bdt_mill.toLocaleString() : "PLANNED"}
                      </td>
                      <td className="py-1">
                        <span className={`px-1 py-0.5 rounded text-xs ${a.outflow_status === "CONFIRMED" ? "b-pos-soft t-pos" : "b-warn-soft t-warn"}`}>
                          {a.outflow_status}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
          }
        </div>
      </div>
    </div>
  );
}

/** One line of the net-liquidity strip. `known=false` prints an em dash, never
 *  a zero: a day outside a source's coverage has no figure, and 0 would be a
 *  claim that nothing happened. */
function CumLine({ label, b }: { label: string; b: CumulativeBlock }) {
  const fmt = (v: number) => v.toLocaleString(undefined, { maximumFractionDigits: 2 });
  const cls = (v: number) => (v < 0 ? "t-warn" : "t-pos");
  return (
    <div className="rounded-lg px-3 py-2 b-panel2" style={{ minWidth: 260 }}>
      <div className="t-dim" style={{ fontSize: 10.5, textTransform: "uppercase", letterSpacing: ".08em" }}>
        {label}
      </div>
      {b.net_crore != null ? (
        <div className={`font-mono ${cls(b.net_crore)}`} style={{ fontSize: 20, fontWeight: 700, marginTop: 2 }}>
          {fmt(b.net_crore)}
        </div>
      ) : (
        <>
          <div className="font-mono t-mute" style={{ fontSize: 20, fontWeight: 700, marginTop: 2 }}
               title="Some days' OMO is not known yet — the total is withheld, not zero">—</div>
          <div className="t-warn" style={{ fontSize: 11, marginTop: 4, lineHeight: 1.5 }}>
            OMO unknown for {b.omo_incomplete_days.length} day(s) (first: {b.omo_incomplete_days[0]}).
            {b.complete_through && b.net_complete_through_crore != null && (
              <> Complete through {b.complete_through}: <b className="font-mono">{fmt(b.net_complete_through_crore)}</b>.</>
            )}
            {" "}Excluding OMO: <b className="font-mono">{fmt(b.net_excl_omo_crore)}</b> (govt in less auctions).
          </div>
        </>
      )}
    </div>
  );
}

function Line({ label, hint, value, tone, known, strong, big }: {
  label: string; hint?: string; value: number;
  tone: "pos" | "neg" | "auto"; known: boolean;
  strong?: boolean; big?: boolean;
}) {
  const cls = tone === "auto" ? (value < 0 ? "t-warn" : "t-pos") : tone === "pos" ? "t-pos" : "t-warn";
  return (
    <div className={`rounded-lg px-3 py-2 ${strong ? "b-panel2" : ""}`} style={{ minWidth: 150 }}>
      <div className="t-dim" style={{ fontSize: 10.5, textTransform: "uppercase", letterSpacing: ".08em" }}>
        {label}
      </div>
      <div className={`font-mono ${known ? cls : "t-mute"}`}
           style={{ fontSize: big ? 20 : 15, fontWeight: strong ? 700 : 500, marginTop: 2 }}>
        {known
          ? value.toLocaleString(undefined, { maximumFractionDigits: 2 })
          : <span title="No data for this date — not zero">—</span>}
      </div>
      {hint && <div className="t-mute" style={{ fontSize: 10, maxWidth: 170, marginTop: 2 }}>{hint}</div>}
    </div>
  );
}
