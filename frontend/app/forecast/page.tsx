import { api, exportUrl } from "@/lib/api";
import Link from "next/link";
import RelatedLinks from "@/components/RelatedLinks";
import { fmtDate, fmtCrore, fmtRate } from "@/lib/format";
import { Panel } from "@/components/terminal/ui";
import LiquidityLadderChart from "@/components/LiquidityLadderChart";
import DateRangeControl from "@/components/DateRangeControl";
import LadderTable from "@/components/LadderTable";
import Freshness from "@/components/Freshness";

export const revalidate = 300;

function iso(d: Date) { return d.toISOString().slice(0, 10); }
function shift(from: string, days: number) {
  const d = new Date(from + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() + days); return iso(d);
}

export default async function ForecastPage({
  searchParams,
}: { searchParams: Promise<{ from?: string; to?: string; days?: string }> }) {
  const sp = await searchParams;
  const today = iso(new Date());

  // The window. `?days=` still works so old links and bookmarks keep resolving,
  // but the page is now driven by an explicit from/to that may sit in the past.
  const legacyDays = sp.days ? Math.min(400, Math.max(1, parseInt(sp.days, 10) || 28)) : null;
  const from = sp.from ?? (legacyDays ? shift(today, 1) : shift(today, 1));
  const to = sp.to ?? (legacyDays ? shift(today, legacyDays) : shift(today, 28));

  const [forecast, cm, policy, fresh] = await Promise.all([
    api.flowsLadder(from, to),
    api.callmoney(14).catch(() => null),
    api.policy(),
    api.freshness(),
  ]);
  const days = forecast.days;

  // Looking at the past is a different job from planning the next four weeks.
  // Advice like "position as a lender going in" is not merely useless pointed
  // backwards, it reads as a recommendation about a day that has already gone.
  const isHistory = to < today;
  const net7 = days.filter(d => d.date >= today).slice(0, 7).reduce((s, d) => s + d.net_crore, 0);
  const active = days.filter(d =>
    d.net_crore !== 0 || d.omo_return_crore > 0 || d.omo_repay_crore > 0 ||
    d.govt_inflow_crore > 0 || d.auction_out_crore > 0);
  const biggestDrain = [...days].sort((a, b) => a.net_crore - b.net_crore)[0];
  const biggestInject = [...days].sort((a, b) => b.net_crore - a.net_crore)[0];
  const closingCum = days.at(-1)?.cum_net_crore ?? 0;

  // Bounds for the picker come from the data's own coverage, so they move when
  // BB publishes instead of rotting in a constant.
  const minDate = forecast.flows_data_from ?? forecast.omo_data_from ?? "2025-07-01";
  const maxDate = forecast.flows_data_to ?? shift(today, 180);
  const omoFrom = forecast.omo_data_from ?? null;
  const omoBlindDays = days.filter(d => !d.omo_known).length;

  const war = cm?.daily_summary?.at(-1)?.overnight_wavg_rate ?? null;
  const cor = policy.current;
  const corridorPos = war != null && cor?.sdf != null && cor?.slf != null
    ? Math.round(((war - cor.sdf) / (cor.slf - cor.sdf)) * 100) : null;

  const pressure = net7 < 0
    ? { label: "DRAINING — upward pressure on call rates", color: "var(--warn)" }
    : { label: "FLUSH — downward pressure on call rates", color: "var(--info)" };

  // Auction visibility: beyond the last ingested auction event, auction
  // outflows are UNKNOWN — say so, never show them as zero.
  const windowEnd = days.at(-1)?.date ?? null;
  const horizon = forecast.auction_horizon ?? null;
  const auctionsBlind = windowEnd != null && (horizon == null || horizon < windowEnd);

  // ── Desk alerts: threshold-crossing signals with an explicit action, shown
  // only when they actually fire — distinct from the always-on KPIs below. ────
  const alerts: { tone: "warn" | "info"; text: string }[] = [];
  if (isHistory) {
    // Nothing to position for; describe what happened instead.
    if (biggestDrain) alerts.push({ tone: "warn", text: `Tightest day in this window: ${fmtDate(biggestDrain.date)} (${biggestDrain.weekday}) at ${fmtCrore(biggestDrain.net_crore)} cr net.` });
    if (biggestInject) alerts.push({ tone: "info", text: `Flushest day: ${fmtDate(biggestInject.date)} (${biggestInject.weekday}) at ${fmtCrore(biggestInject.net_crore)} cr net.` });
    alerts.push({ tone: closingCum < 0 ? "warn" : "info", text: `Over the whole window known flows netted ${fmtCrore(closingCum)} cr — ${closingCum < 0 ? "a net drain" : "a net injection"}.` });
  } else if (war != null && cor?.slf != null && war >= cor.slf - 0.5)
    alerts.push({ tone: "warn", text: `Call O/N ${fmtRate(war, 2)}% is within 50 bps of the SLF ceiling (${cor.slf}%) — the system is tight; go in as a lender.` });
  else if (war != null && cor?.sdf != null && war <= cor.sdf + 0.5)
    alerts.push({ tone: "info", text: `Call O/N ${fmtRate(war, 2)}% is near the SDF floor (${cor.sdf}%) — the system is flush; fund cheap as a borrower.` });
  if (!isHistory) {
    for (const d of days.filter(x => x.date >= today && x.net_crore <= -5000).sort((a, b) => a.net_crore - b.net_crore).slice(0, 2))
      alerts.push({ tone: "warn", text: `Large drain ${fmtDate(d.date)} (${d.weekday}): ${fmtCrore(d.net_crore)} cr net — position as a lender going in.` });
    if (biggestInject && biggestInject.net_crore >= 5000 && biggestInject.date >= today)
      alerts.push({ tone: "info", text: `Flush day ${fmtDate(biggestInject.date)} (${biggestInject.weekday}): +${fmtCrore(biggestInject.net_crore)} cr — a cheap borrowing window.` });
    if (closingCum <= -10000)
      alerts.push({ tone: "warn", text: `Cumulative known liquidity over the window is ${fmtCrore(closingCum)} cr — a sustained squeeze; keep lending capacity in reserve.` });
  }

  return (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, marginBottom: "var(--gap)", flexWrap: "wrap" }}>
        <Freshness updated={fresh.flows} />
        <DateRangeControl min={minDate} max={maxDate} from={from} to={to} />
      </div>

      {auctionsBlind && (
        <div style={{
          border: "1px solid var(--warn)", borderRadius: "var(--radius-sm)",
          padding: "8px 12px", marginBottom: "var(--gap)", fontSize: 12.5,
          color: "var(--warn)", background: "color-mix(in oklab, var(--warn) 10%, transparent)",
        }}>
          ⚠ AUCTION DATA INCOMPLETE — last known auction settlement is {horizon ? fmtDate(horizon) : "none on record"}.
          Days after that show NO auction outflow because the calendar isn&apos;t ingested yet, not because none is scheduled.
          Net figures on those days overstate liquidity. BB auctions T-bills nearly every week.
        </div>
      )}

      {omoBlindDays > 0 && (
        <div style={{
          border: "1px solid var(--info)", borderRadius: "var(--radius-sm)",
          padding: "8px 12px", marginBottom: "var(--gap)", fontSize: 12.5,
          color: "var(--info)", background: "color-mix(in oklab, var(--info) 10%, transparent)",
        }}>
          ⓘ NO OMO DATA for {omoBlindDays} of these {days.length} days — our OMO record starts
          {" "}{omoFrom ? fmtDate(omoFrom) : "later than this window"}. Those days show no repo,
          SDF or other operation because none is <b>recorded</b>, not because BB ran none. Their
          net figures understate movement in both directions and should not be read as a quiet
          market.
        </div>
      )}

      {alerts.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: "var(--gap)" }}>
          <div style={{ fontSize: 10.5, fontWeight: 600, textTransform: "uppercase", letterSpacing: ".12em", color: "var(--fg-dim)" }}>Desk Alerts</div>
          {alerts.map((a, i) => {
            const c = a.tone === "warn" ? "var(--warn)" : "var(--info)";
            return (
              <div key={i} style={{
                display: "flex", gap: 9, alignItems: "flex-start",
                border: `1px solid ${c}`, borderRadius: "var(--radius-sm)", padding: "8px 12px", fontSize: 12.5,
                background: `color-mix(in oklab, ${c} 9%, transparent)`,
              }}>
                <span style={{ fontWeight: 700, color: c }}>{a.tone === "warn" ? "▲" : "▼"}</span>
                <span style={{ color: "var(--fg)" }}>{a.text}</span>
              </div>
            );
          })}
        </div>
      )}

      <div className="kpi-strip" style={{ gridTemplateColumns: "repeat(4,1fr)" }}>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">Call O/N WAR</span></div>
          <div className="kpi-val"><span className="kpi-num">{war != null ? fmtRate(war, 2) : "—"}</span><span className="kpi-unit">%</span></div>
          <div className="kpi-sub">{corridorPos != null ? `${corridorPos}% up the ${cor?.sdf}–${cor?.slf} corridor` : "corridor n/a"}</div>
        </div>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">{isHistory ? "Window Closing Cumulative" : "Next 7d Known Net"}</span></div>
          <div className="kpi-val"><span className="kpi-num" style={{ color: (isHistory ? closingCum : net7) < 0 ? "var(--warn)" : "var(--info)" }}>{fmtCrore(isHistory ? closingCum : net7)}</span><span className="kpi-unit">cr</span></div>
          <div className="kpi-sub" style={isHistory ? undefined : { color: pressure.color }}>
            {isHistory ? `net over ${from} → ${to}` : pressure.label}
          </div>
        </div>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">Tightest Day</span></div>
          <div className="kpi-val"><span className="kpi-num neg">{biggestDrain ? fmtCrore(biggestDrain.net_crore) : "—"}</span><span className="kpi-unit">cr</span></div>
          <div className="kpi-sub">{biggestDrain ? `${fmtDate(biggestDrain.date)}${isHistory ? "" : " — lend into it"}` : ""}</div>
        </div>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">Flushest Day</span></div>
          <div className="kpi-val"><span className="kpi-num pos">{biggestInject ? fmtCrore(biggestInject.net_crore) : "—"}</span><span className="kpi-unit">cr</span></div>
          <div className="kpi-sub">{biggestInject ? `${fmtDate(biggestInject.date)}${isHistory ? "" : " — cheap borrowing"}` : ""}</div>
        </div>
      </div>

      <div className="grid12">
        <Panel title={isHistory ? "Known Liquidity Ladder — What Happened" : "Known Liquidity Ladder"} span={12}
          sub={`${from} → ${to} (BDT crore) · contracted, dated flows only · excludes future BB operations, which react to this`}>
          <LiquidityLadderChart data={days} />
        </Panel>

        <Panel title="Day-by-Day Ladder"
          sub="only days with flows · click a row for the OMO split, the date for the full day"
          span={12} pad={false}
          right={<a href={exportUrl.ladder(from, to)} className="seg-b" style={{ textDecoration: "none" }}>Download Excel</a>}>
          <LadderTable days={active} omoDataFrom={omoFrom}
                       auctionHorizon={forecast.auction_horizon} />
        </Panel>

        <Panel title="How to Read This" span={12}>
          <div style={{ fontSize: 12.5, lineHeight: 1.7, color: "var(--fg-mute)", maxWidth: 900 }}>
            <p><b style={{ color: "var(--fg)" }}>This ladder is what the market already knows.</b> Every bar is a contracted,
            dated cash flow: OMO repos/AR/IBLF maturing (banks must repay BB — drain), SDF maturing (cash returns — inject),
            G-sec coupons and maturities (inject), and auction settlements (drain).</p>
            <p><b style={{ color: "var(--fg)" }}>Trading it:</b> a deeply negative day means the system needs cash — call rates
            get bid toward the SLF ceiling, so position as a <b>lender</b> going in. A flush day pushes rates toward the SDF
            floor — fund yourself there as a <b>borrower</b>. BB usually offsets big imbalances with new OMO the same day;
            the gap between this ladder and what BB actually does is where the rate moves.</p>
          </div>
        </Panel>
      </div>
      <RelatedLinks items={[
        { href: "/omo", label: "OMO Operations", why: "the repos & SDF driving these flows" },
        { href: "/callmoney", label: "Call Money", why: "where tight/flush days hit the rate" },
        { href: "/cashflows", label: "Cash Flows", why: "coupon & maturity detail by date" },
      ]} />
    </>
  );
}
