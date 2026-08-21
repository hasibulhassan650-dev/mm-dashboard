import { api } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import RelatedLinks from "@/components/RelatedLinks";
import DateRangeControl from "@/components/DateRangeControl";
import { resolveRange, inRange } from "@/lib/daterange";
import { OMO_CATS, pivotOmo } from "@/lib/terminal";
import { netLiquiditySeries } from "@/lib/analytics";
import Freshness from "@/components/Freshness";
import OmoView, { type OmoOpRow } from "@/components/terminal/views/OmoView";

export const revalidate = 300;

const INST_LABEL: Record<string, string> = {
  CB_REPO: "Repo", AR: "Assured Repo", IBLF: "IBLF", SLF: "SLF", SDF: "SDF",
};

export default async function OmoPage({ searchParams }: { searchParams: Promise<{ from?: string; to?: string }> }) {
  const sp = await searchParams;
  const [outAll, txnAll, fresh, status] = await Promise.all([
    api.omoOutstanding(365).catch(() => []),
    api.omoTransactions(365).catch(() => []),
    api.freshness(),
    api.status(),
  ]);
  // OMO-cadence integrity alerts (missing operation / no CB Repo on a working
  // Tuesday) — the class of BB press-release mistake that must never pass
  // silently. Shown here with the clarifying question the desk needs to answer.
  const omoAlerts = (status.data_health?.issues ?? [])
    .filter((i) => i.startsWith("omo:"))
    .map((i) => i.replace(/^omo:\s*/, ""));
  const range = resolveRange(sp, outAll.map((r) => r.date), 90);
  const outstanding = outAll.filter((r) => inRange(r.date, range.from, range.to));
  const txns = txnAll.filter((t) => inRange(t.transaction_date, range.from, range.to));

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

  // stance/latest reflect the CURRENT position (full data), not the chart window
  const latestNet = netLiquiditySeries(outAll).at(-1)?.net ?? null;
  const stance = latestNet == null ? null
    : latestNet > 0 ? { label: "Tight — net-injecting", tone: "tight" as const }
    : { label: "Flush — net-absorbing", tone: "flush" as const };

  return (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, marginBottom: "var(--gap)", flexWrap: "wrap" }}>
        <Freshness updated={fresh.omo} />
        <DateRangeControl min={range.min} max={range.max} from={range.from} to={range.to} />
      </div>

      {omoAlerts.length > 0 && (
        <div style={{
          border: "1px solid var(--warn)", borderRadius: "var(--radius-sm)",
          padding: "10px 14px", marginBottom: "var(--gap)", fontSize: 12.5,
          color: "var(--warn)", background: "color-mix(in oklab, var(--warn) 10%, transparent)",
        }}>
          <b>⚠ OMO CADENCE ANOMALY — possible Bangladesh Bank press-release mistake.</b>
          <ul style={{ margin: "6px 0 0", paddingLeft: 18, lineHeight: 1.6 }}>
            {omoAlerts.map((a, i) => <li key={i}>{a}</li>)}
          </ul>
          <div style={{ marginTop: 6, color: "var(--fg-mute)" }}>
            BB runs an OMO every working day and CB Repo is the Tuesday operation. A missing day —
            or a duplicate/mislabeled &ldquo;as on&rdquo; release — is auto-repaired where possible
            (the CB Repo is re-homed to its Tuesday); anything left here needs you to confirm whether
            that day was a bank holiday.
          </div>
        </div>
      )}

      <div className="grid12">
        <OmoView d={{ omoSeries, omoCats: OMO_CATS, ops, outstanding, stance, latestNet }} />
      </div>
      <RelatedLinks items={[
        { href: "/forecast", label: "Liquidity Forecast", why: "when these repos mature & drain" },
        { href: "/callmoney", label: "Call Money", why: "where the corridor stance shows up" },
        { href: "/refrate", label: "Reference Rates", why: "repo/SDF/SLF benchmark rates" },
      ]} />
    </>
  );
}
