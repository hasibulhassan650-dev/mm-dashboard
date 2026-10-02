"use client";
import DownloadButton from "./DownloadButton";
import type { ScheduleByProduct, ScheduleDetail } from "@/lib/api";

/**
 * Two-sheet export of the schedule: the monthly table, and every coupon and
 * maturity behind it so any figure can be traced to the securities paying it.
 *
 * Months past the end of BB's auction calendar export BLANK auction cells, not
 * zeros — the same rule the page follows. A zero in a spreadsheet is a number
 * someone will sum; "BB has not published" is not.
 */
const LABEL: Record<string, string> = { T_BOND: "T-Bond", T_BILL: "T-Bill", FRTB: "FRTB", OTHER: "Other" };

export default function ScheduleExport({ data, detail }: { data: ScheduleByProduct; detail: ScheduleDetail }) {
  // The OTHER bucket exists to catch an unmatched ISIN or a product BB adds
  // later. Show the column only when something is actually in it.
  const hasOther = data.months.some(
    (m) => m.redemption.OTHER || m.coupon.OTHER || m.auction.OTHER
  );
  const products = data.products.filter((p) => p !== "OTHER" || hasOther);

  const monthly = data.months.map((m) => {
    const unknown = m.auction.status === "not_published";
    const row: Record<string, string | number | null> = {
      Month: m.month,
      "Fiscal Year": m.fiscal_year,
    };
    for (const p of products) row[`Redemption ${LABEL[p]}`] = m.redemption[p];
    row["Redemption Total"] = m.redemption.total;
    for (const p of products) {
      row[`Coupon ${LABEL[p]}`] = data.coupon_products.includes(p) ? m.coupon[p] : null;
    }
    row["Coupon Total"] = m.coupon.total;
    row["Inflow Total"] = m.inflow_total;
    for (const p of products) row[`Auction ${LABEL[p]}`] = unknown ? null : m.auction[p];
    row["Auction Total"] = unknown ? null : m.auction.total;
    row["Auction Data"] = unknown ? "BB has not published" : m.auction.status;
    row["Net Borrowing"] = unknown ? null : m.net_borrowing;
    return row;
  });

  const fy = data.fy_subtotals.map((f) => {
    const row: Record<string, string | number | null> = { "Fiscal Year": f.fiscal_year ?? "", Months: f.months };
    for (const p of products) row[`Redemption ${LABEL[p]}`] = f.redemption[p];
    row["Redemption Total"] = f.redemption.total;
    for (const p of products) {
      row[`Coupon ${LABEL[p]}`] = data.coupon_products.includes(p) ? f.coupon[p] : null;
    }
    row["Coupon Total"] = f.coupon.total;
    row["Auction Total"] = f.auction.total;
    row["Auction Months Published"] = `${f.auction_months_published} of ${f.months}`;
    row["Net Borrowing"] = f.net_borrowing_comparable ? f.net_borrowing : null;
    return row;
  });

  const rows = detail.rows.map((r) => ({
    Kind: r.kind === "REDEMPTION" ? "Redemption (principal)" : "Coupon",
    Month: r.month,
    "Payment Date": r.payment_date,
    "Scheduled Date": r.scheduled_date,
    Product: LABEL[r.product] ?? r.product,
    ISIN: r.isin,
    Security: r.security,
    "Coupon Rate %": r.coupon_rate_pct,
    "Amount (crore)": r.amount_crore,
    "Amount (mn)": r.amount_mill,
  }));

  return (
    <DownloadButton
      filename={`bd_gsec_schedule_${data.years}y_${data.from}_${data.to}`}
      label={`Download ${data.years}Y Excel`}
      sheets={[
        { name: "Monthly by product", rows: monthly },
        { name: "Fiscal year summary", rows: fy },
        { name: "Every coupon & maturity", rows },
      ]}
    />
  );
}
