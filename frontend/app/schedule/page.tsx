import Link from "next/link";
import { api, exportUrl } from "@/lib/api";
import type { Product, ScheduleByProduct, ScheduleMonth, ScheduleSummary } from "@/lib/api";
import { Panel } from "@/components/terminal/ui";
import { DataWarning } from "@/components/DataWarning";
import Freshness from "@/components/Freshness";
import HorizonControl from "@/components/HorizonControl";
import RelatedLinks from "@/components/RelatedLinks";
import ScheduleChart from "@/components/ScheduleChart";

export const revalidate = 300;

const LABEL: Record<string, string> = {
  T_BOND: "T-Bond", T_BILL: "T-Bill", FRTB: "FRTB", OTHER: "Other",
};
const MONTH = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function monthLabel(ym: string) {
  const [y, m] = ym.split("-");
  return `${MONTH[Number(m) - 1]} ${y}`;
}
function num(v: number) {
  return v ? Math.round(v).toLocaleString() : "—";
}

export default async function SchedulePage({
  searchParams,
}: { searchParams: Promise<{ years?: string }> }) {
  const sp = await searchParams;
  const asked = Number(sp.years);
  const years = [1, 2, 5, 10, 20].includes(asked) ? asked : 2;

  const [data, fresh] = await Promise.all([api.schedule(years), api.freshness()]);

  // The API deploys separately from this site and has served stale code before,
  // so an empty payload is reported as what it is rather than drawn as a month
  // of zeros.
  if (!data.months.length) {
    return (
      <>
        <DataWarning dataset={["coupons", "maturities", "calendar"]} />
        <Panel title="Redemption & Coupon Schedule" span={12}>
          <p style={{ fontSize: 13, color: "var(--warn)", margin: 0 }}>
            The schedule endpoint returned nothing. This is the API being behind the site,
            not an empty schedule — <code>/api/flows/by-product</code> needs the API project
            redeployed. No figures are shown rather than zeros, which would read as
            &ldquo;nothing matures&rdquo;.
          </p>
        </Panel>
      </>
    );
  }

  const products = data.products.filter(
    (p) => p !== "OTHER" || data.months.some((m) => m.redemption.OTHER || m.coupon.OTHER || m.auction.OTHER),
  );
  const t = data.totals;
  const cut = data.auction_calendar_to;
  const lastFlow = data.last_month_with_flows;
  // Trailing all-zero months are real (nothing is on issue that far out), but a
  // dozen bare rows look like a gap, so the table stops at the last dated flow
  // and says so underneath.
  const shown = lastFlow ? data.months.filter((m) => m.month <= lastFlow) : data.months;
  const hidden = data.months.length - shown.length;

  // Running cumulative net borrowing, anchored at the first month shown. Held
  // back on any month BB has not published auctions for -- compounding a figure
  // that is already an artefact of missing data would make the total read as a
  // forecast when it is not one.
  let run = 0;
  const cumByMonth = new Map<string, number | null>();
  for (const m of shown) {
    if (m.auction.status === "not_published") { cumByMonth.set(m.month, null); continue; }
    run += m.net_borrowing;
    cumByMonth.set(m.month, Math.round(run));
  }

  const cell = (v: number, cls = "") =>
    <td className={"r mono " + cls}>{num(v)}</td>;

  return (
    <>
      <DataWarning dataset={["coupons", "maturities", "calendar", "events-roll"]} />
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center",
                    gap: 12, marginBottom: "var(--gap)", flexWrap: "wrap" }}>
        <Freshness updated={fresh.securities} />
        <HorizonControl years={years} />
      </div>

      <Panel title="What This Shows" span={12}>
        <div style={{ fontSize: 12.5, lineHeight: 1.75, color: "var(--fg-mute)", maxWidth: 980 }}>
          <p style={{ marginTop: 0 }}>
            Every government-securities cash flow due from <b style={{ color: "var(--fg)" }}>
            {monthLabel(data.from.slice(0, 7))}</b> to <b style={{ color: "var(--fg)" }}>
            {monthLabel(data.to.slice(0, 7))}</b>, split by product and dated to the day the
            cash actually <b style={{ color: "var(--fg)" }}>settles</b> — when a payment falls on
            the Friday–Saturday weekend or a holiday, it appears on the next working day.
            All figures in <b style={{ color: "var(--fg)" }}>BDT crore</b>.
          </p>
          <p>
            <b style={{ color: "var(--fg)" }}>T-Bills carry no coupon.</b> They are zero-coupon
            discount instruments — the return is the difference between the discounted issue
            price and par, which arrives in the redemption column. The blank coupon cell is a
            fact about the instrument, not missing data.
          </p>
          <p style={{ marginBottom: 0 }}>
            <b style={{ color: "var(--info)" }}>Auctions are known only to {cut ?? "—"}.</b>{" "}
            Bangladesh Bank publishes its auction calendar about a year ahead; beyond that the
            auction column is marked <i>not published</i> rather than zero, because &ldquo;no
            auction&rdquo; and &ldquo;BB has not said yet&rdquo; are different claims and only
            the second is true. Redemptions and coupons run the full horizon — they are fixed
            by securities already on issue.
          </p>
        </div>
      </Panel>

      <div className="kpi-strip" style={{ gridTemplateColumns: "repeat(4,1fr)" }}>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">Principal Redemptions</span></div>
          <div className="kpi-val"><span className="kpi-num pos">{num(t.redemption.total)}</span>
            <span className="kpi-unit">cr</span></div>
          <div className="kpi-sub">
            T-Bond {num(t.redemption.T_BOND)} · T-Bill {num(t.redemption.T_BILL)} · FRTB {num(t.redemption.FRTB)}
          </div>
        </div>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">Coupons</span></div>
          <div className="kpi-val"><span className="kpi-num pos">{num(t.coupon.total)}</span>
            <span className="kpi-unit">cr</span></div>
          <div className="kpi-sub">T-Bond {num(t.coupon.T_BOND)} · FRTB {num(t.coupon.FRTB)} · bills n/a</div>
        </div>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">Total Inflow</span></div>
          <div className="kpi-val"><span className="kpi-num">{num(t.inflow_total)}</span>
            <span className="kpi-unit">cr</span></div>
          <div className="kpi-sub">over {years}Y · {t.months} months</div>
        </div>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">Auction Settlements</span></div>
          <div className="kpi-val"><span className="kpi-num neg">{num(t.auction.total)}</span>
            <span className="kpi-unit">cr</span></div>
          <div className="kpi-sub">
            {t.auction_months_published === t.months
              ? `all ${t.months} months published`
              : `only ${t.auction_months_published} of ${t.months} months published`}
          </div>
        </div>
      </div>

      {!t.net_borrowing_comparable && (
        <Panel span={12}>
          <p style={{ margin: 0, fontSize: 12, color: "var(--info)" }}>
            Net borrowing is not shown for this horizon. It is inflow minus auction outflow, and
            BB has published auctions for only {t.auction_months_published} of {t.months} months —
            netting full inflows against partial outflows would show a surplus that is an artefact
            of the missing calendar, not a forecast. Use the <b>1Y</b> horizon, or{" "}
            <Link href="/forecast" style={{ color: "var(--accent)" }}>Liquidity Forecast</Link>, for
            a net figure where both sides cover the same window.
          </p>
        </Panel>
      )}

      <div className="grid12">
        <Panel title="Redemptions & Coupons by Product" sub={`${data.from} → ${data.to}`} span={12}>
          <ScheduleChart data={data} />
        </Panel>

        <Panel
          title="Monthly Schedule"
          sub={`${shown.length} months · click a month to drill into its events`}
          span={12}
          pad={false}
          right={<a href={exportUrl.schedule(years)} className="seg-b"
                    style={{ textDecoration: "none" }}>Download {years}Y Excel</a>}
        >
          <div className="table-wrap" style={{ maxHeight: 620, overflowY: "auto" }}>
            <table className="dt">
              <thead>
                <tr>
                  <th rowSpan={2}>Month</th>
                  <th colSpan={products.length + 1} className="r">Principal Redemption (cr)</th>
                  <th colSpan={products.length + 1} className="r">Coupon (cr)</th>
                  <th rowSpan={2} className="r">Inflow (cr)</th>
                  <th colSpan={2} className="r">Auction Settlement (cr)</th>
                  <th rowSpan={2} className="r">Cumulative Net (cr)</th>
                </tr>
                <tr>
                  {products.map((p) => <th key={`r${p}`} className="r">{LABEL[p]}</th>)}
                  <th className="r">Total</th>
                  {products.map((p) => <th key={`c${p}`} className="r">{LABEL[p]}</th>)}
                  <th className="r">Total</th>
                  <th className="r">Amount</th>
                  <th>Basis</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((m) => <MonthRow key={m.month} m={m} products={products}
                                            couponProducts={data.coupon_products} cell={cell}
                                            cum={cumByMonth.get(m.month) ?? null} />)}
              </tbody>
              <tfoot>
                <tr style={{ borderTop: "2px solid var(--border)", fontWeight: 600 }}>
                  <td>{years}Y total</td>
                  {products.map((p) => cell(t.redemption[p]))}
                  {cell(t.redemption.total)}
                  {products.map((p) => cell(t.coupon[p]))}
                  {cell(t.coupon.total)}
                  {cell(t.inflow_total)}
                  {cell(t.auction.total, "neg")}
                  <td style={{ fontSize: 10.5, color: "var(--fg-mute)" }}>
                    {t.auction_months_published} of {t.months} mo
                  </td>
                  <td className="r mono">
                    {t.net_borrowing_comparable
                      ? Math.round(t.net_borrowing).toLocaleString()
                      : <span style={{ color: "var(--info)", fontSize: 10.5 }}>part-published</span>}
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>
          {hidden > 0 && (
            <p style={{ margin: 0, padding: "8px 12px", fontSize: 11.5, color: "var(--fg-mute)",
                        borderTop: "1px solid var(--border)" }}>
              No scheduled flows after {monthLabel(lastFlow!)} — the longest security on issue
              matures then, so the remaining {hidden} month{hidden === 1 ? "" : "s"} of this
              horizon are genuinely empty rather than unknown. They are omitted here and included
              in the export.
            </p>
          )}
        </Panel>

        <Panel title="Fiscal Year Summary" sub="Bangladesh FY runs July–June" span={12} pad={false}>
          <div className="table-wrap">
            <table className="dt">
              <thead>
                <tr>
                  <th>Fiscal Year</th><th className="r">Months</th>
                  {products.map((p) => <th key={`fr${p}`} className="r">Redeem {LABEL[p]}</th>)}
                  <th className="r">Redeem Total</th>
                  <th className="r">Coupon Total</th>
                  <th className="r">Inflow</th>
                  <th className="r">Auction</th>
                  <th className="r">Net Borrowing</th>
                </tr>
              </thead>
              <tbody>
                {data.fy_subtotals.map((f: ScheduleSummary) => (
                  <tr key={f.fiscal_year}>
                    <td className="mono">{f.fiscal_year}</td>
                    <td className="r mono">{f.months}</td>
                    {products.map((p) => cell(f.redemption[p]))}
                    {cell(f.redemption.total)}
                    {cell(f.coupon.total)}
                    {cell(f.inflow_total)}
                    {cell(f.auction.total, "neg")}
                    <td className="r mono">
                      {f.net_borrowing_comparable
                        ? <span className={f.net_borrowing > 0 ? "neg" : "pos"}>
                            {f.net_borrowing > 0 ? "▲" : "▼"} {Math.abs(Math.round(f.net_borrowing)).toLocaleString()}
                          </span>
                        : <span style={{ color: "var(--info)", fontSize: 10.5 }}
                                title={`BB has published auctions for ${f.auction_months_published} of ${f.months} months in this fiscal year`}>
                            part-published
                          </span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      </div>

      <RelatedLinks items={[
        { href: "/cashflows", label: "Cash Flows", why: "the same flows day by day" },
        { href: "/forecast", label: "Liquidity Forecast", why: "these flows netted into daily liquidity" },
        { href: "/securities", label: "Securities", why: "the instruments behind every figure here" },
      ]} />
    </>
  );
}

function MonthRow({ m, products, couponProducts, cell, cum }: {
  m: ScheduleMonth;
  products: Product[];
  couponProducts: Product[];
  cell: (v: number, cls?: string) => React.ReactElement;
  cum: number | null;
}) {
  const unknown = m.auction.status === "not_published";
  // Link to the drilldown for the first of the month — the day view is per-date,
  // so this is a starting point into the month rather than a month view.
  return (
    <tr>
      <td>
        <Link href={`/drilldown?date=${m.month}-01`} style={{ color: "var(--accent)" }}>
          {monthLabel(m.month)}
        </Link>
        <span style={{ marginLeft: 6, fontSize: 10, color: "var(--fg-mute)" }}>{m.fiscal_year}</span>
      </td>
      {products.map((p) => cell(m.redemption[p]))}
      {cell(m.redemption.total)}
      {products.map((p) => (
        <td key={`c${p}`} className="r mono">
          {couponProducts.includes(p)
            ? num(m.coupon[p])
            : <span title="Zero-coupon discount instrument — no coupon by construction"
                    style={{ color: "var(--fg-mute)" }}>n/a</span>}
        </td>
      ))}
      {cell(m.coupon.total)}
      {cell(m.inflow_total)}
      <td className="r mono neg">
        {unknown ? <span style={{ color: "var(--info)" }}>—</span> : num(m.auction.total)}
      </td>
      <td style={{ fontSize: 10.5 }}>
        {unknown
          ? <span style={{ color: "var(--info)" }} title="Bangladesh Bank has not published an auction calendar covering this month">
              not published
            </span>
          : m.auction.status === "partial"
            ? <span style={{ color: "var(--warn)" }} title="BB's published calendar ends part-way through this month">
                part month
              </span>
            : <span style={{ color: "var(--pos)" }}>published</span>}
      </td>
      <td className="r mono" style={{ fontWeight: 600 }}>
        {cum == null
          ? <span style={{ color: "var(--info)" }} title="Paused: BB has not published auctions for this month, so a running net would compound a gap">—</span>
          : <span className={cum > 0 ? "neg" : "pos"}>{cum.toLocaleString()}</span>}
      </td>
    </tr>
  );
}
