import { api } from "@/lib/api";
import { sparseLabels, shortMonth } from "@/lib/terminal";
import GenericView, { type GenericData } from "@/components/terminal/views/GenericView";

export const revalidate = 300;

export default async function MacroPage() {
  const macro = await api.macro();
  const series = [...macro.series].sort((a, b) => a.month.localeCompare(b.month));

  const resRows = series.filter((s) => s.gross_reserves_usd_bn != null && s.net_reserves_bpm6_usd_bn != null);
  const remRows = series.filter((s) => s.remittance_usd_mn != null);
  const lastRes = resRows[resRows.length - 1];
  const lastRem = remRows[remRows.length - 1];

  const d: GenericData = {
    kpiFour: true,
    kpis: [
      { id: "gross", label: "Gross Reserves", value: lastRes ? lastRes.gross_reserves_usd_bn!.toFixed(2) : "—", unit: "B$", sub: lastRes ? shortMonth(lastRes.month) : "" },
      { id: "net", label: "Net (BPM6)", value: lastRes ? lastRes.net_reserves_bpm6_usd_bn!.toFixed(2) : "—", unit: "B$", sub: "IMF basis" },
      { id: "rem", label: "Remittance", value: lastRem ? lastRem.remittance_usd_mn!.toLocaleString() : "—", unit: "M$", sub: lastRem ? shortMonth(lastRem.month) : "" },
      { id: "remb", label: "Remittance (12m)", value: remRows.length ? (remRows.slice(-12).reduce((s, r) => s + (r.remittance_usd_mn ?? 0), 0) / 1000).toFixed(2) : "—", unit: "B$", sub: "trailing year" },
    ],
    charts: [
      {
        title: "Foreign-Exchange Reserves", sub: "gross vs net (BPM6) · USD bn", span: 7, height: 300, yUnit: "",
        labels: sparseLabels(resRows.map((r) => shortMonth(r.month))),
        series: [
          { name: "Gross", data: resRows.map((r) => r.gross_reserves_usd_bn as number), color: "var(--accent)" },
          { name: "Net BPM6", data: resRows.map((r) => r.net_reserves_bpm6_usd_bn as number), color: "var(--info)", dashed: true },
        ],
      },
      {
        title: "Wage-Earner Remittance", sub: "monthly inflow · USD mn", span: 5, height: 300, yUnit: "",
        labels: sparseLabels(remRows.map((r) => shortMonth(r.month))),
        series: [{ name: "Remittance", data: remRows.map((r) => r.remittance_usd_mn as number), color: "var(--accent)" }],
      },
    ],
    tables: [{
      title: "Monthly Detail", sub: "reserves & remittance",
      cols: [
        { key: "month", label: "Month" },
        { key: "gross", label: "Gross (B$)", align: "r", mono: true, fmt: "num2" },
        { key: "net", label: "Net BPM6 (B$)", align: "r", mono: true, fmt: "num2" },
        { key: "rem", label: "Remittance (M$)", align: "r", mono: true, fmt: "num" },
      ],
      rows: [...series].reverse().map((s) => ({ month: shortMonth(s.month), gross: s.gross_reserves_usd_bn, net: s.net_reserves_bpm6_usd_bn, rem: s.remittance_usd_mn })),
    }],
  };
  return <GenericView d={d} />;
}
