import { api } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { OMO_CATS, pivotOmo } from "@/lib/terminal";
import { netLiquiditySeries } from "@/lib/analytics";
import Freshness from "@/components/Freshness";
import OmoView, { type OmoOpRow } from "@/components/terminal/views/OmoView";

export const revalidate = 300;

const INST_LABEL: Record<string, string> = {
  CB_REPO: "Repo", AR: "Assured Repo", IBLF: "IBLF", SLF: "SLF", SDF: "SDF",
};

export default async function OmoPage() {
  const [outstanding, txns, fresh] = await Promise.all([
    api.omoOutstanding(90).catch(() => []),
    api.omoTransactions(60).catch(() => []),
    api.freshness(),
  ]);
  const omoSeries = pivotOmo(outstanding);
  const ops: OmoOpRow[] = txns
    .filter((t) => t.accepted_bdt_crore > 0)
    .slice(0, 40)
    .map((t) => ({
      date: fmtDate(t.transaction_date),
      inst: INST_LABEL[t.instrument] || t.instrument,
      tenor: t.tenor_label,
      accepted: Math.round(t.accepted_bdt_crore),
      rate: t.rate_pct,
      rateRange: t.rate_range,
      maturity: fmtDate(t.maturity_date),
      direction: t.direction,
    }));

  const netSeries = netLiquiditySeries(outstanding);
  const latestNet = netSeries.at(-1)?.net ?? null;
  const stance = latestNet == null ? null
    : latestNet > 0 ? { label: "Tight — net-injecting", tone: "tight" as const }
    : { label: "Flush — net-absorbing", tone: "flush" as const };

  return (
    <>
      <div style={{ marginBottom: "var(--gap)" }}><Freshness updated={fresh.omo} /></div>
      <div className="grid12">
        <OmoView d={{ omoSeries, omoCats: OMO_CATS, ops, outstanding, stance, latestNet }} />
      </div>
    </>
  );
}
