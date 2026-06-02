import { api } from "@/lib/api";
import { fmtDate, fmtDateShort } from "@/lib/format";
import { sparseLabels } from "@/lib/terminal";
import GenericView, { type GenericData } from "@/components/terminal/views/GenericView";

export const revalidate = 300;

export default async function FxPage() {
  const fx = await api.fx(365).catch(() => []);
  const sorted = [...fx].sort((a, b) => a.auction_date.localeCompare(b.auction_date));
  const dated = sorted.filter((r) => (r.weighted_avg_rate ?? r.cutoff_rate) != null);
  const rate = dated.map((r) => (r.weighted_avg_rate ?? r.cutoff_rate) as number);
  const labels = sparseLabels(dated.map((r) => fmtDateShort(r.auction_date)));
  const last = dated[dated.length - 1];

  const d: GenericData = {
    kpis: [
      { id: "rate", label: "USD / BDT (wtd-avg)", value: last?.weighted_avg_rate != null ? last.weighted_avg_rate.toFixed(2) : "—", sub: "latest auction", dir: "flat", series: rate.slice(-30) },
      { id: "cut", label: "Cut-off rate", value: last?.cutoff_rate != null ? last.cutoff_rate.toFixed(2) : "—", sub: "latest auction", dir: "flat" },
      { id: "acc", label: "Accepted", value: last?.accepted_amount_usd_mill != null ? last.accepted_amount_usd_mill.toLocaleString() : "—", unit: "M$", sub: last ? fmtDate(last.auction_date) : "" },
      { id: "n", label: "Auctions (1y)", value: String(fx.length), sub: "buy / sell windows" },
    ],
    charts: [
      { title: "USD / BDT — Auction Rate", sub: "weighted-average accepted rate", labels, series: [{ name: "USD/BDT", data: rate, color: "var(--accent)" }], height: 320, yUnit: "" },
    ],
    tables: [{
      title: "FX Auctions", sub: "buy / sell interventions",
      cols: [
        { key: "date", label: "Date" },
        { key: "type", label: "Type", fmt: "pill" },
        { key: "bids", label: "Bids", align: "r", mono: true, fmt: "num" },
        { key: "bid", label: "Bid (M$)", align: "r", mono: true, fmt: "num1" },
        { key: "acc", label: "Accepted (M$)", align: "r", mono: true, fmt: "num1" },
        { key: "cut", label: "Cut-off", align: "r", mono: true, fmt: "num2" },
        { key: "wavg", label: "Wtd Avg", align: "r", mono: true, fmt: "num2" },
      ],
      rows: [...dated].reverse().slice(0, 40).map((r) => ({
        date: fmtDate(r.auction_date), type: r.auction_type, bids: r.num_bids,
        bid: r.bid_amount_usd_mill, acc: r.accepted_amount_usd_mill, cut: r.cutoff_rate, wavg: r.weighted_avg_rate,
      })),
    }],
  };
  return <GenericView d={d} />;
}
