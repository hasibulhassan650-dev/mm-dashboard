import Link from "next/link";
import { api } from "@/lib/api";
import type { NewsEvent } from "@/lib/api";
import { Panel } from "@/components/terminal/ui";
import RelatedLinks from "@/components/RelatedLinks";

export const revalidate = 300;

/**
 * Liquidity Watch: liquidity events reported in the Bangladeshi press that BB's own
 * data does not hold - a subsidy release, arrears, support to a bank, a CRR change.
 *
 * Read by a language model, then checked by code: the quote must be in the article
 * and the amount is re-read from it. Never part of a ladder, cumulative or model
 * total; shown beside BB's figures, labelled as news.
 */
const KIND_LABEL: Record<string, string> = {
  SUBSIDY_RELEASE: "Subsidy release", ARREARS_CASH: "Arrears paid", BB_BANK_SUPPORT: "BB bank support",
  BB_REFINANCE: "Refinance scheme", GOVT_BORROW_BB: "Govt borrowing from BB", BANK_RECAP: "Bank recapitalisation",
  DEPOSIT_PAYOUT: "Deposit payout", RESERVE_REQ_CUT: "CRR/SLR cut", BB_FX_BUY: "BB buys USD",
  RESERVE_REQ_HIKE: "CRR/SLR hike", BB_BILL: "BB bills", SAVINGS_CERT_NET_SALE: "Savings certificates",
  GOVT_REPAY_BB: "Govt repays BB", BB_FX_SELL: "BB sells USD", TAX_DEADLINE: "Tax / dividend drain",
  SPECIAL_BOND_ARREARS: "Special bonds (arrears)", BUDGET_SUPPORT_FX: "Budget support (USD)",
};
const fmt = (v: number | null | undefined, d = 0) =>
  v == null ? "—" : v.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });

function Dir({ d }: { d: NewsEvent["direction"] }) {
  const col = d === "INJECT" ? "var(--pos)" : d === "ABSORB" ? "var(--warn)" : "var(--fg-mute)";
  return <b style={{ color: col }}>{d === "INJECT" ? "▲ injects" : d === "ABSORB" ? "▼ absorbs" : "neutral"}</b>;
}

