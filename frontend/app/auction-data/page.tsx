import { api, exportUrl } from "@/lib/api";
import type { AuctionSheetRow } from "@/lib/api";
import { Panel } from "@/components/terminal/ui";
import { DataWarning } from "@/components/DataWarning";
import DateRangeControl from "@/components/DateRangeControl";
import Freshness from "@/components/Freshness";
import RelatedLinks from "@/components/RelatedLinks";

export const revalidate = 300;

function iso(d: Date) { return d.toISOString().slice(0, 10); }
function shift(from: string, days: number) {
  const d = new Date(from + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() + days); return iso(d);
}
const n2 = (v: number | null | undefined) =>
  v == null ? "" : v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export default async function AuctionDataPage({
  searchParams,
}: { searchParams: Promise<{ from?: string; to?: string }> }) {
  const sp = await searchParams;
  const today = iso(new Date());
  const from = sp.from ?? shift(today, -730);
  const to = sp.to ?? today;

  const [sheet, fresh] = await Promise.all([
    api.auctionSheet({ from, to }), api.freshness(),
  ]);

  if (!sheet.count) {
    return (
      <>
        <DataWarning dataset={["yields", "auctions"]} />
        <Panel title="Auction Data" span={12}>
          <p style={{ margin: 0, fontSize: 13, color: "var(--warn)" }}>
            Nothing in this window, or <code>/api/securities/auction-sheet</code> is not deployed
            yet. No figures are shown rather than zeros.
          </p>
        </Panel>
      </>
    );
  }

  const gaps = sheet.count - sheet.with_isin;
  const noSale = sheet.count - sheet.with_sale_value;

  return (
    <>
      <DataWarning dataset={["yields", "auctions", "securities"]} />
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center",
                    gap: 12, marginBottom: "var(--gap)", flexWrap: "wrap" }}>
        <Freshness updated={fresh.yields} />
        <DateRangeControl min="2007-01-01" max={shift(today, 30)} from={from} to={to} />
      </div>

      <div className="kpi-strip" style={{ gridTemplateColumns: "repeat(4,1fr)" }}>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">Auctions</span></div>
          <div className="kpi-val"><span className="kpi-num">{sheet.count.toLocaleString()}</span></div>
          <div className="kpi-sub">{from} → {to}</div>
        </div>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">Re-issues</span></div>
          <div className="kpi-val"><span className="kpi-num">{sheet.reissue_count.toLocaleString()}</span></div>
          <div className="kpi-sub">{sheet.isins_auctioned_more_than_once.length} ISINs sold more than once</div>
        </div>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">With ISIN</span></div>
          <div className="kpi-val">
            <span className="kpi-num" style={{ color: gaps ? "var(--warn)" : "var(--pos)" }}>
              {sheet.with_isin.toLocaleString()}
            </span>
          </div>
          <div className="kpi-sub">{gaps ? `${gaps} older rows need a re-fetch` : "complete"}</div>
        </div>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">With Sale Value</span></div>
          <div className="kpi-val">
            <span className="kpi-num" style={{ color: noSale ? "var(--warn)" : "var(--pos)" }}>
              {sheet.with_sale_value.toLocaleString()}
            </span>
          </div>
          <div className="kpi-sub">{noSale ? `${noSale} without` : "complete"}</div>
        </div>
      </div>

      {(gaps > 0 || noSale > 0) && (
        <div style={{
          border: "1px solid var(--warn)", borderRadius: "var(--radius-sm)",
          padding: "8px 12px", marginBottom: "var(--gap)", fontSize: 12.5,
          color: "var(--warn)", background: "color-mix(in oklab, var(--warn) 9%, transparent)",
        }}>
          ⚠ {Math.max(gaps, noSale)} auction{Math.max(gaps, noSale) === 1 ? "" : "s"} in this window
          have no ISIN or sale value. BB has always published both; those months were stored before
          the parser kept them. Re-run <b>Treasury Backfill</b> with <b>refetch</b> to fill them —
          the cells are blank rather than zero so nothing is invented in the meantime.
        </div>
      )}

      <div className="grid12">
        <Panel
          title="Auction Data"
          sub={`${sheet.count} auctions · BDT crore · the eight columns of your sheet, in order`}
          span={12}
          pad={false}
          right={<a href={exportUrl.auctionSheet({ from, to })} className="seg-b"
                    style={{ textDecoration: "none" }}>Download Excel</a>}
        >
          <div className="table-wrap" style={{ maxHeight: 640, overflowY: "auto" }}>
            <table className="dt">
              <thead><tr>
                <th>ISIN</th>
                <th>Issue Date</th>
                <th>Maturity Date</th>
                <th className="r">Tenure</th>
                <th className="r">Auctioned Amount</th>
                <th className="r">Successful Market Bid</th>
                <th className="r">Devolvement on Bangladesh Bank</th>
                <th className="r">Coupon</th>
              </tr></thead>
              <tbody>
                {sheet.rows.map((r, i) => <Row key={i} r={r} />)}
              </tbody>
            </table>
          </div>
          <div style={{ padding: "8px 12px", fontSize: 11.5, color: "var(--fg-mute)",
                        borderTop: "1px solid var(--border)", lineHeight: 1.75 }}>
            <p style={{ margin: 0 }}>
              One row per <b style={{ color: "var(--fg)" }}>auction</b>, not per security. A
              re-issue is the same ISIN sold again later at its own cut-off, so an ISIN appears
              several times with different issue dates — <b style={{ color: "var(--fg)" }}>that is
              correct, not a duplicate</b>. Those rows are marked.
            </p>
            <p style={{ margin: "6px 0 0" }}>
              <b style={{ color: "var(--fg)" }}>Auctioned Amount</b> is the accepted face value
              (par); <b style={{ color: "var(--fg)" }}>Successful Market Bid</b> is the sale value —
              the cash actually paid. They differ on every auction: bills sell at a discount, and a
              premium re-issue sells above par.
            </p>
            <p style={{ margin: "6px 0 0" }}>
              <b style={{ color: "var(--fg)" }}>Devolvement</b> is blank because BB publishes a
              devolvement <i>yield</i>, not an amount — blank means not published, not zero
              devolved. <b style={{ color: "var(--fg)" }}>Coupon</b> is blank for T-Bills, which are
              zero-coupon discount instruments; for an FRTB it is the current reset and is marked,
              because the rate at that auction may have differed.
            </p>
          </div>
        </Panel>
      </div>

      <RelatedLinks items={[
        { href: "/auctions", label: "Treasury Auctions", why: "the same auctions with bids and cover" },
        { href: "/yields", label: "Yield Curve", why: "where these cut-offs sit on the curve" },
        { href: "/securities", label: "Securities", why: "the instrument master behind each ISIN" },
      ]} />
    </>
  );
}

