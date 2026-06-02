import { api } from "@/lib/api";
import { fmtDate, fmtDateShort } from "@/lib/format";
import { sparseLabels } from "@/lib/terminal";
import GenericView, { type GenericData } from "@/components/terminal/views/GenericView";

export const revalidate = 300;

// values arrive in BDT million → display in crore (÷10)
const cr = (mn: number) => mn / 10;

export default async function CashFlowsPage() {
  const flows = await api.flows(6).catch(() => []);
  const sorted = [...flows].sort((a, b) => a.flow_date.localeCompare(b.flow_date));
  const labels = sparseLabels(sorted.map((f) => fmtDateShort(f.flow_date)), 10);

  const sumIn = sorted.reduce((s, f) => s + f.total_inflow_bdt_mill, 0);
  const sumOut = sorted.reduce((s, f) => s + f.auction_outflow_planned_mill, 0);
  const sumNet = sorted.reduce((s, f) => s + f.net_borrowing_bdt_mill, 0);

  const d: GenericData = {
    kpiFour: true,
    kpis: [
      { id: "in", label: "Total Inflow", value: "৳" + Math.round(cr(sumIn)).toLocaleString(), unit: "cr", sub: "coupons + maturities" },
      { id: "out", label: "Total Outflow", value: "৳" + Math.round(cr(sumOut)).toLocaleString(), unit: "cr", sub: "auction settlements" },
      { id: "net", label: "Net Borrowing", value: "৳" + Math.round(cr(sumNet)).toLocaleString(), unit: "cr", sub: "net of redemptions" },
      { id: "n", label: "Flow Days", value: String(sorted.length), sub: "6-month window" },
    ],
    charts: [
      {
        title: "Government Cash Flows", sub: "inflow vs auction outflow · ৳ crore", height: 320, yUnit: "",
        labels,
        series: [
          { name: "Inflow", data: sorted.map((f) => +cr(f.total_inflow_bdt_mill).toFixed(0)), color: "var(--pos)" },
          { name: "Outflow", data: sorted.map((f) => +cr(f.auction_outflow_planned_mill).toFixed(0)), color: "var(--neg)", dashed: true },
        ],
      },
    ],
    tables: [{
      title: "Daily Flows", sub: "coupons, maturities, auctions",
      cols: [
        { key: "date", label: "Date" },
        { key: "coupon", label: "Coupon (cr)", align: "r", mono: true, fmt: "num" },
        { key: "principal", label: "Maturity (cr)", align: "r", mono: true, fmt: "num" },
        { key: "inflow", label: "Inflow (cr)", align: "r", mono: true, fmt: "num" },
        { key: "outflow", label: "Outflow (cr)", align: "r", mono: true, fmt: "num" },
        { key: "net", label: "Net (cr)", align: "r", mono: true, fmt: "signedBps" },
      ],
      rows: [...sorted].reverse().filter((f) => f.total_inflow_bdt_mill > 0 || f.auction_outflow_planned_mill > 0).slice(0, 50).map((f) => ({
        date: fmtDate(f.flow_date),
        coupon: Math.round(cr(f.coupon_inflow_bdt_mill)),
        principal: Math.round(cr(f.principal_inflow_bdt_mill)),
        inflow: Math.round(cr(f.total_inflow_bdt_mill)),
        outflow: Math.round(cr(f.auction_outflow_planned_mill)),
        net: Math.round(cr(f.net_borrowing_bdt_mill)),
      })),
    }],
  };
  return <GenericView d={d} />;
}
