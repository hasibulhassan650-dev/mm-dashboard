import { api } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { Panel } from "@/components/terminal/ui";
import InterbankFxChart from "@/components/InterbankFxChart";
import DateRangeControl from "@/components/DateRangeControl";
import { resolveRange, inRange } from "@/lib/daterange";
import DownloadButton from "@/components/DownloadButton";
import Freshness from "@/components/Freshness";
import RelatedLinks from "@/components/RelatedLinks";
import { DataWarning } from "@/components/DataWarning";

export const revalidate = 300;

// BB quotes JPY, INR and LKR per unit like the rest, but they sit orders of
// magnitude below the majors — shown with more decimals so they stay readable.
const SMALL_UNIT = new Set(["JPY", "INR", "LKR"]);
const fmtRate = (v: number | null, ccy: string) =>
  v == null ? "—" : v.toFixed(SMALL_UNIT.has(ccy) ? 4 : 4);

export default async function FxMarketPage({ searchParams }: { searchParams: Promise<{ from?: string; to?: string }> }) {
  const sp = await searchParams;
  const [allFx, allRates, fresh] = await Promise.all([
    api.interbankFx(3650),
    api.fxRates(3650),
    api.freshness(),
  ]);

  const range = resolveRange(sp, allFx.map((r) => r.trade_date), 180);
  const fx = allFx.filter((r) => inRange(r.trade_date, range.from, range.to));

  const spot = allFx.filter((r) => r.segment === "SPOT");
  const latestSpot = spot[0] ?? null, prevSpot = spot[1] ?? null;
  const warDelta = latestSpot?.war_rate != null && prevSpot?.war_rate != null
    ? latestSpot.war_rate - prevSpot.war_rate : null;

  // Latest published rates: one row per currency on the most recent trade date.
  const latestRateDate = allRates[0]?.rate_date ?? null;
  const latestRates = allRates.filter((r) => r.rate_date === latestRateDate);
  const usd = latestRates.find((r) => r.currency === "USD") ?? null;
  const crosses = latestRates.filter((r) => r.currency !== "USD");

  const inRangeSpot = fx.filter((r) => r.segment === "SPOT");
  const turnover = fx.reduce((s, r) => s + (r.volume_usd_mn ?? 0), 0);
  const spotTurnover = inRangeSpot.reduce((s, r) => s + (r.volume_usd_mn ?? 0), 0);

  return (
    <>
      <DataWarning dataset={["fxmarket", "fxrates", "cross-source"]} />
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, marginBottom: "var(--gap)", flexWrap: "wrap" }}>
        <Freshness updated={fresh.fxmarket} />
        <DateRangeControl min={range.min} max={range.max} from={range.from} to={range.to} />
      </div>

      <div className="kpi-strip" style={{ gridTemplateColumns: "repeat(4,1fr)" }}>
        <div className="kpi">
          <div className="kpi-top">
            <span className="kpi-label">USD/BDT Spot WAR</span>
            {warDelta != null && (
              <span className={"delta " + (warDelta <= 0 ? "pos" : "neg")}>
                <svg viewBox="0 0 12 12" width="9" height="9" style={{ transform: warDelta >= 0 ? "none" : "rotate(180deg)" }}><path d="M6 2 L10 8 L2 8 Z" fill="currentColor" /></svg>
                {Math.abs(warDelta).toFixed(4)}
              </span>
            )}
          </div>
          <div className="kpi-val"><span className="kpi-num" style={{ color: "var(--info)" }}>{latestSpot?.war_rate?.toFixed(4) ?? "—"}</span></div>
          <div className="kpi-sub">interbank · {latestSpot ? fmtDate(latestSpot.trade_date) : "—"}</div>
        </div>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">Day Range</span></div>
          <div className="kpi-val"><span className="kpi-num" style={{ fontSize: "calc(var(--kpi-num) * 0.72)" }}>
            {latestSpot?.low_rate != null && latestSpot?.high_rate != null ? `${latestSpot.low_rate.toFixed(2)}/${latestSpot.high_rate.toFixed(2)}` : "—"}
          </span></div>
          <div className="kpi-sub">low / high · {latestSpot?.num_deals ?? "—"} deals</div>
        </div>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">Spot Turnover</span></div>
          <div className="kpi-val"><span className="kpi-num">{latestSpot?.volume_usd_mn?.toFixed(1) ?? "—"}</span><span className="kpi-unit">M$</span></div>
          <div className="kpi-sub">${Math.round(spotTurnover).toLocaleString()}m in range</div>
        </div>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">All Segments</span></div>
          <div className="kpi-val"><span className="kpi-num">{Math.round(turnover).toLocaleString()}</span><span className="kpi-unit">M$</span></div>
          <div className="kpi-sub">spot + forward + swap, in range</div>
        </div>
      </div>

      <div className="grid12">
        <Panel title="Interbank FX Turnover & Spot Rate"
          sub="turnover by segment (bars) · spot weighted-average rate (line)" span={12}>
          <InterbankFxChart data={fx} />
        </Panel>

        <Panel title="Exchange Rate of the Taka"
          sub={latestRateDate ? `all currencies · trading day ${fmtDate(latestRateDate)}` : "no data yet"}
          span={12} pad={false}
          right={<DownloadButton data={allRates} filename="bb_exchange_rates" />}>
          <div style={{ padding: "10px 14px 0", fontSize: 11, color: "var(--fg-mute)", lineHeight: 1.5 }}>
            Mid = (bid + ask) / 2. These are <b>Bangladesh Bank&apos;s published rates</b>, not live dealable
            quotes: USD is the interbank high/low at Dhaka close, and the crosses are derived from it
            against New York and Dhaka closes.{usd?.published_date && <> BB published them on {fmtDate(usd.published_date)} for
            the trading day above — the two dates are deliberately kept apart.</>}
          </div>
          <div className="table-wrap" style={{ maxHeight: 420, overflowY: "auto", marginTop: 8 }}>
            <table className="dt">
              <thead><tr><th>Currency</th><th className="r">Bid</th><th className="r">Ask</th><th className="r">Mid</th><th className="r">Spread</th><th className="r">WAR</th></tr></thead>
              <tbody>
                {usd && (
                  <tr key="USD" style={{ background: "color-mix(in oklab, var(--accent) 7%, transparent)" }}>
                    <td><b>USD</b></td>
                    <td className="r mono">{fmtRate(usd.bid_rate, "USD")}</td>
                    <td className="r mono">{fmtRate(usd.ask_rate, "USD")}</td>
                    <td className="r mono" style={{ color: "var(--info)", fontWeight: 600 }}>{fmtRate(usd.mid_rate, "USD")}</td>
                    <td className="r mono">{usd.bid_rate != null && usd.ask_rate != null ? (usd.ask_rate - usd.bid_rate).toFixed(4) : "—"}</td>
                    <td className="r mono" style={{ color: "var(--warn)" }}>{fmtRate(usd.war_rate, "USD")}</td>
                  </tr>
                )}
                {crosses.map((r) => (
                  <tr key={r.currency}>
                    <td>{r.currency}</td>
                    <td className="r mono">{fmtRate(r.bid_rate, r.currency)}</td>
                    <td className="r mono">{fmtRate(r.ask_rate, r.currency)}</td>
                    <td className="r mono" style={{ color: "var(--info)" }}>{fmtRate(r.mid_rate, r.currency)}</td>
                    <td className="r mono">{r.bid_rate != null && r.ask_rate != null ? (r.ask_rate - r.bid_rate).toFixed(4) : "—"}</td>
                    <td className="r mono" style={{ color: "var(--fg-mute)" }}>—</td>
                  </tr>
                ))}
                {latestRates.length === 0 && (
                  <tr><td colSpan={6} style={{ textAlign: "center", color: "var(--fg-mute)", height: 70 }}>
                    No published rates stored yet.
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>
        </Panel>

        <Panel title="Interbank FX History" sub={`${fx.length} rows`} span={12} pad={false}
          right={<DownloadButton data={fx} filename="interbank_fx" />}>
          <div className="table-wrap" style={{ maxHeight: 440, overflowY: "auto" }}>
            <table className="dt">
              <thead><tr><th>Date</th><th>Segment</th><th className="r">Deals</th><th className="r">Volume $m</th><th className="r">Low</th><th className="r">High</th><th className="r">WAR</th></tr></thead>
              <tbody>
                {fx.map((r, i) => (
                  <tr key={i}>
                    <td>{fmtDate(r.trade_date)}</td>
                    <td><span className="pill-inst">{r.segment}</span></td>
                    <td className="r mono">{r.num_deals ?? "—"}</td>
                    <td className="r mono">{r.volume_usd_mn?.toFixed(2) ?? "—"}</td>
                    <td className="r mono">{r.low_rate?.toFixed(4) ?? "—"}</td>
                    <td className="r mono">{r.high_rate?.toFixed(4) ?? "—"}</td>
                    <td className="r mono" style={{ color: "var(--info)" }}>{r.war_rate?.toFixed(4) ?? "—"}</td>
                  </tr>
                ))}
                {fx.length === 0 && (
                  <tr><td colSpan={7} style={{ textAlign: "center", color: "var(--fg-mute)", height: 70 }}>No FX turnover in this window.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </Panel>
      </div>

      <RelatedLinks items={[
        { href: "/fx", label: "FX Auctions", why: "BB's own interventions in this market" },
        { href: "/macro", label: "External Sector", why: "reserves behind the rate" },
        { href: "/repo", label: "Repo", why: "the taka side of the funding picture" },
      ]} />
    </>
  );
}
