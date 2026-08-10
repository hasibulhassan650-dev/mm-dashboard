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
  naive: "Naive", momentum: "Momentum", ols: "OLS", curve: "Curve carry", blend: "Blend",
};
const MODEL_ORDER: ForecastModel[] = ["naive", "momentum", "curve", "ols", "blend"];
const TRACK_TENOR = "91D";   // deepest series — the one worth eyeballing

/** Section heading inside a prose panel. */
function H({ children }: { children: React.ReactNode }) {
  return (
    <h4 style={{
      color: "var(--fg)", fontSize: 12.5, fontWeight: 600, margin: "18px 0 6px",
      letterSpacing: ".2px", textTransform: "uppercase",
    }}>{children}</h4>
  );
}

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

  // The model to actually bid off, per tenor: lowest out-of-sample MAE. This is
  // chosen from backtest skill, never from which forecast looks nicer today.
  const bestModel = (t: string): ForecastModel | null => {
    const e = byTenor.get(t);
    if (!e) return null;
    const ranked = MODEL_ORDER.filter(m => e[m]?.mae_bps != null)
      .sort((a, b) => e[a]!.mae_bps! - e[b]!.mae_bps!);
    return ranked[0] ?? null;
  };
  const headline: { row: ForecastRow; model: ForecastModel; metric: BacktestRow }[] = tenors
    .map(t => {
      const m = bestModel(t);
      const row = m ? fc.forecasts.find(f => f.tenor === t && f.model === m) : undefined;
      const metric = m ? byTenor.get(t)![m] : undefined;
      return row && m && metric ? { row, model: m, metric } : null;
    })
    .filter((x): x is { row: ForecastRow; model: ForecastModel; metric: BacktestRow } => x !== null);

  const beats = tenors.filter(t => {
    const m = bestModel(t);
    return m != null && m !== "naive";
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
          <div className="kpi-top"><span className="kpi-label">Tenors Beating Naive</span></div>
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

        <Panel title="Next Auction — Best Model per Tenor" span={12} pad={false}
          sub="the model with the lowest out-of-sample MAE on that tenor · the number to actually bid off, with the error it has historically carried"
          right={<DownloadButton data={fc.forecasts} filename="auction_forecasts" />}>
          <div className="table-wrap">
            <table className="dt">
              <thead><tr>
                <th>Tenor</th><th>Model</th><th>Target Auction</th>
                <th className="r">Last Print</th><th className="r">Forecast</th>
                <th className="r">Change</th><th className="r">95% Low</th><th className="r">95% High</th>
                <th className="r">MAE</th><th className="r">Dir. Acc.</th>
              </tr></thead>
              <tbody>
                {headline.map(({ row: f, model, metric }, i) => {
                  const chg = f.last_actual_yield != null ? f.point_yield - f.last_actual_yield : null;
                  const strong = metric.dm_pvalue != null && metric.dm_pvalue < 0.05;
                  return (
                    <tr key={i}>
                      <td style={{ fontWeight: 600 }}>{f.tenor}</td>
                      <td>
                        <span style={{ color: model === "naive" ? "var(--fg-mute)" : "var(--accent)", fontWeight: 500 }}>
                          {MODEL_LABEL[model]}
                        </span>
                        {strong && <span title="Beats naive at p<0.05 (Diebold-Mariano)"
                          style={{ marginLeft: 6, fontSize: 10, color: "var(--pos)" }}>✓ sig</span>}
                      </td>
                      <td>{fmtDate(f.target_auction_date)}</td>
                      <td className="r mono">{fmtPct(f.last_actual_yield, 4)}</td>
                      <td className="r mono" style={{ fontWeight: 600 }}>{fmtPct(f.point_yield, 4)}</td>
                      <td className="r mono" style={{ color: chg == null ? undefined : chg < 0 ? "var(--pos)" : "var(--neg)" }}>
                        {chg == null ? DASH : bps(chg)}
                      </td>
                      <td className="r mono" style={{ color: "var(--fg-mute)" }}>{fmtPct(f.lo_yield, 4)}</td>
                      <td className="r mono" style={{ color: "var(--fg-mute)" }}>{fmtPct(f.hi_yield, 4)}</td>
                      <td className="r mono">{metric.mae_bps?.toFixed(1) ?? DASH}</td>
                      <td className="r mono">{pct1(metric.dir_acc)}</td>
                    </tr>
                  );
                })}
                {headline.length === 0 && (
                  <tr><td colSpan={10} style={{ textAlign: "center", color: "var(--fg-mute)", padding: 16 }}>
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

        <Panel title="What Is Actually Happening" span={12}
          sub="the mechanism behind each model — read this once and the tables above stop being a black box">
          <div style={{ fontSize: 12.5, lineHeight: 1.75, color: "var(--fg-mute)", maxWidth: 980 }}>
            <H>The problem: an auction cutoff is almost a random walk</H>
            <p>A treasury cutoff is set by competitive bidding among banks that all see the same
            information. Anything predictable about next week&apos;s rate is already in today&apos;s bids —
            so the rate mostly moves when genuinely <i>new</i> information arrives, and new information is
            by definition unforecastable. That is why <b style={{ color: "var(--fg)" }}>naive</b> —
            &quot;next cutoff = last cutoff&quot; — is such a punishing benchmark. It is not a strawman;
            it is the theoretically correct forecast if the rate is a pure random walk. Any model that
            beats it is claiming to have found information the market did not price. Most do not.</p>
            <p>Concretely: on 91D, naive is wrong by about <b style={{ color: "var(--fg)" }}>10 bps on
            average</b> and lands within ±5 bps half the time. To be useful, a model has to do better
            than that <i>consistently</i>, not once.</p>

            <H>The insight that actually worked: stale information</H>
            <p>Bills auction weekly; bonds auction roughly monthly. When a 10Y bond comes to auction,
            the naive forecast anchors on that bond&apos;s <i>previous</i> print — a month old. But in
            the meantime the 91D/182D/364D bills have printed four more times, and the whole curve has
            moved. Naive is not wrong because rates are unpredictable here; it is wrong because it is
            using <b style={{ color: "var(--fg)" }}>deliberately out-of-date information</b> when
            fresher, public, free information exists.</p>
            <p><b style={{ color: "var(--fg)" }}>Curve carry</b> fixes exactly that. It takes how far
            the 364D bill has moved since this tenor last auctioned, multiplies by a fitted
            pass-through λ, and shifts the last cutoff by that much. Nothing clever — it just refuses
            to ignore the last month of market history.</p>
            <p>The fitted λ tells you how the curve behaves: roughly
            <b style={{ color: "var(--fg)" }}> 0.97–1.13 for 2Y–15Y</b> (the BD curve shifts nearly in
            parallel — a 50 bps move in bills is a ~50 bps move in bonds),
            <b style={{ color: "var(--fg)" }}> 0.78 at 20Y</b> (the long end is anchored by duration
            demand and moves less), and <b style={{ color: "var(--fg)" }}>~0.40 for 91D/182D</b> (they
            print the same day as the anchor, so much of the move is already shared). Those numbers were
            not assumed — they were estimated, and they match how a curve is supposed to behave. That
            agreement is the main reason to trust this model rather than the OLS one.</p>

            <H>The insight that failed, and why that is useful too</H>
            <p><b style={{ color: "var(--fg)" }}>OLS</b> regresses the cutoff change on auction-demand
            factors — cover ratio, its change, relative size, distance from the trailing mean. It loses
            to naive on every tenor. The Granger tests explain it: none of those factors
            <b style={{ color: "var(--fg)" }}> lead</b> the cutoff (all p &gt; 0.18 on 148+ auctions).
            They describe an auction that has already happened, and describing is not predicting. Each
            extra coefficient then has to be estimated from finite data, and that estimation error goes
            straight into the forecast — so adding useless factors makes you actively worse, not merely
            no better. That is the bias-variance tradeoff in its most concrete form.</p>

            <H>Why the blend exists</H>
            <p><b style={{ color: "var(--fg)" }}>Blend</b> is the plain average of the other models.
            Averaging forecasts is one of the most durable results in forecasting: independent errors
            partly cancel, so the average usually lands near the best single model without you having
            to know in advance which one that will be. It is insurance, not brilliance — you give up
            some upside when one model is clearly best, in exchange for never being caught fully
            committed to a model that just broke.</p>
          </div>
        </Panel>

        <Panel title="How to Read the Statistics" span={6}>
          <div style={{ fontSize: 12.5, lineHeight: 1.75, color: "var(--fg-mute)" }}>
            <p><b style={{ color: "var(--fg)" }}>MAE vs RMSE.</b> MAE is your average miss in bps —
            the honest headline. RMSE squares errors first, so it punishes rare large misses. When RMSE
            is much bigger than MAE, the model is usually fine but occasionally badly wrong — which
            matters more than the average if a single bad auction can hurt the book.</p>
            <p><b style={{ color: "var(--fg)" }}>Directional accuracy</b> asks only: did the rate go the
            way the model said? For a bidder this is often the number that pays. A model can have a
            mediocre MAE and still be extremely useful at 90% direction, because it tells you which side
            of the last print to lean. Naive shows &quot;—&quot; here: it predicts no change, so it makes
            no directional call at all.</p>
            <p><b style={{ color: "var(--fg)" }}>Within ±5 bps</b> is the practical one — how often the
            forecast was close enough to bid off directly.</p>
            <p><b style={{ color: "var(--fg)" }}>DM p-value</b> (Diebold-Mariano) is the discipline.
            A lower MAE could be luck across 20 auctions. DM tests whether the difference in squared
            error is statistically real. <b style={{ color: "var(--fg)" }}>p &lt; 0.05 = trust the
            edge; p &gt; 0.1 = interesting, not established.</b> This is why momentum, despite winning
            on all three bills, is only labelled a lean: its p-values sit at 0.07–0.26.</p>
            <p><b style={{ color: "var(--fg)" }}>ADF / KPSS</b> test whether a series has a stable level
            to revert to. They disagree by design (ADF&apos;s null is a unit root, KPSS&apos;s null is
            stationarity), so agreement between them is strong evidence and disagreement means
            &quot;treat as non-stationary and model the change, not the level&quot; — which is what
            every model here does.</p>
            <p><b style={{ color: "var(--fg)" }}>Granger causality</b> does not mean causation. It asks
            the narrower question: does knowing X&apos;s past reduce the error in predicting Y beyond
            Y&apos;s own past? If no, X is useless as a predictor no matter how correlated it looks.</p>
          </div>
        </Panel>

        <Panel title="Using This at the Desk" span={6}>
          <div style={{ fontSize: 12.5, lineHeight: 1.75, color: "var(--fg-mute)" }}>
            <p><b style={{ color: "var(--fg)" }}>The band is the position-sizing input, not decoration.</b>{" "}
            It is ±1.96 × the model&apos;s own realised out-of-sample RMSE, so it is a measured error
            distribution, not an assumption. A ±30 bps band on a bill means your rate view is worth
            acting on; a ±150 bps band on a bond means the model is explicitly telling you it cannot
            call that auction, and size accordingly.</p>
            <p><b style={{ color: "var(--fg)" }}>Direction first, level second.</b> On bonds the curve
            model&apos;s edge is overwhelmingly directional (80–95%). Use it to decide which side of the
            last print to bid, not to pin a basis point.</p>
            <p><b style={{ color: "var(--fg)" }}>Where the model is blind.</b> It sees only auction
            history. It cannot see an MPC decision, a BB circular, a quarter-end funding squeeze, a
            large government payment, or a devolvement. Those are step-changes, and they are precisely
            when the forecast will be most wrong. Overlay your own judgement on those weeks and record
            both calls — the model forecast and your adjusted one — so you can tell later which added
            value.</p>
            <p><b style={{ color: "var(--fg)" }}>Watch λ for regime change.</b> If a tenor&apos;s
            pass-through drifts materially from the levels above, the relationship between that tenor
            and the bill curve is changing — a signal in its own right, and a reason to stop trusting
            the curve forecast until it settles.</p>
            <p><b style={{ color: "var(--fg)" }}>Reflexivity.</b> If the desk bids differently because
            of this model, at the margin it affects the cutoff it is forecasting. Small at your size,
            but it is the reason a published track record matters more than a backtest.</p>
          </div>
        </Panel>

        <Panel title="Honest Limitations" span={12}>
          <div style={{ fontSize: 12.5, lineHeight: 1.7, color: "var(--fg-mute)", maxWidth: 980 }}>
            <p><b style={{ color: "var(--fg)" }}>Bond samples are small — about 20 scored auctions each.</b> The curve
            model&apos;s improvement is large and mechanically sensible, but twenty observations is twenty observations.
            2Y, 5Y and 10Y clear the Diebold-Mariano bar (p = 0.0003 / 0.003 / 0.030) so those edges are established;
            15Y (p = 0.079) and especially <b style={{ color: "var(--fg)" }}>20Y (p = 0.849)</b> are not — at 20Y the
            lower MAE is well within luck. Treat the long end as directional guidance only.</p>

            <p><b style={{ color: "var(--fg)" }}>The curve model is currently making an aggressive call.</b> The 364D
            anchor has fallen sharply since the long bonds last auctioned, so with λ near 1 the model projects a large
            drop at 15Y and 20Y. That is the mechanism working as designed, not a glitch — but it is an extrapolation
            from an unusually big anchor move, and the band around it is wide for exactly that reason. Size to the band.</p>

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

            <p><b style={{ color: "var(--fg)" }}>A wide band is information, not a defect.</b> Where a model cannot
            call an auction, its band says so in basis points. Read the band before the point estimate.</p>

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
