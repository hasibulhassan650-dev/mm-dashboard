import { api } from "@/lib/api";
import { sparseLabels, shortMonth } from "@/lib/terminal";
import GenericView, { type GenericData } from "@/components/terminal/views/GenericView";

export const revalidate = 300;

export default async function MonetaryPage() {
  const data = await api.monetary();
  const monthly = [...data.monthly].sort((a, b) => a.month.localeCompare(b.month));
  const latest = monthly[monthly.length - 1];
  const rr = data.reserve_requirements.current;

  const cpiRows = monthly.filter((m) => m.cpi_p2p != null);
  const rateRows = monthly.filter((m) => m.wavg_deposit != null && m.wavg_lending != null);

  const d: GenericData = {
    kpiFour: true,
    kpis: [
      { id: "cpi", label: "CPI (point-to-point)", value: latest?.cpi_p2p != null ? latest.cpi_p2p.toFixed(2) : "—", unit: "%", sub: latest ? shortMonth(latest.month) : "" },
      { id: "m2", label: "M2 growth", value: latest?.m2_growth != null ? latest.m2_growth.toFixed(2) : "—", unit: "%", sub: "broad money YoY" },
      { id: "pvt", label: "Pvt credit growth", value: latest?.private_credit_growth != null ? latest.private_credit_growth.toFixed(2) : "—", unit: "%", sub: "private sector YoY" },
      { id: "crr", label: "CRR / SLR", value: rr ? `${rr.crr ?? "—"}/${rr.slr ?? "—"}` : "—", unit: "%", sub: "reserve requirements" },
    ],
    charts: [
      {
        title: "CPI Inflation", sub: "point-to-point vs 12-month average", span: 6, height: 300,
        labels: sparseLabels(cpiRows.map((m) => shortMonth(m.month))),
        series: [
          { name: "p2p", data: cpiRows.map((m) => m.cpi_p2p as number), color: "var(--accent)" },
          { name: "12-mo avg", data: cpiRows.map((m) => (m.cpi_12mo_avg ?? m.cpi_p2p) as number), color: "var(--warn)", dashed: true },
        ],
      },
      {
        title: "Deposit vs Lending Rate", sub: "weighted-average · spread", span: 6, height: 300,
        labels: sparseLabels(rateRows.map((m) => shortMonth(m.month))),
        series: [
          { name: "Lending", data: rateRows.map((m) => m.wavg_lending as number), color: "var(--neg)" },
          { name: "Deposit", data: rateRows.map((m) => m.wavg_deposit as number), color: "var(--info)" },
        ],
      },
    ],
    tables: [{
      title: "Monetary & Prices — Monthly", sub: "CPI, aggregates, rates",
      cols: [
        { key: "month", label: "Month" },
        { key: "cpi", label: "CPI p2p", align: "r", mono: true, fmt: "pct" },
        { key: "m2", label: "M2 %", align: "r", mono: true, fmt: "pct" },
        { key: "pvt", label: "Pvt credit %", align: "r", mono: true, fmt: "pct" },
        { key: "dep", label: "Deposit %", align: "r", mono: true, fmt: "pct" },
        { key: "lend", label: "Lending %", align: "r", mono: true, fmt: "pct" },
      ],
      rows: [...monthly].reverse().map((m) => ({ month: shortMonth(m.month), cpi: m.cpi_p2p, m2: m.m2_growth, pvt: m.private_credit_growth, dep: m.wavg_deposit, lend: m.wavg_lending })),
    }],
  };
  return <GenericView d={d} />;
}
