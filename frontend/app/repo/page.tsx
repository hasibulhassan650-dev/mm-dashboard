import { api } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { Panel } from "@/components/terminal/ui";
import RepoChart from "@/components/RepoChart";
import DateRangeControl from "@/components/DateRangeControl";
import { resolveRange, inRange } from "@/lib/daterange";
import DownloadButton from "@/components/DownloadButton";
import Freshness from "@/components/Freshness";
import RelatedLinks from "@/components/RelatedLinks";
import { DataWarning } from "@/components/DataWarning";

export const revalidate = 300;

export default async function RepoPage({ searchParams }: { searchParams: Promise<{ from?: string; to?: string }> }) {
  const sp = await searchParams;
  const [allRows, cm, fresh] = await Promise.all([
    api.interbankRepo(3650),
    api.callmoney(365).catch(() => ({ daily_summary: [], latest_breakdown: [], latest_date: null })),
    api.freshness(),
  ]);

  const range = resolveRange(sp, allRows.map((r) => r.trade_date), 180);
  const rows = allRows.filter((r) => inRange(r.trade_date, range.from, range.to));

  // Overnight call money by date, so each repo day can be shown against it.
  const callByDate = new Map<string, number>();
  for (const d of cm.daily_summary ?? []) {
    if (d.overnight_wavg_rate != null) callByDate.set(d.trade_date, d.overnight_wavg_rate);
  }

  const latest = allRows.find((r) => r.war_pct != null) ?? null;
  const prev = allRows.filter((r) => r.war_pct != null)[1] ?? null;
  const warDelta = latest?.war_pct != null && prev?.war_pct != null ? latest.war_pct - prev.war_pct : null;
  const latestCall = latest ? callByDate.get(latest.trade_date) ?? null : null;
  const spread = latest?.war_pct != null && latestCall != null ? latest.war_pct - latestCall : null;
  const turnover = rows.reduce((s, r) => s + (r.amount_crore ?? 0), 0);

  return (
    <>
      <DataWarning dataset={["repo", "cross-source"]} />
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, marginBottom: "var(--gap)", flexWrap: "wrap" }}>
        <Freshness updated={fresh.repo} />
        <DateRangeControl min={range.min} max={range.max} from={range.from} to={range.to} />
      </div>

      <div className="kpi-strip" style={{ gridTemplateColumns: "repeat(4,1fr)" }}>
        <div className="kpi">
          <div className="kpi-top">
            <span className="kpi-label">Repo WAR</span>
            {warDelta != null && (
              <span className={"delta " + (warDelta <= 0 ? "pos" : "neg")}>
                <svg viewBox="0 0 12 12" width="9" height="9" style={{ transform: warDelta >= 0 ? "none" : "rotate(180deg)" }}><path d="M6 2 L10 8 L2 8 Z" fill="currentColor" /></svg>
                {Math.abs(warDelta).toFixed(2)}
              </span>
            )}
          </div>
          <div className="kpi-val"><span className="kpi-num">{latest?.war_pct?.toFixed(2) ?? "—"}</span><span className="kpi-unit">%</span></div>
          <div className="kpi-sub">secured · {latest ? fmtDate(latest.trade_date) : "—"}</div>
        </div>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">vs Call O/N</span></div>
          <div className="kpi-val">
            <span className="kpi-num" style={{ color: spread != null && Math.abs(spread) > 0.5 ? "var(--warn)" : "var(--info)" }}>
              {spread != null ? `${spread >= 0 ? "+" : ""}${(spread * 100).toFixed(0)}` : "—"}
            </span><span className="kpi-unit">bp</span>
          </div>
          <div className="kpi-sub">call o/n {latestCall != null ? `${latestCall.toFixed(2)}%` : "—"}</div>
        </div>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">Turnover</span></div>
          <div className="kpi-val"><span className="kpi-cur">৳</span><span className="kpi-num">{latest?.amount_crore != null ? Math.round(latest.amount_crore).toLocaleString() : "—"}</span><span className="kpi-unit">cr</span></div>
          <div className="kpi-sub">{latest?.num_deals ?? "—"} deals</div>
        </div>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">Range Total</span></div>
          <div className="kpi-val"><span className="kpi-cur">৳</span><span className="kpi-num">{Math.round(turnover / 1000).toLocaleString()}</span><span className="kpi-unit">k cr</span></div>
          <div className="kpi-sub">{rows.length} trading days</div>
        </div>
      </div>

      <div className="grid12">
        <Panel title="Repo Rate vs Call Money" sub="secured (repo) against unsecured overnight · turnover in bars" span={12}>
          <RepoChart data={rows} call={Object.fromEntries(callByDate)} />
        </Panel>
        <Panel title="Interbank Repo History" sub={`${rows.length} trading days`} span={12} pad={false}
          right={<DownloadButton data={rows} filename="interbank_repo" />}>
          <div style={{ padding: "10px 14px 0", fontSize: 11, color: "var(--fg-mute)", lineHeight: 1.5 }}>
            BB&apos;s interbank repo trades <b>1–7 days</b>, not overnight, so a small positive spread over
            overnight call money is term premium rather than stress. A blank rate is a genuine no-trade
            day — BB prints a row of zeros, and a 0% repo rate would be a fiction.
          </div>
          <div className="table-wrap" style={{ maxHeight: 440, overflowY: "auto", marginTop: 8 }}>
            <table className="dt">
              <thead><tr><th>Date</th><th className="r">Deals</th><th className="r">Amount (cr)</th><th className="r">Tenor</th><th className="r">Rate Range</th><th className="r">WAR</th><th className="r">Call O/N</th><th className="r">Spread</th></tr></thead>
              <tbody>
                {rows.map((r, i) => {
                  const call = callByDate.get(r.trade_date) ?? null;
                  const sp2 = r.war_pct != null && call != null ? r.war_pct - call : null;
                  return (
                    <tr key={i}>
                      <td>{fmtDate(r.trade_date)}</td>
                      <td className="r mono">{r.num_deals ?? "—"}</td>
                      <td className="r mono">{r.amount_crore != null ? Math.round(r.amount_crore).toLocaleString() : "—"}</td>
                      <td className="r mono">{r.tenor_min_days != null ? `${r.tenor_min_days}–${r.tenor_max_days}d` : "—"}</td>
                      <td className="r mono">{r.rate_min_pct != null ? `${r.rate_min_pct.toFixed(2)}–${r.rate_max_pct?.toFixed(2)}` : "—"}</td>
                      <td className="r mono" style={{ color: "var(--info)" }}>{r.war_pct?.toFixed(2) ?? "—"}</td>
                      <td className="r mono" style={{ color: "var(--fg-dim)" }}>{call?.toFixed(2) ?? "—"}</td>
                      <td className="r mono" style={{ color: sp2 != null && Math.abs(sp2) > 0.5 ? "var(--warn)" : "var(--fg-dim)" }}>
                        {sp2 != null ? `${sp2 >= 0 ? "+" : ""}${(sp2 * 100).toFixed(0)}bp` : "—"}
                      </td>
                    </tr>
                  );
                })}
                {rows.length === 0 && (
                  <tr><td colSpan={8} style={{ textAlign: "center", color: "var(--fg-mute)", height: 70 }}>No repo trading in this window.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </Panel>
      </div>

      <RelatedLinks items={[
        { href: "/callmoney", label: "Call Money", why: "the unsecured side of the same market" },
        { href: "/omo", label: "OMO", why: "BB's own repo operations" },
        { href: "/fxmarket", label: "FX Market", why: "the other half of bank funding" },
      ]} />
    </>
  );
}
