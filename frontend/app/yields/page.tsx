import { api } from "@/lib/api";
import { buildCurve } from "@/lib/terminal";
import { bidToCover } from "@/lib/analytics";
import { fmtDate } from "@/lib/format";
import { Panel } from "@/components/terminal/ui";
import YieldsView from "@/components/terminal/views/YieldsView";
import AuctionExplorer from "@/components/terminal/AuctionExplorer";
import YieldCurveChartFull from "@/components/YieldCurveChartFull";
import YieldTrendChart from "@/components/YieldTrendChart";
import CurveSlopeChart from "@/components/CurveSlopeChart";
import BidCoverChart from "@/components/BidCoverChart";
import DownloadButton from "@/components/DownloadButton";
import Freshness from "@/components/Freshness";
import InfoTip from "@/components/InfoTip";

export const revalidate = 300;

export default async function YieldsPage() {
  const [primary, secondary, history, slope, fresh, range] = await Promise.all([
    api.yieldCurve().catch(() => []),
    api.yieldSecondary().catch(() => []),
    api.yields(12).catch(() => []),
    api.yieldSlope(24).catch(() => []),
    api.freshness(),
    api.yieldDateRange(),
  ]);
  const cb = buildCurve(primary, history);

  const tbills = primary.filter((r) => r.security_type === "T_BILL");
  const tbonds = primary.filter((r) => r.security_type !== "T_BILL");
  const kpi = (label: string, vals: number[], tenorLabel: string, color: string, hi: boolean) => {
    if (!vals.length) return null;
    const v = hi ? Math.max(...vals) : Math.min(...vals);
    return (
      <div className="kpi" key={label}>
        <div className="kpi-top"><span className="kpi-label">{label}</span></div>
        <div className="kpi-val"><span className="kpi-num" style={{ color }}>{v.toFixed(2)}</span><span className="kpi-unit">%</span></div>
        <div className="kpi-sub">{tenorLabel}</div>
      </div>
    );
  };
  const tbShort = tbills.slice().sort((a, b) => (a.tenor_years ?? 0) - (b.tenor_years ?? 0))[0]?.tenor_label ?? "";
  const tbLong = tbills.slice().sort((a, b) => (b.tenor_years ?? 0) - (a.tenor_years ?? 0))[0]?.tenor_label ?? "";
  const bdShort = tbonds.slice().sort((a, b) => (a.tenor_years ?? 0) - (b.tenor_years ?? 0))[0]?.tenor_label ?? "";
  const bdLong = tbonds.slice().sort((a, b) => (b.tenor_years ?? 0) - (a.tenor_years ?? 0))[0]?.tenor_label ?? "";

  return (
    <>
      <div style={{ marginBottom: "var(--gap)" }}><Freshness updated={fresh.yields} /></div>

      <div className="kpi-strip" style={{ gridTemplateColumns: "repeat(4,1fr)" }}>
        {kpi("T-Bill short end", tbills.map((r) => r.cutoff_yield_pct), tbShort, "var(--accent)", false)}
        {kpi("T-Bill long end", tbills.map((r) => r.cutoff_yield_pct), tbLong, "var(--accent)", true)}
        {kpi("T-Bond short end", tbonds.map((r) => r.cutoff_yield_pct), bdShort, "var(--info)", false)}
        {kpi("T-Bond long end", tbonds.map((r) => r.cutoff_yield_pct), bdLong, "var(--info)", true)}
      </div>

      <div className="grid12">
        <YieldsView d={{ tenors: cb.tenors, today: cb.today, weekAgo: cb.weekAgo, monthAgo: cb.monthAgo }} />
      </div>

      <div className="grid12" style={{ marginTop: "var(--gap)" }}>
        <AuctionExplorer bounds={range} />
      </div>

      <div className="grid12" style={{ marginTop: "var(--gap)" }}>
        <Panel title="Yield Curve — Primary & Secondary" sub="BB auctions (cut-off) vs GSOM secondary MTM" span={12}>
          <YieldCurveChartFull primary={primary} secondary={secondary} />
        </Panel>

        <Panel title="Curve Slope — Term Spreads" sub="2s10s & 10Y−91D (bps) · below 0 = inverted" span={6}>
          <CurveSlopeChart data={slope} />
        </Panel>
        <Panel title="Bid-to-Cover by Tenor" sub="amount bid ÷ accepted, latest auction" span={6}>
          <BidCoverChart rows={history} />
        </Panel>

        <Panel title="Yield Trend — Auction History" sub={`${history.length} cut-off observations across ${new Set(history.map((r) => r.tenor_label)).size} tenors`} span={12}>
          <YieldTrendChart data={history} />
        </Panel>

        <Panel title="Secondary Market (GSOM MTM)" sub={`${secondary.length} securities`} span={6} pad={false}>
          <div className="table-wrap" style={{ maxHeight: 340, overflowY: "auto" }}>
            <table className="dt">
              <thead><tr><th>Type</th><th>Security</th><th className="r">Rem (yr)</th><th className="r">Yield</th><th className="r">O/S (mn)</th></tr></thead>
              <tbody>
                {secondary.map((r, i) => (
                  <tr key={i}>
                    <td><span className="pill-inst">{r.security_type}</span></td>
                    <td style={{ maxWidth: 150, overflow: "hidden", textOverflow: "ellipsis" }}>{r.security_name_norm ?? r.isin}</td>
                    <td className="r mono">{r.remaining_years.toFixed(2)}</td>
                    <td className="r mono">{r.market_yield_pct.toFixed(4)}%</td>
                    <td className="r mono">{r.outstanding_bdt_mill?.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
        <Panel title="Primary Market (BB Treasury)" sub="latest per tenor" span={6} pad={false}>
          <div className="table-wrap" style={{ maxHeight: 340, overflowY: "auto" }}>
            <table className="dt">
              <thead><tr><th>Type</th><th>Tenor</th><th className="r">Yield</th><th className="r">Offered</th><th className="r">Accepted</th></tr></thead>
              <tbody>
                {primary.map((r, i) => (
                  <tr key={i}>
                    <td><span className="pill-inst">{r.security_type}</span></td>
                    <td className="mono">{r.tenor_label}</td>
                    <td className="r mono">{r.cutoff_yield_pct.toFixed(4)}%</td>
                    <td className="r mono">{r.offered_bdt_crore?.toLocaleString() ?? "—"}</td>
                    <td className="r mono">{r.accepted_bdt_crore?.toLocaleString() ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>

        <Panel title="Full Auction History" sub="last 12 months" span={12} pad={false}
          right={<DownloadButton data={history} filename="yields_auction_history" />}>
          <div className="table-wrap" style={{ maxHeight: 420, overflowY: "auto" }}>
            <table className="dt">
              <thead><tr><th>Auction Date</th><th>Type</th><th>Tenor</th><th className="r">Yield</th><th className="r">Offered (cr)</th><th className="r">Accepted (cr)</th><th className="r">B/C</th></tr></thead>
              <tbody>
                {history.map((r, i) => {
                  const bc = bidToCover(r);
                  return (
                    <tr key={i}>
                      <td>{fmtDate(r.auction_date)}</td>
                      <td><span className="pill-inst">{r.security_type}</span></td>
                      <td className="mono">{r.tenor_label}</td>
                      <td className="r mono">{r.cutoff_yield_pct.toFixed(4)}%</td>
                      <td className="r mono">{r.offered_bdt_crore?.toFixed(0) ?? "—"}</td>
                      <td className="r mono">{r.accepted_bdt_crore?.toFixed(0) ?? "—"}</td>
                      <td className="r mono"><span className={bc == null ? "" : bc < 1.1 ? "neg" : "pos"}>{bc == null ? "—" : `${bc.toFixed(2)}×`}</span></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Panel>
      </div>

      <p style={{ fontSize: 11, color: "var(--fg-mute)", marginTop: 14, display: "flex", gap: 4, flexWrap: "wrap" }}>
        <InfoTip term="GSOM" /> <InfoTip term="MTM" /> <InfoTip term="Bid-to-cover" /> <InfoTip term="Yield-curve slope (2s10s)" />
      </p>
    </>
  );
}
