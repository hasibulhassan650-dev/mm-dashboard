import { api } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { Panel } from "@/components/terminal/ui";
import FxChart from "@/components/FxChart";
import DateRangeControl from "@/components/DateRangeControl";
import { resolveRange, inRange } from "@/lib/daterange";
import DownloadButton from "@/components/DownloadButton";
import Freshness from "@/components/Freshness";
import RelatedLinks from "@/components/RelatedLinks";

export const revalidate = 300;

export default async function FxPage({ searchParams }: { searchParams: Promise<{ from?: string; to?: string }> }) {
  const sp = await searchParams;
  const [allRows, fresh] = await Promise.all([api.fx(3650).catch(() => []), api.freshness()]);
  const range = resolveRange(sp, allRows.map((r) => r.auction_date), 365);
  const rows = allRows.filter((r) => inRange(r.auction_date, range.from, range.to));

  const latest = allRows[0] ?? null, prev = allRows[1] ?? null;
  const rateDelta = latest?.weighted_avg_rate != null && prev?.weighted_avg_rate != null ? latest.weighted_avg_rate - prev.weighted_avg_rate : null;
  const totalAcc = rows.reduce((s, r) => s + (r.accepted_amount_usd_mill ?? 0), 0);
  const totalBid = rows.reduce((s, r) => s + (r.bid_amount_usd_mill ?? 0), 0);
  const hitRate = totalBid > 0 ? (totalAcc / totalBid) * 100 : null;

  return (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, marginBottom: "var(--gap)", flexWrap: "wrap" }}>
        <Freshness updated={fresh.fx} />
        <DateRangeControl min={range.min} max={range.max} from={range.from} to={range.to} />
      </div>

      <div className="kpi-strip" style={{ gridTemplateColumns: "repeat(4,1fr)" }}>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">Wtd-Avg Rate</span>{rateDelta != null && <span className={"delta " + (rateDelta <= 0 ? "pos" : "neg")}><svg viewBox="0 0 12 12" width="9" height="9" style={{ transform: rateDelta >= 0 ? "none" : "rotate(180deg)" }}><path d="M6 2 L10 8 L2 8 Z" fill="currentColor" /></svg>{Math.abs(rateDelta).toFixed(4)}</span>}</div>
          <div className="kpi-val"><span className="kpi-num" style={{ color: "var(--info)" }}>{latest?.weighted_avg_rate?.toFixed(2) ?? "—"}</span></div>
          <div className="kpi-sub">latest · {latest ? fmtDate(latest.auction_date) : "—"}</div>
        </div>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">Cut-off Rate</span></div>
          <div className="kpi-val"><span className="kpi-num" style={{ color: "var(--warn)" }}>{latest?.cutoff_rate?.toFixed(2) ?? "—"}</span></div>
          <div className="kpi-sub">{latest?.auction_type ?? "—"}</div>
        </div>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">Accepted</span></div>
          <div className="kpi-val"><span className="kpi-num">{latest?.accepted_amount_usd_mill?.toFixed(1) ?? "—"}</span><span className="kpi-unit">M$</span></div>
          <div className="kpi-sub">{latest?.num_accepted ?? "—"} of {latest?.num_bids ?? "—"} bids</div>
        </div>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">Bid-Hit Ratio</span></div>
          <div className="kpi-val"><span className="kpi-num">{hitRate != null ? hitRate.toFixed(1) : "—"}</span><span className="kpi-unit">%</span></div>
          <div className="kpi-sub">${totalAcc.toFixed(0)}m of ${totalBid.toFixed(0)}m bid</div>
        </div>
      </div>

      <div className="grid12">
        <Panel title="USD / BDT — Rate & Volume" sub="weighted-avg & cut-off (lines) · accepted $m (bars)" span={12}>
          <FxChart data={rows} />
        </Panel>
        <Panel title="Auction History" sub={`${rows.length} auctions`} span={12} pad={false}
          right={<DownloadButton data={rows} filename="fx_auction_history" />}>
          <div className="table-wrap" style={{ maxHeight: 460, overflowY: "auto" }}>
            <table className="dt">
              <thead><tr><th>Date</th><th>Settle</th><th>Type</th><th className="r">Bids</th><th className="r">Bid $m</th><th>Range</th><th className="r">Accepted</th><th className="r">Acc $m</th><th className="r">Cut-off</th><th className="r">Wtd Avg</th></tr></thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i}>
                    <td>{fmtDate(r.auction_date)}</td>
                    <td>{fmtDate(r.settlement_date)}</td>
                    <td><span className="pill-inst">{r.auction_type}</span></td>
                    <td className="r mono">{r.num_bids ?? "—"}</td>
                    <td className="r mono">{r.bid_amount_usd_mill?.toFixed(1) ?? "—"}</td>
                    <td className="mono" style={{ fontSize: 11 }}>{r.bid_range ?? "—"}</td>
                    <td className="r mono">{r.num_accepted ?? "—"}</td>
                    <td className="r mono">{r.accepted_amount_usd_mill?.toFixed(1) ?? "—"}</td>
                    <td className="r mono" style={{ color: "var(--warn)" }}>{r.cutoff_rate?.toFixed(4) ?? "—"}</td>
                    <td className="r mono" style={{ color: "var(--info)" }}>{r.weighted_avg_rate?.toFixed(4) ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      </div>
      <RelatedLinks items={[
        { href: "/macro", label: "External Sector", why: "reserves the interventions defend" },
        { href: "/monetary", label: "Monetary", why: "policy backdrop" },
        { href: "/refrate", label: "Reference Rates", why: "money-market benchmarks" },
      ]} />
    </>
  );
}
