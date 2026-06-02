import { api } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import GenericView, { type GenericData } from "@/components/terminal/views/GenericView";

export const revalidate = 300;

export default async function SecuritiesPage() {
  const secs = await api.securities().catch(() => []);
  const sorted = [...secs].sort((a, b) => a.maturity_date.localeCompare(b.maturity_date));
  const totalOut = secs.reduce((s, x) => s + (x.outstanding_bdt_mill || 0), 0) / 10; // → crore
  const bonds = secs.filter((s) => s.security_type !== "T_BILL").length;
  const bills = secs.length - bonds;

  const d: GenericData = {
    kpiFour: true,
    kpis: [
      { id: "n", label: "Securities", value: String(secs.length), sub: "live GSOM master" },
      { id: "out", label: "Outstanding", value: "৳" + Math.round(totalOut).toLocaleString(), unit: "cr", sub: "total face value" },
      { id: "bonds", label: "T-Bonds", value: String(bonds), sub: "fixed-coupon" },
      { id: "bills", label: "T-Bills", value: String(bills), sub: "zero-coupon" },
    ],
    tables: [{
      title: "Securities Master", sub: "ISIN · coupon · maturity · outstanding",
      cols: [
        { key: "isin", label: "ISIN", mono: true },
        { key: "name", label: "Security" },
        { key: "type", label: "Type", fmt: "pill" },
        { key: "coupon", label: "Coupon", align: "r", mono: true, fmt: "pct" },
        { key: "maturity", label: "Maturity", align: "r" },
        { key: "out", label: "Outstanding (cr)", align: "r", mono: true, fmt: "num" },
      ],
      rows: sorted.map((s) => ({
        isin: s.isin,
        name: s.security_name_norm,
        type: s.security_type === "T_BILL" ? "T-Bill" : "T-Bond",
        coupon: s.coupon_rate_pct,
        maturity: fmtDate(s.maturity_date),
        out: Math.round((s.outstanding_bdt_mill || 0) / 10),
      })),
    }],
  };
  return <GenericView d={d} />;
}