export default async function LiquidityWatchPage({
  searchParams,
}: { searchParams: Promise<{ redundant?: string; minor?: string }> }) {
  const sp = await searchParams;
  const showRed = sp.redundant === "1";
  const showMinor = sp.minor === "1";
  const feed = await api.news(180, showRed, showMinor);

  if (!feed) {
    return (
      <Panel title="Liquidity Watch" span={12}>
        <p style={{ margin: 0, fontSize: 13, color: "var(--warn)" }}>
          The news feed is unavailable, or <code>/api/news</code> is not deployed yet. Nothing is assumed
          in its place.
        </p>
      </Panel>
    );
  }

  const t = feed.totals;
  const h = feed.health;
  const stale = !h.last_fetch_utc || (Date.now() - new Date(h.last_fetch_utc + "Z").getTime()) > 6 * 3600e3;
  const waiting = h.articles_waiting_for_claude;
  const qs = (r: boolean, m: boolean) => `?${r ? "redundant=1&" : ""}${m ? "minor=1" : ""}`.replace(/[?&]$/, "");

  const kpi = (label: string, value: string, sub: string, color?: string) => (
    <div className="kpi">
      <div className="kpi-top"><span className="kpi-label">{label}</span></div>
      <div className="kpi-val"><span className="kpi-num" style={color ? { color } : undefined}>{value}</span></div>
      <div className="kpi-sub">{sub}</div>
    </div>
  );

  return (
    <>
      {(stale || waiting > 20) && (
        <div style={{
          border: "1px solid var(--neg)", borderRadius: "var(--radius-sm)", padding: "9px 14px",
          marginBottom: "var(--gap)", fontSize: 12.5, color: "var(--neg)",
          background: "color-mix(in oklab, var(--neg) 9%, transparent)",
        }}>
          <b>The news watch is not working properly.</b>{" "}
          {stale && <>It has not read any article for 6 hours or more. </>}
          {waiting > 20 && <>{waiting} articles are waiting for Claude to read them (the ANTHROPIC_API_KEY secret is
            missing, or the calls are failing). Events below may be missing. </>}
        </div>
      )}

      <div className="kpi-strip" style={{ gridTemplateColumns: "repeat(auto-fit,minmax(190px,1fr))" }}>
        {kpi("Injected this month", fmt(t.month_inject_crore), "crore · events with a stated amount", "var(--pos)")}
        {kpi("Absorbed this month", fmt(t.month_absorb_crore), "crore", "var(--warn)")}
        {kpi("Injected, FY to date", fmt(t.fy_inject_crore), `crore · since ${feed.fiscal_year_start}`, "var(--pos)")}
        {kpi("Absorbed, FY to date", fmt(t.fy_absorb_crore), "crore", "var(--warn)")}
        {kpi("Events counted", String(t.events_counted),
             t.events_without_amount ? `${t.events_without_amount} state no amount — listed, never counted as 0` : "all state an amount")}
      </div>

      <div className="grid12">
        <Panel
          title="Liquidity events from the news"
          sub={`${feed.count} shown · last ${feed.window_days} days · BDT crore · NOT Bangladesh Bank data, NOT in any ladder total`}
          span={12}
          pad={false}
          right={
            <span style={{ fontSize: 11.5, display: "flex", gap: 12 }}>
              <Link href={qs(!showRed, showMinor)} style={{ color: "var(--info)", textDecoration: "none" }}>
                {showRed ? "hide" : "show"} already in BB data ({feed.hidden.redundant})</Link>
              <Link href={qs(showRed, !showMinor)} style={{ color: "var(--info)", textDecoration: "none" }}>
                {showMinor ? "hide" : "show"} minor ({feed.hidden.minor})</Link>
            </span>
          }
        >
          <div className="table-wrap" style={{ maxHeight: 640, overflowY: "auto" }}>
            <table className="dt">
              <thead><tr>
                <th>Date</th><th>Event</th><th>Effect</th><th className="r">Amount (cr)</th>
                <th>Status</th><th>Who</th><th>What</th><th>Sources</th>
              </tr></thead>
              <tbody>
                {feed.events.length === 0 && (
                  <tr><td colSpan={8} style={{ padding: "12px", fontSize: 12.5, color: "var(--fg-mute)" }}>
                    No qualifying event in this window. That is a finding only if the watch is running
                    (see above) - it is not a claim that nothing happened.
                  </td></tr>
                )}
                {feed.events.map((e) => (
                  <tr key={e.id} style={e.redundant_with || !e.matters ? { opacity: 0.6 } : undefined}>
                    <td className="mono">{e.when ?? "—"}</td>
                    <td>{KIND_LABEL[e.kind] ?? e.kind}
                      {e.redundant_with && <div style={{ fontSize: 10, color: "var(--fg-mute)" }}>already in {e.redundant_with}</div>}
                      {!e.matters && !e.redundant_with && <div style={{ fontSize: 10, color: "var(--fg-mute)" }}>below threshold</div>}
                    </td>
                    <td><Dir d={e.direction} /></td>
                    <td className="r mono">
                      {e.amount_crore == null
                        ? <span title={e.amount_unverified ? "the article's wording did not support the amount, so it is not shown" : "no taka amount stated"}
                                style={{ color: "var(--fg-mute)" }}>{e.amount_unverified ? "unverified" : "not stated"}</span>
                        : fmt(e.amount_crore)}
                    </td>
                    <td style={{ fontSize: 11 }}>{e.status ? e.status.toLowerCase() : "—"}</td>
                    <td style={{ whiteSpace: "normal", maxWidth: 150, padding: "6px var(--pad)" }}>{e.counterparty ?? "—"}</td>
                    <td style={{ whiteSpace: "normal", maxWidth: 420, padding: "6px var(--pad)", lineHeight: 1.5 }}>
                      {e.summary}
                      <div style={{ fontSize: 11, color: "var(--fg-mute)", marginTop: 2, fontStyle: "italic" }}>
                        “{e.evidence_quote}”
                      </div>
                    </td>
                    <td style={{ whiteSpace: "normal", padding: "6px var(--pad)" }}>
                      {e.sources.slice(0, 4).map((s, i) => (
                        <div key={i}><a href={s.url} target="_blank" rel="noreferrer"
                          style={{ fontSize: 11, textDecoration: "none", color: "var(--info)" }}>{s.source}</a></div>
                      ))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ padding: "8px 12px", fontSize: 11.5, color: "var(--fg-mute)",
                        borderTop: "1px solid var(--border)", lineHeight: 1.75 }}>
            {feed.note} Events of 500 crore or more, and any reserve-requirement change, BB lending to a bank
            or government borrowing from BB, are counted; smaller ones are stored and hidden. An event whose
            figures BB already publishes (an OMO tranche, a buyback, an FX auction) is hidden so you are not
            told the same fact twice.
          </div>
        </Panel>

        <Panel title="By month" sub="events that matter, with a stated amount · BDT crore" span={6} pad={false}>
          <div className="table-wrap">
            <table className="dt">
              <thead><tr><th>Month</th><th className="r">Injected</th><th className="r">Absorbed</th><th className="r">Events</th></tr></thead>
              <tbody>
                {feed.monthly.length === 0 && (
                  <tr><td colSpan={4} style={{ padding: "10px 12px", fontSize: 12.5, color: "var(--fg-mute)" }}>None yet.</td></tr>
                )}
                {[...feed.monthly].reverse().map((m) => (
                  <tr key={m.month}>
                    <td className="mono">{m.month}</td>
                    <td className="r mono" style={{ color: "var(--pos)" }}>{fmt(m.inject_crore)}</td>
                    <td className="r mono" style={{ color: "var(--warn)" }}>{fmt(m.absorb_crore)}</td>
                    <td className="r mono">{m.events}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>

        <Panel title="What the watch has read" sub="every article is stored, including the ones filtered out" span={6}>
          <div style={{ fontSize: 12.5, lineHeight: 1.9, color: "var(--fg-dim)" }}>
            {Object.entries(h.articles_by_status).sort().map(([k, n]) => (
              <div key={k}><b className="mono" style={{ color: "var(--fg)" }}>{n}</b> {k.replace("_", " ")}</div>
            ))}
            <div style={{ marginTop: 8, color: "var(--fg-mute)", fontSize: 11.5 }}>
              Last read: {h.last_fetch_utc ?? "never"} (UTC). Sources: The Daily Star, The Business Standard,
              Prothom Alo English, Dhaka Tribune, The Daily Observer. Bangladesh Bank&apos;s own releases are on the
              Press Releases page.
            </div>
          </div>
        </Panel>
      </div>

      <RelatedLinks items={[
        { href: "/omo", label: "OMO", why: "what BB itself injects and absorbs" },
        { href: "/cashflows", label: "Cash Flows", why: "the ladder these events are NOT part of" },
        { href: "/press", label: "Press Releases", why: "BB's own announcements" },
      ]} />
    </>
  );
}
