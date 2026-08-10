import { api, BacktestRow, ForecastRow, ForecastModel } from "@/lib/api";
import RelatedLinks from "@/components/RelatedLinks";
import { Panel } from "@/components/terminal/ui";
import ForecastIntervalChart from "@/components/ForecastIntervalChart";
import BacktestTrackChart from "@/components/BacktestTrackChart";
import DownloadButton from "@/components/DownloadButton";
import { fmtDate, fmtPct, bps } from "@/lib/format";

export const revalidate = 300;

const DASH = "—";
const pct1 = (v: number | null) => (v == null ? DASH : `${(v * 100).toFixed(0)}%`);
const MODEL_LABEL: Record<ForecastModel, string> = {
  naive: "Naive", momentum: "Momentum", ols: "OLS",
};
const TRACK_TENOR = "91D";   // deepest series — the one worth eyeballing

export default async function ResearchPage() {
  const [fc, track, diags] = await Promise.all([
    api.forecast(),
    api.forecastPredictions(TRACK_TENOR),
    api.forecastDiagnostics(),
  ]);

  const byTenor = new Map<string, Partial<Record<ForecastModel, BacktestRow>>>();
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

        <Panel title={`Backtest Track — ${TRACK_TENOR}`} span={12}
          sub="every out-of-sample prediction against what actually printed · a simulation of the models, NOT the published forecast log">
          <BacktestTrackChart rows={track} />
        </Panel>

        <Panel title="Backtest — Model vs Naive" span={12} pad={false}
          sub="expanding-window, one-step-ahead, out-of-sample · naive is the bar every model must clear · DM p<0.05 means the gap is real, not luck"
          right={<DownloadButton data={fc.metrics} filename="backtest_metrics" />}>
          <div className="table-wrap">
            <table className="dt">
              <thead><tr>
                <th>Tenor</th><th>Model</th>
                <th className="r">MAE (bps)</th><th className="r">RMSE</th><th className="r">Edge vs naive</th>
                <th className="r">Dir. Acc.</th><th className="r">Within ±5bps</th>
                <th className="r">DM p vs naive</th><th className="r">n</th>
              </tr></thead>
              <tbody>
                {tenors.flatMap(t => {
                  const e = byTenor.get(t)!;
                  const naiveMae = e.naive?.mae_bps ?? null;
                  return (["naive", "momentum", "ols"] as ForecastModel[])
                    .filter(m => e[m])
                    .map((m, mi) => {
                      const r = e[m]!;
                      const edge = m !== "naive" && naiveMae != null && r.mae_bps != null
                        ? naiveMae - r.mae_bps : null;
                      const sig = r.dm_pvalue != null && r.dm_pvalue < 0.05;
                      return (
                        <tr key={`${t}-${m}`}>
                          <td style={{ fontWeight: 600, color: mi === 0 ? undefined : "transparent" }}>{t}</td>
                          <td style={{ color: m === "naive" ? "var(--fg-mute)" : undefined }}>{MODEL_LABEL[m]}</td>
                          <td className="r mono" style={{ fontWeight: m === "naive" ? 400 : 600 }}>{r.mae_bps?.toFixed(2) ?? DASH}</td>
                          <td className="r mono" style={{ color: "var(--fg-mute)" }}>{r.rmse_bps?.toFixed(2) ?? DASH}</td>
                          <td className="r mono" style={{ fontWeight: 600, color: edge == null ? "var(--fg-mute)" : edge > 0 ? "var(--pos)" : "var(--neg)" }}>
                            {edge == null ? DASH : `${edge > 0 ? "+" : ""}${edge.toFixed(2)}`}
                          </td>
                          <td className="r mono">{pct1(r.dir_acc)}</td>
                          <td className="r mono">{pct1(r.hit_5bps)}</td>
                          <td className="r mono" style={{ color: sig ? "var(--fg)" : "var(--fg-mute)", fontWeight: sig ? 600 : 400 }}>
                            {r.dm_pvalue == null ? DASH : r.dm_pvalue.toFixed(3)}
                          </td>
                          <td className="r mono" style={{ color: "var(--fg-mute)" }}>{r.n_obs}</td>
                        </tr>
                      );
                    });
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

        {diags.length > 0 && (
          <Panel title="Statistical Diagnostics" span={12} pad={false}
            sub="guide Phase 4 · ADF/KPSS stationarity, Granger causality, Newey-West OLS inference, VIF, Ljung-Box · fitted in CI with statsmodels"
            right={<DownloadButton data={diags} filename="research_diagnostics" />}>
            <div className="table-wrap" style={{ maxHeight: 420, overflowY: "auto" }}>
              <table className="dt">
                <thead><tr>
                  <th>Tenor</th><th>Test</th><th>Subject</th>
                  <th className="r">Statistic</th><th className="r">p-value</th><th className="r">Lag</th>
                  <th>Conclusion</th>
                </tr></thead>
                <tbody>
                  {diags.map((d, i) => {
                    const flag = d.conclusion?.includes("SIGN WRONG") || d.conclusion?.includes("collinear");
                    return (
                      <tr key={i}>
                        <td style={{ fontWeight: 600 }}>{d.tenor}</td>
                        <td className="mono" style={{ color: "var(--fg-mute)" }}>{d.test}</td>
                        <td className="mono">{d.subject}</td>
                        <td className="r mono">{d.statistic == null ? DASH : d.statistic.toFixed(3)}</td>
                        <td className="r mono" style={{ fontWeight: d.pvalue != null && d.pvalue < 0.05 ? 600 : 400 }}>
                          {d.pvalue == null ? DASH : d.pvalue.toFixed(4)}
                        </td>
                        <td className="r mono" style={{ color: "var(--fg-mute)" }}>{d.lag ?? DASH}</td>
                        <td style={{ fontSize: 11.5, color: flag ? "var(--warn)" : "var(--fg-mute)" }}>{d.conclusion}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Panel>
        )}

        <Panel title="How to Read This" span={12}>
          <div style={{ fontSize: 12.5, lineHeight: 1.7, color: "var(--fg-mute)", maxWidth: 900 }}>
            <p><b style={{ color: "var(--fg)" }}>Three models, and the honest scoreboard between them.</b>{" "}
            <b style={{ color: "var(--fg)" }}>Naive</b> says the next cutoff equals the last one — on a rate that
            moves in small weekly steps, that is a genuinely hard benchmark. <b style={{ color: "var(--fg)" }}>Momentum</b>{" "}
            adds β × the last change, with β fitted by OLS on how much one change has historically carried into the next.
            <b style={{ color: "var(--fg)" }}> OLS</b> regresses the change on the auction-demand factor set (cover ratio,
            its change, relative size, and distance from the trailing mean), and runs only on the deep bill series.
            <b style={{ color: "var(--fg)" }}> Edge</b> is naive MAE minus the model&apos;s: positive means it earned its keep.</p>

            <p><b style={{ color: "var(--fg)" }}>The result you should act on: the factor model lost.</b> OLS is worse
            than naive on every tenor it runs on, and its directional accuracy sits below a coin flip. The diagnostics
            say why — <b style={{ color: "var(--fg)" }}>no factor Granger-causes the cutoff change</b> at any lag tested,
            so the regression is fitting noise and paying estimation cost for it. Momentum does beat naive on all three
            bills, but its Diebold-Mariano p-values (0.07–0.26) are above 5%: the edge is suggestive, not yet proven.
            Treat the bill momentum forecast as a lean, not a signal, and bid off naive on the bonds.</p>

            <p><b style={{ color: "var(--fg)" }}>Why the guide&apos;s full factor set isn&apos;t here.</b> Call money, OMO
            and reference rates only start March 2026 in this database, and the policy corridor has a single stored
            observation — so <span className="mono">call_spread</span>, <span className="mono">d_policy</span> and the
            error-correction model against the policy anchor cannot be built without inventing history. They are
            deliberately absent rather than fitted on placeholders. Backfilling the policy corridor is the single
            highest-value unlock for this page.</p>

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

            <p style={{ color: "var(--fg-dim)" }}><b style={{ color: "var(--fg)" }}>Method.</b> Everything here is
            fitted weekly in GitHub Actions and written to the database; this page and its API only read pre-computed
            rows, which is why a serverless API can serve it instantly. The forecast itself is fitted in pure numpy so
            it never depends on the stats stack; statsmodels is installed only for the diagnostics table, behind an
            isolated <span className="mono">requirements-forecast.txt</span>. Every backtest fit sees strictly prior
            auctions, and every feature is lagged — no value measured at an auction is ever used to forecast it.</p>
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
