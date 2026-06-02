import { api } from "@/lib/api";
import { fmtDate, fmtDateShort } from "@/lib/format";
import { sparseLabels } from "@/lib/terminal";
import GenericView, { type GenericData } from "@/components/terminal/views/GenericView";
import type { RefRateRow } from "@/lib/api";

export const revalidate = 300;

const PROD = "Overnight";

export default async function RefRatePage() {
  const rows = await api.refrate(90).catch(() => [] as RefRateRow[]);
  const sorted = [...rows].sort((a, b) => a.trade_date.localeCompare(b.trade_date));
  const dates = [...new Set(sorted.map((r) => r.trade_date))];

  const rateAt = (date: string, type: string, product = PROD) =>
    sorted.find((r) => r.trade_date === date && r.rate_type === type && r.product === product)?.rate_pct ?? null;

  const common = dates.filter((d) => rateAt(d, "DOMMR") != null && rateAt(d, "BOFR") != null);
  const labels = sparseLabels(common.map((d) => fmtDateShort(d)));
  const dommr = common.map((d) => rateAt(d, "DOMMR") as number);
  const bofr = common.map((d) => rateAt(d, "BOFR") as number);

  const latest = dates[dates.length - 1];
  const products = [...new Set(rows.map((r) => r.product))];
  const latestRows = sorted.filter((r) => r.trade_date === latest);

  const d: GenericData = {
    kpiFour: true,
    kpis: [
      { id: "dommr", label: "DOMMR · O/N", value: dommr.length ? dommr[dommr.length - 1].toFixed(2) : "—", unit: "%", sub: "domestic money mkt" },
      { id: "bofr", label: "BOFR · O/N", value: bofr.length ? bofr[bofr.length - 1].toFixed(2) : "—", unit: "%", sub: "bank overnight" },
      { id: "asof", label: "As of", value: latest ? fmtDateShort(latest) : "—", sub: "latest trade date" },
      { id: "prod", label: "Products", value: String(products.length), sub: "tenors quoted" },
    ],
    charts: [
      { title: "Reference Rates — Overnight", sub: "DOMMR vs BOFR", labels, height: 320, series: [
        { name: "DOMMR", data: dommr, color: "var(--accent)" },
        { name: "BOFR", data: bofr, color: "var(--info)", dashed: true },
      ] },
    ],
    tables: [{
      title: "Latest Breakdown", sub: latest ? fmtDate(latest) : "by product",
      cols: [
        { key: "type", label: "Rate", fmt: "pill" },
        { key: "product", label: "Product" },
        { key: "rate", label: "Rate", align: "r", mono: true, fmt: "pct" },
        { key: "amount", label: "Amount (cr)", align: "r", mono: true, fmt: "num" },
        { key: "deals", label: "Deals", align: "r", mono: true, fmt: "num" },
      ],
      rows: latestRows.map((r) => ({ type: r.rate_type, product: r.product, rate: r.rate_pct, amount: r.amount_crore, deals: r.num_deals })),
    }],
  };
  return <GenericView d={d} />;
}
