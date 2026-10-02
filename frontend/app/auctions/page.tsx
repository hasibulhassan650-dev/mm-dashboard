import Link from "next/link";
import { api, exportUrl } from "@/lib/api";
import type { AuctionRow } from "@/lib/api";
import { Panel } from "@/components/terminal/ui";
import { DataWarning } from "@/components/DataWarning";
import AuctionVolumeChart from "@/components/AuctionVolumeChart";
import DateRangeControl from "@/components/DateRangeControl";
import Freshness from "@/components/Freshness";
import NextAuction from "@/components/NextAuction";
import RelatedLinks from "@/components/RelatedLinks";
import { fmtDate } from "@/lib/format";

export const revalidate = 300;

const LABEL: Record<string, string> = {
  T_BOND: "T-Bond", T_BILL: "T-Bill", FRTB: "FRTB", OTHER: "Other",
};

function iso(d: Date) { return d.toISOString().slice(0, 10); }
function shift(from: string, days: number) {
  const d = new Date(from + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() + days); return iso(d);
}
const num = (v: number | null | undefined) =>
  v == null ? "—" : Math.round(v).toLocaleString();

export default async function AuctionsPage({
  searchParams,
}: { searchParams: Promise<{ from?: string; to?: string }> }) {
  const sp = await searchParams;
  const today = iso(new Date());
  // Default window: six months back, and forward to wherever BB's calendar
  // reaches — the planned auctions are the point of the page.
  const from = sp.from ?? shift(today, -183);
  const to = sp.to ?? shift(today, 120);

  const [book, next, fresh] = await Promise.all([
    api.auctionBook({ from, to }), api.nextAuction(), api.freshness(),
  ]);

  if (!book.count && !book.calendar_published_to) {
    return (
      <>
        <DataWarning dataset={["auctions", "calendar"]} />
        <Panel title="Treasury Auctions" span={12}>
          <p style={{ margin: 0, fontSize: 13, color: "var(--warn)" }}>
            The auction endpoint returned nothing. This is the API being behind the site, not an
            empty auction book — <code>/api/securities/auctions</code> needs the API project
            redeployed. No figures are shown rather than zeros.
          </p>
        </Panel>
      </>
    );
  }

  const horizon = book.calendar_published_to;
  const t = book.totals;
  const products = book.products.filter((p) => book.by_product[p]?.auctions);
  const dupes = book.auctions.filter((a) => a.duplicate_results).length;
  const minDate = "2007-01-01";
  const maxDate = horizon ? shift(horizon, 30) : shift(today, 180);

  return (
    <>
      <DataWarning dataset={["auctions", "calendar", "yields"]} />
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center",
                    gap: 12, marginBottom: "var(--gap)", flexWrap: "wrap" }}>
        <Freshness updated={fresh.yields} />
        <DateRangeControl min={minDate} max={maxDate} from={from} to={to} />
      </div>

      <NextAuction data={next} />

      <div className="kpi-strip" style={{ gridTemplateColumns: "repeat(4,1fr)" }}>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">Auctions in Window</span></div>
          <div className="kpi-val"><span className="kpi-num">{t.auctions}</span></div>
          <div className="kpi-sub">{t.confirmed} settled · {t.planned} still planned</div>
        </div>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">Notified</span></div>
          <div className="kpi-val"><span className="kpi-num">{num(t.notified_crore)}</span>
            <span className="kpi-unit">cr</span></div>
          <div className="kpi-sub">what BB offered</div>
        </div>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">Accepted</span></div>
          <div className="kpi-val"><span className="kpi-num neg">{num(t.accepted_crore)}</span>
            <span className="kpi-unit">cr</span></div>
          <div className="kpi-sub">cash the market paid in</div>
        </div>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">Calendar Published To</span></div>
          <div className="kpi-val"><span className="kpi-num" style={{ fontSize: 17 }}>
            {horizon ? fmtDate(horizon) : "—"}</span></div>
          <div className="kpi-sub">nothing beyond is known, not nothing scheduled</div>
        </div>
      </div>

      <div className="grid12">
        <Panel title="By Product" sub={`${from} → ${to} · BDT crore`} span={12} pad={false}>
          <div className="table-wrap">
            <table className="dt">
              <thead><tr>
                <th>Product</th>
                <th className="r">Auctions</th>
                <th className="r">Settled</th>
                <th className="r">Planned</th>
                <th className="r">Notified</th>
                <th className="r">Accepted</th>
                <th className="r">Accepted / Notified</th>
                <th className="r">Avg Cut-off</th>
              </tr></thead>
              <tbody>
                {products.map((p) => {
                  const v = book.by_product[p];
                  return (
                    <tr key={p}>
                      <td style={{ fontWeight: 600 }}>{LABEL[p] ?? p}</td>
                      <td className="r mono">{v.auctions}</td>
                      <td className="r mono pos">{v.confirmed}</td>
                      <td className="r mono">{v.planned || ""}</td>
                      <td className="r mono">{num(v.notified_crore)}</td>
                      <td className="r mono">{num(v.accepted_crore)}</td>
                      <td className="r mono">
                        {v.accepted_share == null ? "—" : `${(v.accepted_share * 100).toFixed(0)}%`}
                      </td>
                      <td className="r mono">
                        {v.avg_cutoff_pct == null ? "—" : `${v.avg_cutoff_pct.toFixed(4)}%`}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p style={{ margin: 0, padding: "8px 12px", fontSize: 11.5, color: "var(--fg-mute)",
                      borderTop: "1px solid var(--border)", lineHeight: 1.7 }}>
            <b style={{ color: "var(--fg)" }}>Notified</b> is the amount BB offered;
            {" "}<b style={{ color: "var(--fg)" }}>accepted</b> is what BB took, and the cash the
            market actually paid. They differ on a devolved or under-subscribed auction, so only
            accepted is a cash flow. The average cut-off is weighted by amount accepted — a
            35,000 cr bill and a 500 cr FRTB are not equal observations of &ldquo;the average
            yield&rdquo;. Planned auctions carry no cut-off and are excluded from it.
          </p>
        </Panel>

        <Panel title="Monthly Auction Volume by Product"
          sub="accepted amount · accepted-weighted cut-off on the right axis" span={12}>
          <AuctionVolumeChart auctions={book.auctions} products={book.products} />
        </Panel>

        <Panel
          title="Every Auction"
          sub={`${book.count} in this window · newest first`}
          span={12}
          pad={false}
          right={<a href={exportUrl.auctions({ from, to })} className="seg-b"
                    style={{ textDecoration: "none" }}>Download Excel</a>}
        >
          <div className="table-wrap" style={{ maxHeight: 620, overflowY: "auto" }}>
            <table className="dt">
              <thead><tr>
                <th>Auction</th><th>Settles</th><th>Product</th><th>Tenor</th>
                <th className="r">Notified (cr)</th>
                <th className="r">Bids (cr)</th>
                <th className="r">Accepted (cr)</th>
                <th className="r">Cut-off</th>
                <th className="r">Bid/Cover</th>
                <th>Status</th>
              </tr></thead>
              <tbody>
                {book.auctions.map((a, i) => <AuctionRowView key={i} a={a} />)}
              </tbody>
              <tfoot>
                <tr style={{ borderTop: "2px solid var(--border)", fontWeight: 600 }}>
                  <td colSpan={4}>Total · {t.auctions} auctions</td>
                  <td className="r mono">{num(t.notified_crore)}</td>
                  <td className="r mono"></td>
                  <td className="r mono">{num(t.accepted_crore)}</td>
                  <td colSpan={3}></td>
                </tr>
              </tfoot>
            </table>
          </div>
          <p style={{ margin: 0, padding: "8px 12px", fontSize: 11.5, color: "var(--fg-mute)",
                      borderTop: "1px solid var(--border)", lineHeight: 1.7 }}>
            A <b style={{ color: "var(--warn)" }}>PLANNED</b> row is BB&rsquo;s calendar target,
            not a settled amount — its accepted, cut-off and cover cells are blank because the
            auction has not happened. <b style={{ color: "var(--fg)" }}>Bid/Cover</b> is bids
            over accepted, and is left blank where bids are unknown or exactly equal accepted: in
            the earlier era BB published a fixed weekly target in that column, so a ratio of
            1.00 there is an artefact rather than a fully-covered auction.
            {horizon && <> Nothing is listed after {fmtDate(horizon)} because BB has not published
            the calendar that far — BB auctions bills nearly every week, so that is a gap in
            what is announced, not a gap in issuance.</>}
          </p>
          {dupes > 0 && (
            <p style={{ margin: 0, padding: "0 12px 8px", fontSize: 11.5, color: "var(--warn)" }}>
              ⚠ {dupes} auction{dupes === 1 ? "" : "s"} matched more than one published result.
              The amounts are not doubled here, but it means the yields table holds a duplicate —
              see the Guardian page.
            </p>
          )}
        </Panel>
      </div>

      <RelatedLinks items={[
        { href: "/yields", label: "Yield Curve", why: "where these cut-offs sit on the curve" },
        { href: "/schedule", label: "Schedule by Product", why: "these auctions netted against redemptions" },
        { href: "/forecast", label: "Liquidity Forecast", why: "the day each settlement drains cash" },
      ]} />
    </>
  );
}

function AuctionRowView({ a }: { a: AuctionRow }) {
  const planned = a.status !== "CONFIRMED";
  return (
    <tr style={planned ? { background: "color-mix(in oklab, var(--warn) 6%, transparent)" } : undefined}>
      <td className="mono">
        {a.auction_date ? fmtDate(a.auction_date) : "—"}
        {a.auction_no && <span style={{ marginLeft: 6, fontSize: 10, color: "var(--fg-mute)" }}>
          #{a.auction_no}</span>}
      </td>
      <td className="mono">
        {a.settlement_date
          ? <Link href={`/drilldown?date=${a.settlement_date}`} style={{ color: "var(--accent)" }}>
              {fmtDate(a.settlement_date)}
            </Link>
          : "—"}
      </td>
      <td>{LABEL[a.product] ?? a.product}</td>
      <td className="mono">{a.tenor_label ?? "—"}</td>
      <td className="r mono">{num(a.notified_crore)}</td>
      <td className="r mono">{num(a.bids_crore)}</td>
      <td className="r mono">{num(a.accepted_crore)}</td>
      <td className="r mono">
        {a.cutoff_yield_pct == null ? "—" : `${a.cutoff_yield_pct.toFixed(4)}%`}
      </td>
      <td className="r mono">
        {a.bid_to_cover == null
          ? <span style={{ color: "var(--fg-mute)" }} title="Bids unknown, or bids equal accepted — see the note below">—</span>
          : `${a.bid_to_cover.toFixed(2)}x`}
      </td>
      <td style={{ fontSize: 10.5 }}>
        {planned
          ? <span style={{ color: "var(--warn)" }} title="BB's published calendar target — not yet held">
              planned
            </span>
          : <span style={{ color: "var(--pos)" }}>settled</span>}
      </td>
    </tr>
  );
}