function Row({ r }: { r: AuctionSheetRow }) {
  return (
    <tr>
      <td className="mono">
        {r.isin ?? <span style={{ color: "var(--warn)" }} title="BB's page for this month was stored before the parser kept the ISIN">—</span>}
        {r.is_reissue && (
          <span style={{ marginLeft: 6, fontSize: 9.5, color: "var(--info)" }}
                title={`BB marked this a re-issue${r.reissue_remaining ? `, ${r.reissue_remaining} remaining` : ""}`}>
            RE-ISSUE
          </span>
        )}
      </td>
      <td className="mono">{r.issue_date ?? "—"}</td>
      <td className="mono">
        {r.maturity_date ?? "—"}
        {r.maturity_is_derived && (
          <span style={{ marginLeft: 5, fontSize: 9.5, color: "var(--fg-mute)" }}
                title="Derived from issue date + tenor: this ISIN is not in the security master">
            ≈
          </span>
        )}
      </td>
      <td className="r mono">{r.tenure ?? "—"}</td>
      <td className="r mono">{n2(r.auctioned_amount_crore)}</td>
      <td className="r mono">{n2(r.successful_market_bid_crore)}</td>
      <td className="r mono" style={{ color: "var(--fg-mute)" }}
          title="BB publishes a devolvement yield, not an amount">-</td>
      <td className="r mono">
        {r.coupon_not_applicable
          ? <span style={{ color: "var(--fg-mute)" }} title="Zero-coupon discount instrument">—</span>
          : r.coupon_pct == null
            ? "—"
            : <>
                {r.coupon_pct.toFixed(2)}%
                {r.coupon_is_current_reset && (
                  <span style={{ marginLeft: 4, fontSize: 9.5, color: "var(--info)" }}
                        title="FRTB coupon resets each period — this is the current reset, not necessarily the rate at this auction">
                    ↻
                  </span>
                )}
              </>}
      </td>
    </tr>
  );
}
