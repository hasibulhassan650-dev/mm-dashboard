import { api, BacktestRow, ForecastRow } from "@/lib/api";
import RelatedLinks from "@/components/RelatedLinks";
import { Panel } from "@/components/terminal/ui";
import ForecastIntervalChart from "@/components/ForecastIntervalChart";
import DownloadButton from "@/components/DownloadButton";
import { fmtDate, fmtPct, bps } from "@/lib/format";

export const revalidate = 300;

const DASH = "—";
const pct1 = (v: number | null) => (v == null ? DASH : `${(v * 100).toFixed(0)}%`);

export default async function ResearchPage() {
  const fc = await api.forecast();

  const byTenor = new Map<string, { naive?: BacktestRow; momentum?: BacktestRow }>();
  fc.metrics.forEach(m => {
    const e = byTenor.get(m.tenor) ?? {};
    e[m.model] = m;
    byTenor.set(m.tenor, e);
  });

  // Sort tenors short → long so every table reads like the curve.
  const tenorOrder = (t: string) => {
    const m = /^(\d+(?:\.\d+)?)(D|Y)/.exec(t);
    return m ? parseFloat(m[1]) * (m[2] === "D" ? 1 / 365 : 1) : 999;
  };
  const tenors = [...byTenor.keys()].sort((a, b) => tenorOrder(a) - tenorOrder(b));

  const momentum: ForecastRow[] = fc.forecasts
    .filter(f => f.model === "momentum")
    .sort((a, b) => tenorOrder(a.tenor) - tenorOrder(b.tenor));

  const beats = tenors.filter(t => {
    const e = byTenor.get(t)!;
    return e.momentum?.mae_bps != null && e.naive?.mae_bps != null && e.momentum.mae_bps < e.naive.mae_bps;
  });
  const sharpest = [...fc.metrics].filter(m => m.mae_bps != null)
    .sort((a, b) => a.mae_bps! - b.mae_bps!)[0] ?? null;
  const scored = fc.track_record.length;

  const empty = fc.run_date == null;

  return (
    <>
      {empty && (
        <div style={{
          border: "1px solid var(--warn)", borderRadius: "var(--radius-sm)",
          padding: "8px 12px", marginBottom: "var(--gap)", fontSize: 12.5,
          color: "var(--warn)", background: "color-mix(in oklab, var(--warn) 10%, transparent)",
        }}>
          ⚠ NO FORECAST RUN YET — the weekly job (Thursday) writes into
          <span className="mono"> auction_forecasts</span>. Until it runs, this tab has nothing to display.
        </div>
      )}

      <div className="kpi-strip" style={{ gridTemplateColumns: "repeat(4,1fr)" }}>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">Forecast Run</span></div>
          <div className="kpi-val"><span className="kpi-num">{fc.run_date ? fmtDate(fc.run_date) : DASH}</span></div>
          <div className="kpi-sub">{tenors.length} tenors modelled · refits weekly</div>
        </div>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">Momentum Beats Naive</span></div>
          <div className="kpi-val">
            <span className="kpi-num" style={{ color: beats.length ? "var(--pos)" : "var(--fg)" }}>
              {tenors.length ? `${beats.length}/${tenors.length}` : DASH}
            </span>
          </div>
          <div className="kpi-sub">{beats.length ? beats.join(" · ") : "on MAE, out-of-sample"}</div>
        </div>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">Sharpest Tenor</span></div>
          <div className="kpi-val">
            <span className="kpi-num">{sharpest ? sharpest.mae_bps!.toFixed(1) : DASH}</span>
            <span className="kpi-unit">bps MAE</span>
          </div>
          <div className="kpi-sub">{sharpest ? `${sharpest.tenor} · ${sharpest.model}` : "no metrics yet"}</div>
        </div>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">Live Track Record</span></div>
          <div className="kpi-val"><span className="kpi-num">{scored}</span><span className="kpi-unit">scored</span></div>
          <div className="kpi-sub">{scored ? "published forecasts vs actual prints" : "builds as auctions print"}</div>
        </div>
      </div>

      <div className="grid12">
        <Panel title="Next Auction — Forecast Change per Tenor" span={12}
          sub="change from the last cutoff, in bps · bar = 95% band from the model's own out-of-sample error">
          <ForecastIntervalChart rows={fc.forecasts} />
        </Panel>

        <Panel title="Next Auction Forecast — Momentum Model" span={12} pad={false}
          sub="point forecast and 95% band in yield terms"
          right={<DownloadButton data={fc.forecasts} filename="auction_forecasts" />}>
          <div className="table-wrap">
            <table className="dt">
              <thead><tr>
                <th>Tenor</th><th>Instrument</th><th>Target Auction</th>
                <th className="r">Last Print</th><th className="r">Forecast</th>
                <th className="r">Change</th><th className="r">95% Low</th><th className="r">95% High</th>
              </tr></thead>
              <tbody>
                {momentum.map((f, i) => {
                  const chg = f.last_actual_yield != null ? f.point_yield - f.last_actual_yield : null;
                  return (
                    <tr key={i}>
                      <td style={{ fontWeight: 600 }}>{f.tenor}</td>
                      <td style={{ color: "var(--fg-mute)" }}>{f.instrument ?? DASH}</td>
                      <td>{fmtDate(f.target_auction_date)}</td>
                      <td className="r mono">{fmtPct(f.last_actual_yield, 4)}</td>
                      <td className="r mono" style={{ fontWeight: 600 }}>{fmtPct(f.point_yield, 4)}</td>
                      <td className="r mono" style={{ color: chg == null ? undefined : chg < 0 ? "var(--pos)" : "var(--neg)" }}>
                        {chg == null ? DASH : bps(chg)}
                      </td>
                      <td className="r mono" style={{ color: "var(--fg-mute)" }}>{fmtPct(f.lo_yield, 4)}</td>
                      <td className="r mono" style={{ color: "var(--fg-mute)" }}>{fmtPct(f.hi_yield, 4)}</td>
                    </tr>
                  );
                })}
                {momentum.length === 0 && (
                  <tr><td colSpan={8} style={{ textAlign: "center", color: "var(--fg-mute)", padding: 16 }}>
                    No forecasts stored yet.
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>
        </Panel>

        <Panel title="Backtest — Model vs Naive" span={12} pad={false}
          sub="expanding-window, one-step-ahead, out-of-sample · the naive column is the bar every model must clear"
          right={<DownloadButton data={fc.metrics} filename="backtest_metrics" />}>
          <div className="table-wrap">
            <table className="dt">
              <thead><tr>
                <th>Tenor</th>
                <th className="r">Naive MAE</th><th className="r">Momentum MAE</th><th className="r">Edge</th>
                <th className="r">Naive RMSE</th><th className="r">Momentum RMSE</th>
                <th className="r">Dir. Acc.</th><th className="r">Within ±5bps</th><th className="r">n</th>
              </tr></thead>
              <tbody>
                {tenors.map(t => {
                  const e = byTenor.get(t)!;
                  const edge = e.naive?.mae_bps != null && e.momentum?.mae_bps != null
                    ? e.naive.mae_bps - e.momentum.mae_bps : null;
                  return (
                    <tr key={t}>
                      <td style={{ fontWeight: 600 }}>{t}</td>
                      <td className="r mono">{e.naive?.mae_bps?.toFixed(2) ?? DASH}</td>
                      <td className="r mono">{e.momentum?.mae_bps?.toFixed(2) ?? DASH}</td>
                      <td className="r mono" style={{ fontWeight: 600, color: edge == null ? undefined : edge > 0 ? "var(--pos)" : "var(--neg)" }}>
                        {edge == null ? DASH : `${edge > 0 ? "+" : ""}${edge.toFixed(2)}`}
                      </td>
                      <td className="r mono" style={{ color: "var(--fg-mute)" }}>{e.naive?.rmse_bps?.toFixed(2) ?? DASH}</td>
                      <td className="r mono" style={{ color: "var(--fg-mute)" }}>{e.momentum?.rmse_bps?.toFixed(2) ?? DASH}</td>
                      <td className="r mono">{pct1(e.momentum?.dir_acc ?? null)}</td>
                      <td className="r mono">{pct1(e.momentum?.hit_5bps ?? null)}</td>
                      <td className="r mono" style={{ color: "var(--fg-mute)" }}>{e.momentum?.n_obs ?? e.naive?.n_obs ?? DASH}</td>
                    </tr>
                  );
                })}
                {tenors.length === 0 && (
                  <tr><td colSpan={9} style={{ textAlign: "center", color: "var(--fg-mute)", padding: 16 }}>
                    No backtest metrics stored yet.
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>
        </Panel>

        <Panel title="How to Read This" span={12}>
          <div style={{ fontSize: 12.5, lineHeight: 1.7, color: "var(--fg-mute)", maxWidth: 900 }}>
            <p><b style={{ color: "var(--fg)" }}>Two models, and the honest scoreboard between them.</b>{" "}
            <b style={{ color: "var(--fg)" }}>Naive</b> says the next cutoff equals the last one — on a rate that
            moves in small weekly steps, that is a genuinely hard benchmark. <b style={{ color: "var(--fg)" }}>Momentum</b>{" "}
            adds β × the last change, with β fitted by OLS on how much one change has historically carried into the next.
            <b style={{ color: "var(--fg)" }}> Edge</b> is naive MAE minus momentum MAE: positive means momentum earned
            its keep on that tenor, negative means it did not and you should read the naive number instead.</p>

            <p><b style={{ color: "var(--fg)" }}>The band is measured, not assumed.</b> It is ±1.96 × the model&apos;s own
            out-of-sample RMSE from an expanding-window backtest — refit on everything up to week t, predict week t,
            score, roll forward. No future data ever touches a fit, so the width is what this model actually delivered,
            not what a distributional assumption would flatter it into claiming.</p>

            <p><b style={{ color: "var(--fg)" }}>Trust bills, discount bonds.</b> 91D/182D/364D auction weekly, so they
            have hundreds of prints and error bands tens of bps wide. The 2Y–20Y bonds auction roughly monthly and have
            only ~35 prints since 2023 — their bands run over a full percentage point, which is the model correctly
            telling you it cannot call a monthly bond auction. A wide band is information, not a defect.</p>

            <p><b style={{ color: "var(--fg)" }}>Target auction dates are inferred</b> from the median recent gap between
            prints (7d for bills, ~28d for bonds), because BB&apos;s published auction calendar is not ingested yet.
            Expect them to shift around holidays.</p>

            <p style={{ color: "var(--fg-dim)" }}><b style={{ color: "var(--fg)" }}>Method &amp; roadmap.</b> Everything
            here is fitted weekly in GitHub Actions and written to the database; this page and its API only read
            pre-computed rows, which is why a serverless API can serve it instantly. Phase 1 is deliberately pure
            numpy — naive and momentum first, so every later model has a scoreboard to be judged against. OLS on
            macro drivers, an error-correction model against the policy corridor, and Granger tests on call money and
            liquidity come next, behind an isolated dependency set.</p>
          </div>
        </Panel>
      </div>

      <RelatedLinks items={[
        { href: "/yields", label: "Yields", why: "the cutoff history these models are fitted on" },
        { href: "/monetary", label: "Monetary", why: "the policy corridor cutoffs anchor to" },
        { href: "/callmoney", label: "Call Money", why: "the liquidity signal that leads auction demand" },
        { href: "/explore", label: "Explore", why: "test a driver against yields before it becomes a model" },
      ]} />
    </>
  );
}
