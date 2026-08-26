import { api, BacktestRow, ForecastRow, ForecastModel } from "@/lib/api";
import RelatedLinks from "@/components/RelatedLinks";
import { Panel } from "@/components/terminal/ui";
import ForecastIntervalChart from "@/components/ForecastIntervalChart";
import BacktestTrackChart from "@/components/BacktestTrackChart";
import DownloadButton from "@/components/DownloadButton";
import ResearchIntro from "@/components/ResearchIntro";
import { Term } from "@/components/Explain";
import { fmtDate, fmtPct, bps } from "@/lib/format";

export const revalidate = 300;

const DASH = "—";
const pct1 = (v: number | null) => (v == null ? DASH : `${(v * 100).toFixed(0)}%`);
const MODEL_LABEL: Record<ForecastModel, string> = {
  naive: "Naive", momentum: "Momentum", ols: "OLS", curve: "Curve carry",
  ecm: "ECM", blend: "Blend",
};
const MODEL_ORDER: ForecastModel[] = ["naive", "momentum", "curve", "ecm", "ols", "blend"];
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

  // The model to actually bid off. NOT simply the lowest MAE — picking the
  // winner after seeing the results is selection bias, and across six models and
  // eight tenors something always wins by luck. A challenger is only chosen when
  // its improvement over naive is statistically established (bootstrap interval
  // excludes zero AND small-sample DM agrees). Otherwise: naive.
  const bestModel = (t: string): ForecastModel | null => {
    const e = byTenor.get(t);
    if (!e) return null;
    const proven = MODEL_ORDER
      .filter(m => m !== "naive" && e[m]?.verdict === "established" && e[m]?.mae_bps != null)
      .sort((a, b) => e[a]!.mae_bps! - e[b]!.mae_bps!);
    return proven[0] ?? (e.naive ? "naive" : null);
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
  // Scored on the RECENT error of the model actually being recommended — not the
  // lifetime average of whichever model happens to look best in hindsight.
  const sharpest = headline
    .filter(h => (h.metric.mae_recent ?? h.metric.mae_bps) != null)
    .sort((a, b) => (a.metric.mae_recent ?? a.metric.mae_bps!) - (b.metric.mae_recent ?? b.metric.mae_bps!))[0] ?? null;
  const scored = fc.track_record.length;

  const empty = fc.run_date == null;
  const gate = diags.find(d => d.test === "policy_gate");
  const gateBlocked = gate?.conclusion?.startsWith("BLOCKED") ?? false;

  return (
    // Fragment: the page needs no client state at the top level — each Term
    // manages its own tooltip and concept reader.
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
          <div className="kpi-top"><span className="kpi-label">Most Predictable Tenor</span></div>
          <div className="kpi-val">
            <span className="kpi-num">
              {sharpest ? (sharpest.metric.mae_recent ?? sharpest.metric.mae_bps!).toFixed(1) : DASH}
            </span>
            <span className="kpi-unit">bps typical miss</span>
          </div>
          <div className="kpi-sub">
            {sharpest ? `${sharpest.row.tenor} · ${MODEL_LABEL[sharpest.model]} · recent period` : "no metrics yet"}
          </div>
        </div>
        <div className="kpi">
          <div className="kpi-top"><span className="kpi-label">Live Track Record</span></div>
          <div className="kpi-val"><span className="kpi-num">{scored}</span><span className="kpi-unit">scored</span></div>
          <div className="kpi-sub">{scored ? "published forecasts vs actual prints" : "builds as auctions print"}</div>
        </div>
      </div>

      <div className="grid12">
        <Panel title="Start Here — What This Page Says, In Plain Words" span={12}
          sub="hover any underlined term for a definition · click it to open the full concept explained from scratch">
          <ResearchIntro />
        </Panel>


        <Panel title="Next Auction — Forecast Change per Tenor" span={12}
          sub="change from the last cutoff, in bps · bar = the model's own measured error band, scaled by recent volatility so it tracks the regime">
          <ForecastIntervalChart rows={fc.forecasts} />
        </Panel>

        <Panel title="Next Auction — Best Model per Tenor" span={12} pad={false}
          sub={'a challenger is used ONLY where its edge over "same as last time" is statistically established — '
            + 'otherwise this falls back to the last cutoff · "typical miss" is the RECENT error, not the flattering lifetime average'}
          right={<DownloadButton data={fc.forecasts} filename="auction_forecasts" />}>
          <div className="table-wrap">
            <table className="dt">
              <thead><tr>
                <th><Term t="Tenor" /></th><th>Model</th><th>Target Auction</th>
                <th className="r"><Term t="Cutoff Yield">Last Print</Term></th><th className="r">Forecast</th>
                <th className="r">Change</th>
                <th className="r"><Term t="Coverage">95% Low</Term></th>
                <th className="r"><Term t="Coverage">95% High</Term></th>
                <th className="r">Typical miss<br /><span style={{ fontWeight: 400, color: "var(--fg-mute)" }}>recent, bps</span></th>
                <th className="r">Right direction</th>
                <th>Verdict</th>
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
                      <td className="r mono" title={`full-sample MAE ${metric.mae_bps?.toFixed(1) ?? "—"} bps`}>
                        {(metric.mae_recent ?? metric.mae_bps)?.toFixed(1) ?? DASH}
                      </td>
                      <td className="r mono">{pct1(metric.dir_acc)}</td>
                      <td style={{ fontSize: 11.5 }}>
                        {model === "naive"
                          ? <span style={{ color: "var(--fg-mute)" }}>nothing beat &quot;same as last&quot;</span>
                          : <span style={{ color: "var(--pos)" }}>proven better than &quot;same as last&quot;</span>}
                      </td>
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
          sub={'every model tested against "same as last time" on auctions it never saw · '
            + '"better than naive by" is a 95% range from 2,000 bootstrap resamples — if it straddles 0, the edge is not proven'}
          right={<DownloadButton data={fc.metrics} filename="backtest_metrics" />}>
          <div style={{
            padding: "10px 14px", fontSize: 12, color: "var(--fg-mute)",
            borderBottom: "1px solid var(--border-soft)",
          }}>
            How a verdict is decided — click any for the full explanation:{" "}
            <Term t="Bootstrap" /> resampling gives the range, the{" "}
            <Term t="Diebold-Mariano" /> test checks it is not luck, and{" "}
            <Term t="Selection Bias">a rule fixed in advance</Term> decides which model is used.
          </div>
          <div className="table-wrap">
            <table className="dt">
              <thead><tr>
                <th><Term t="Tenor" /></th><th>Model</th>
                <th className="r"><Term t="MAE">Typical miss</Term><br />
                  <span style={{ fontWeight: 400 }}><Term t="Basis Point">bps</Term> (early → recent)</span></th>
                <th className="r"><Term t="Confidence Interval">Better than naive by</Term><br />
                  <span style={{ fontWeight: 400 }}>bps, 95% range</span></th>
                <th className="r"><Term t="Diebold-Mariano">Could it be luck?</Term><br />
                  <span style={{ fontWeight: 400 }}><Term t="p-value" /></span></th>
                <th><Term t="Selection Bias">Verdict</Term></th>
                <th className="r"><Term t="Directional Accuracy">Right direction</Term></th>
                <th className="r"><Term t="Coverage">Band holds</Term></th>
                <th className="r"><Term t="Out-of-sample">n</Term></th>
              </tr></thead>
              <tbody>
                {tenors.flatMap(t => {
                  const e = byTenor.get(t)!;
                  const naiveMae = e.naive?.mae_bps ?? null;
                  return MODEL_ORDER
                    .filter(m => e[m])
                    .map((m, mi) => {
                      const r = e[m]!;
                      const v = r.verdict;
                      const vColor = v === "established" ? "var(--pos)"
                        : v === "worse" ? "var(--neg)"
                        : v === "benchmark" ? "var(--fg-mute)"
                        : v === "exploratory" ? "var(--info)" : "var(--warn)";
                      const vText = v === "established" ? "real edge"
                        : v === "worse" ? "worse than naive"
                        : v === "benchmark" ? "the benchmark"
                        : v === "exploratory" ? "exploratory only" : "could be luck";
                      const gap = r.gap_lo != null && r.gap_hi != null
                        ? `${r.gap_lo > 0 ? "+" : ""}${r.gap_lo.toFixed(1)} to ${r.gap_hi > 0 ? "+" : ""}${r.gap_hi.toFixed(1)}`
                        : DASH;
                      // An interval that straddles zero is the whole story: the
                      // model might be better, might be worse. Say so in colour.
                      const gapProven = r.gap_lo != null && r.gap_lo > 0;
                      return (
                        <tr key={`${t}-${m}`}>
                          <td style={{ fontWeight: 600, color: mi === 0 ? undefined : "transparent" }}>{t}</td>
                          <td style={{ color: m === "naive" ? "var(--fg-mute)" : undefined }}>{MODEL_LABEL[m]}</td>
                          <td className="r mono" style={{ fontWeight: m === "naive" ? 400 : 600 }}>
                            {r.mae_first != null && r.mae_recent != null
                              ? <>{r.mae_first.toFixed(1)} <span style={{ color: "var(--fg-mute)" }}>→</span> {r.mae_recent.toFixed(1)}</>
                              : (r.mae_bps?.toFixed(1) ?? DASH)}
                          </td>
                          <td className="r mono" style={{ color: gapProven ? "var(--pos)" : "var(--fg-mute)" }}>{gap}</td>
                          <td className="r mono" style={{ color: r.dm_pvalue != null && r.dm_pvalue < 0.05 ? "var(--fg)" : "var(--fg-mute)" }}>
                            {r.dm_pvalue == null ? DASH : r.dm_pvalue.toFixed(3)}
                          </td>
                          <td style={{ color: vColor, fontSize: 11.5, fontWeight: v === "established" ? 600 : 400 }}>{vText}</td>
                          <td className="r mono">{pct1(r.dir_acc)}</td>
                          <td className="r mono" style={{ color: "var(--fg-mute)" }}>{pct1(r.coverage)}</td>
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
            <div style={{
              padding: "10px 14px", fontSize: 12, color: "var(--fg-mute)",
              borderBottom: "1px solid var(--border-soft)",
            }}>
              Tests used here — click any for the full explanation:{" "}
              <Term t="Stationarity" />, <Term t="Granger Causality" />,{" "}
              <Term t="Cointegration" />.
            </div>
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

        {gate && (
          <Panel title="Error-Correction Model — Status" span={12}
            sub="the pull-to-anchor model from the project guide (Phase 5.3), and what it is waiting on">
            <div style={{ fontSize: 12.5, lineHeight: 1.7, color: "var(--fg-mute)", maxWidth: 980 }}>
              <div style={{
                display: "inline-flex", alignItems: "center", gap: 8, marginBottom: 12,
                padding: "6px 12px", borderRadius: "var(--radius-sm)", fontSize: 12,
                border: `1px solid ${gateBlocked ? "var(--warn)" : "var(--pos)"}`,
                color: gateBlocked ? "var(--warn)" : "var(--pos)",
                background: `color-mix(in oklab, ${gateBlocked ? "var(--warn)" : "var(--pos)"} 10%, transparent)`,
              }}>
                <b>{gateBlocked ? "BLOCKED" : "ACTIVE"}</b>
                <span style={{ color: "var(--fg-dim)" }}>{gate.conclusion?.replace(/^(BLOCKED|OPEN) — /, "")}</span>
              </div>

              <p>The ECM is the most theoretically honest model for a corridor regime: it says a cutoff
              sitting well above the policy rate should get <i>pulled back</i> toward it, at a speed α that
              the data estimates. Everything for it is built and wired — the effective-dated corridor table,
              the step-function lookup, the spread and policy-change terms, the Engle-Granger{" "}
              <Term t="Cointegration">cointegration</Term> test, and a sign check that withholds the forecast if α comes out positive (which would mean
              the spread is explosive, not error-correcting).</p>

              <p><b style={{ color: "var(--fg)" }}>What it is waiting on is data, not code.</b> The model
              needs the corridor as a step function across the whole fit window. The database holds one
              verified corridor observation; the seed&apos;s other two entries say in their own notes that
              they were drafted from memory. Fitting on those would not fail loudly — it would produce a
              confident α and a plausible-looking pull-to-anchor built on invented numbers, which is worse
              than having no model at all. So it stays switched off.</p>

              <p><b style={{ color: "var(--fg)" }}>To unlock it:</b> add every corridor change back to at
              least the start of the fit window into{" "}
              <span className="mono">api/seeds/policy_rates.yaml</span> from the BB MPD circular archive,
              set <span className="mono">verified: true</span> on each one you actually checked, then run{" "}
              <span className="mono">python forecast/policy.py --load</span>. The ECM and the cointegration
              test switch themselves on at the next weekly run — no code change needed.</p>
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

            <H>How we decide an edge is real, not luck</H>
            <p>A lower average error in a backtest is a <i>claim</i>, not a fact. With six models and eight
            tenors, something always looks good by chance. Three defences are applied, and they changed the
            conclusions materially:</p>
            <p><b style={{ color: "var(--fg)" }}>1. A range instead of a number.</b> Each model&apos;s
            improvement over naive is re-computed on 2,000 resampled versions of history (in blocks, so
            neighbouring auctions stay together — errors are correlated week to week, and pretending
            otherwise would make the range look far tighter than it is). If that 95% range includes zero,
            the model might genuinely be worse, and we call it unproven. This is what demoted the bill
            models: 91D momentum&apos;s &quot;win&quot; has a range of −0.2 to +1.1 bps.</p>
            <p><b style={{ color: "var(--fg)" }}>2. A small-sample correction.</b> The Diebold-Mariano test
            over-rejects badly on ~20 bond auctions — it declares victories that are not there. The
            Harvey-Leybourne-Newbold correction rescales it and reads a t-distribution instead of a normal.
            Every p-value shown is the corrected one.</p>
            <p><b style={{ color: "var(--fg)" }}>3. A correction for testing many things at once.</b>{" "}
            Screening ~30 comparisons at p&lt;0.05 manufactures winners: simulated under the null,
            that produces about 1.5 false &quot;established&quot; verdicts per run.{" "}
            <Term t="Multiple Testing">Benjamini-Hochberg</Term> cuts that to 0.05. The subtlety is
            defining the family honestly — the correction is applied to the pre-specified primary
            hypothesis (curve carry beats naive, across tenors), not to every model-by-tenor cell,
            because those cells are not independent tests and padding the family with models already
            known to fail would reject even the strongest result. Every other model is marked{" "}
            <b style={{ color: "var(--info)" }}>exploratory</b>: shown with its real numbers, never
            promoted. All five bond tenors survive this correction; the bills do not.</p>

            <p><b style={{ color: "var(--fg)" }}>4. A rule fixed in advance.</b> The model shown at the top
            is not simply the one with the lowest error — choosing after seeing results is how backtests
            lie. A challenger is used only when its range excludes zero <i>and</i> the corrected test agrees.
            Otherwise the page falls back to naive, even when another model&apos;s headline number looks better.</p>
            <p><b style={{ color: "var(--fg)" }}>The band now tracks the regime.</b> It used to be a
            fixed multiple of recent error, which assumes the error scale is constant. It is not: the
            10Y cutoff moved 16 bps per auction in 2024 and 104 bps in 2025. That band was too wide in
            calm years and — far worse — too narrow in violent ones, covering only 83% of 10Y prints in
            2025 when risk was highest. It is now scaled by an{" "}
            <Term t="EWMA Volatility">exponentially-weighted volatility</Term> estimate, which lifted
            measured coverage from 88.8% to 94.3% and the worst year from 77% to 87%, for about 2 bps
            of extra average width. Each tenor&apos;s realised coverage is published in the scoreboard
            rather than a nominal 95% being asserted.</p>

            <H>Why the average error understates today</H>
            <p>Splitting the backtest in half is uncomfortable reading. On 91D the typical miss was about
            <b style={{ color: "var(--fg)" }}> 5 bps early on and about 14 bps recently</b>; the same pattern
            holds across the bills. The models did not break — the market got more volatile. But it means a
            single lifetime-average error is the wrong number to size against, which is why the tables show
            <b style={{ color: "var(--fg)" }}> early → recent</b> and the headline uses the recent figure.</p>

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
            It is 1.96 × an EWMA estimate of the model&apos;s own recent out-of-sample error, so it is a measured
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

            <p><b style={{ color: "var(--fg)" }}>The band is measured, not assumed.</b> It is 1.96 × an EWMA estimate of the model&apos;s own
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
