import { api } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { OMO_CATS, pivotOmo } from "@/lib/terminal";
import OmoView, { type OmoOpRow } from "@/components/terminal/views/OmoView";

export const revalidate = 300;

const INST_LABEL: Record<string, string> = {
  CB_REPO: "Repo", AR: "Assured Repo", IBLF: "IBLF", SLF: "SLF", SDF: "SDF",
};

export default async function OmoPage() {
  const [outstanding, txns] = await Promise.all([
    api.omoOutstanding(60).catch(() => []),
    api.omoTransactions(60).catch(() => []),
  ]);
  const omoSeries = pivotOmo(outstanding);
  const ops: OmoOpRow[] = txns
    .filter((t) => t.accepted_bdt_crore > 0)
    .slice(0, 30)
    .map((t) => ({
      date: fmtDate(t.transaction_date),
      inst: INST_LABEL[t.instrument] || t.instrument,
      tenor: t.tenor_label,
      accepted: Math.round(t.accepted_bdt_crore),
      rate: t.rate_pct,
      maturity: fmtDate(t.maturity_date),
      direction: t.direction,
    }));
  return <OmoView d={{ omoSeries, omoCats: OMO_CATS, ops }} />;
}
