import { Panel } from "@/components/terminal/ui";
import { Term } from "@/components/Explain";
import { ForecastPayload, BacktestRow, ForecastModel } from "@/lib/api";
import { byTenor } from "@/lib/tenor";
import spec from "@/lib/forecastMethod.json";

/**
 * How the next-auction cutoffs are forecast, explained in full.
 *
 * Every constant on this page comes from forecastMethod.json, which is generated from the Python modules
 * that actually run the forecast (forecast/method_spec.py). Every measured figure comes from the same
 * /api/forecast payload the rest of the page uses. Nothing is typed here: a number that is in neither
 * place is not stated, and the reader is sent to the table. tests/test_forecast_method.py enforces both.
 */

const DASH = "—";
const body: React.CSSProperties = { fontSize: 12.5, lineHeight: 1.75, color: "var(--fg-mute)", maxWidth: 980 };
const strong: React.CSSProperties = { color: "var(--fg)" };
const sub: React.CSSProperties = { color: "var(--fg-dim)" };
const list: React.CSSProperties = { paddingLeft: 20, margin: "0 0 10px" };
const item: React.CSSProperties = { marginBottom: 6 };
const anchorWrap: React.CSSProperties = { gridColumn: "span 12", scrollMarginTop: 88 };

const VERDICT_TEXT: Record<string, string> = {
  established: "real edge", worse: "worse than naive", benchmark: "the benchmark",
  exploratory: "exploratory only", unproven: "could be luck",
};

const B = ({ children }: { children: React.ReactNode }) => <b style={strong}>{children}</b>;
const M = ({ children }: { children: React.ReactNode }) => <span className="mono">{children}</span>;

const pct0 = (v: number) => `${Math.round(v * 100)}%`;
const range = (xs: number[], f: (v: number) => string) =>
  xs.length === 0 ? DASH : Math.min(...xs) === Math.max(...xs) ? f(xs[0]) : `${f(Math.min(...xs))} to ${f(Math.max(...xs))}`;
const signed = (v: number) => `${v > 0 ? "+" : ""}${v.toFixed(1)}`;

export default function ForecastMethod({ fc, label }: { fc: ForecastPayload; label: Record<ForecastModel, string> }) {
  const isBill = (t: string) => spec.bill_tenors.includes(t);
  const primary = spec.primary_model as ForecastModel;

  // Everything measured is read off the payload, per tenor, from the benchmark rows (one per tenor).
  const bench = fc.metrics.filter(m => m.model === "naive");
  const nBills = bench.filter(m => isBill(m.tenor)).map(m => m.n_obs);
  const nBonds = bench.filter(m => !isBill(m.tenor)).map(m => m.n_obs);
  const coverages = fc.metrics.map(m => m.coverage).filter((c): c is number => c != null);

  const fam: BacktestRow[] = fc.metrics.filter(m => m.model === primary).sort((a, b) => byTenor(a.tenor, b.tenor));
  const famTested = fam.filter(m => m.dm_pvalue != null);
  const famPassed = fam.filter(m => m.dm_fdr_pass === true);
  const anchorTenor = spec.anchor_tenor;
  const hasData = fc.metrics.length > 0;

  return (
    <div id="method" style={anchorWrap}>
      <div className="grid12">

        <Panel title="How the Yield Curve Is Forecast" span={12}
          sub="what is predicted, from what data, by which models, and how every claim of skill is checked · every figure on this section is read from the code or from the stored forecast run">
          <div style={body}>
            <p><B>No yield curve is fitted.</B> There is no Nelson-Siegel curve, no spline and no
            constraint that neighbouring tenors line up. Each tenor&apos;s <B>next primary-auction cutoff
            yield</B> is forecast <B>separately, one auction ahead</B>. The &quot;curve&quot; of forecasts
            on this page is simply those separate forecasts read from short to long, so two adjacent tenors
            can be forecast in directions that a smooth curve would not allow.</p>
            <p>The only link between tenors is one shared input: the weekly cutoff of the{" "}
            <B>{anchorTenor}</B> bill, the <i>anchor</i>. Only the curve-carry model (and the blend that
            contains it) looks at it. Every other model sees nothing but its own tenor&apos;s history (the
            error-correction model also sees the repo rate).</p>
            <p>The forecast is a yield <i>level</i> in percent. Errors are measured in basis points so a
            short bill and a long bond can be read on the same axis. The target date shown is not Bangladesh
            Bank&apos;s auction calendar: it is the last print date plus the median of that tenor&apos;s
            last <M>{spec.next_date_gap_window}</M> gaps between prints, so it can be wrong around holidays
            and rescheduled auctions.</p>
          </div>
        </Panel>

        <Panel title="Data and Windows" span={6}>
          <div style={body}>
            <p><B>Inputs.</B> From <M>primary_yield_snapshots</M>, per tenor per auction: the cutoff yield,
            the amount offered and the amount accepted. Auctions with no cutoff are
            dropped, and where one date carries a tenor twice the last row is kept. Demand is measured as{" "}
            <M>offered / accepted</M>, the cover on what BB actually took. It is not the textbook bid-to-cover,
            because the announced amount is not stored.</p>
            <p><B>Windows.</B> A tenor is fitted and judged on its most recent{" "}
            <M>{spec.lookback_years.bills}</M> years of prints if it is a bill (<M>{spec.bill_tenors.join(", ")}</M>),
            and <M>{spec.lookback_years.bonds}</M> years otherwise. The longer bond window exists because bonds
            auction roughly monthly, so a short window leaves too few scored forecasts to tell skill from luck.
            See <Term t="Lookback Window" />.</p>
            <p><B>No look-ahead.</B> Every input for auction t is something a bidder could have known before
            bidding. Anything measured at auction t (its own cutoff, offered, accepted) enters only as a lag.
            The anchor is matched strictly to bill prints from an earlier date, because a bond and a bill can
            auction on the same day and that day&apos;s bill result is not knowable when the bond is bid.</p>
            <p><B>Factors.</B> The <M>{spec.factors.length}</M> lagged factors are{" "}
            {spec.factors.map((f, i) => <span key={f}>{i ? ", " : ""}<M>{f}</M></span>)}. The size and
            level-gap factors use a trailing mean over the last <M>{spec.trailing_window}</M> auctions. A row missing any
            factor is dropped, so the first auctions of a window and any auction with a missing amount do not
            enter a fit.</p>
          </div>
        </Panel>

        <Panel title="The Models" span={6}>
          <div style={body}>
            <p><M>{spec.models.length}</M> models, all predicting the same quantity (the next cutoff level),
            so their errors compare directly: <M>{spec.models.join(" · ")}</M>.</p>
            <ul style={list}>
              <li style={item}><B>{label.naive}</B>: <M>next = last cutoff</M>. The benchmark every other
              model must beat. See <Term t="Naive Benchmark" />.</li>
              <li style={item}><B>{label.momentum}</B>: <M>next = last + β × last change</M>. β is the
              least-squares slope, through the origin, of each past change on the change before it; refitted
              at every step. No intercept, so no built-in drift.</li>
              <li style={item}><B>{label.ols}</B>: <M>next = last + a + Σ b × factor</M> over the{" "}
              <M>{spec.factors.length}</M> factors above, fitted by ordinary least squares on every earlier
              auction. It needs at least <M>{spec.min_train_regression}</M> earlier rows, because it carries{" "}
              <M>{spec.factors.length + 1}</M> parameters.</li>
              <li style={item}><B>{label.curve}</B>: <M>next = last + λ × (move in the {anchorTenor} cutoff
              since this tenor last auctioned)</M>. λ is the least-squares slope through the origin of past
              changes on past anchor moves, so no anchor move means no predicted move. It is switched off for{" "}
              {anchorTenor} itself, which cannot be its own anchor. See <Term t="Curve Carry" /> and{" "}
              <Term t="Pass-through" />.</li>
              <li style={item}><B>{label.ecm}</B>: <M>next = last + a + b × (last cutoff − repo rate in force)
              + c × (change in repo)</M>. Pulls a cutoff toward the policy rate; see the error-correction
              panel for its current status.</li>
              <li style={item}><B>{label.blend}</B>: the plain average of every other model&apos;s forecast
              available at that auction, <i>naive included</i>. It therefore shrinks any one model toward
              last time.</li>
            </ul>
          </div>
        </Panel>

        <Panel title="Walk-Forward Validation" span={6}>
          <div style={body}>
            <p><B>Procedure.</B> After the first <M>{spec.min_train}</M> usable auctions of a tenor, step
            through the rest one at a time. At each auction t, refit every model on the auctions{" "}
            <i>strictly before</i> t, forecast t from its lagged inputs, record the error against what
            printed, then move on. The training set only grows (an expanding window) and no future print
            touches any fit. See <Term t="Out-of-sample" />.</p>
            <p><B>What is scored.</B> Mean absolute error, root-mean-square error, direction (how often the
            predicted sign of the change matched the actual one, counted only where the model predicted a
            non-zero change, so naive has none) and how often the forecast landed within{" "}
            <M>±{spec.hit_bps} bps</M>. The early → recent column is the first half of the scored errors
            against the second half.</p>
            <p><B>Cut-offs.</B> A tenor needs <M>{spec.min_history}</M> prints in its window and at least{" "}
            <M>{spec.min_scored}</M> scored forecasts, or it is not shown at all.</p>
            <p><B>How many auctions that is.</B> {hasData ? (
              <>Bill tenors have <M>{range(nBills, String)}</M> scored auctions each; the other tenors{" "}
              have <M>{range(nBonds, String)}</M>. The exact count for every model is the n column of the
              backtest table, and it differs by model because OLS and the error-correction model start later.
              Models are compared with naive only on the auctions both scored.</>
            ) : <>No forecast run is stored yet, so no counts can be shown. See the backtest table once the run exists.</>}</p>
          </div>
        </Panel>

        <Panel title="The Band, and Whether It Holds" span={6}>
          <div style={body}>
            <p><B>Construction.</B> Band = forecast ± <M>{spec.band.z}</M> × σ, where σ is an{" "}
            <Term t="EWMA Volatility">exponentially-weighted</Term> root-mean-square of the model&apos;s own
            out-of-sample errors, weight <M>{spec.band.ewma_lambda}</M> per step of age. Recent misses count
            more, so the band widens when the market turns volatile. If a model has too few errors for that
            estimate, the band falls back to <M>{spec.band.fallback_z}</M> × the RMSE of the most recent{" "}
            <M>{pct0(spec.band.fallback_recent_fraction)}</M> of its errors.</p>
            <p><B>It is not a guaranteed interval at its nominal level.</B> The multiplier is the normal-distribution one, and
            errors are not normal. So the share of backtested prints that really landed inside the band is
            measured per tenor and model and published as <Term t="Coverage">Band holds</Term>.{" "}
            {coverages.length
              ? <>In the stored run it ranges from <M>{range(coverages, pct0)}</M>.</>
              : <>No run is stored yet.</>}{" "}
            Treat the column, not the multiplier, as the truth about the band.</p>
            <p><B>Caveat on that measurement.</B> Coverage applies the band width as it stands today to every
            past error. It is a check on whether the band width was reasonable, not a record of bands as they
            were published at the time. The Live Track Record, which scores forecasts exactly as they were
            published, is the genuine out-of-sample check.</p>
          </div>
        </Panel>

        <Panel title="Verdict Rule and Multiple-Testing Correction" span={12}
          sub="a model is only called 'real edge' by a rule fixed in advance, never by looking at which has the lowest error">
          <div style={body}>
            <ol style={list}>
              <li style={item}><B>A range for the improvement.</B> For each challenger, the gap{" "}
              <M>naive MAE − model MAE</M> in bps, on the auctions both scored. Its{" "}
              <M>{pct0(spec.bootstrap.ci_level)}</M> range comes from a moving-block{" "}
              <Term t="Bootstrap" /> of <M>{spec.bootstrap.draws.toLocaleString("en-US")}</M> draws. Blocks are{" "}
              <M>{pct0(spec.bootstrap.block_fraction)}</M> of the sample long so neighbouring auctions stay
              together (errors are correlated week to week), the same blocks are used for both models so the
              pairing survives, and the random seed is fixed (<M>{spec.bootstrap.seed}</M>) so a published
              range is reproducible.</li>
              <li style={item}><B>A significance test.</B> <Term t="Diebold-Mariano" /> on squared error,
              with a Newey-West long-run variance
              {spec.verdict.dm_hln_correction
                ? ", the Harvey-Leybourne-Newbold small-sample correction and a t reference distribution"
                : ""}. Shown as the p-value in the backtest table.</li>
              <li style={item}><B>The verdict.</B> &quot;Worse than naive&quot; if the whole range is below
              zero. &quot;Real edge&quot; (<M>established</M>) only if the whole range is above zero{" "}
              <i>and</i> the p-value is below <M>{spec.verdict.dm_alpha}</M>. Anything else is &quot;could be
              luck&quot;.</li>
              <li style={item}><B>Many tests at once.</B> Screening many model-by-tenor comparisons manufactures
              winners by chance, so <Term t="Multiple Testing">Benjamini-Hochberg</Term> at q ={" "}
              <M>{spec.verdict.fdr_q}</M> is applied, but only to the pre-specified primary hypothesis:{" "}
              <M>{spec.primary_model}</M> beats naive, tested across tenors. It was chosen from a mechanism
              before it was tested. A primary-model &quot;real edge&quot; that fails the correction is
              demoted to &quot;could be luck&quot;.</li>
              <li style={item}><B>Everything else is exploratory.</B> Any other model that clears the verdict rule is
              relabelled <M>exploratory</M>: shown with its real numbers, and never promoted, whatever its
              p-value. The model the page recommends for a
              tenor is therefore the primary model where it is established, and naive otherwise.</li>
            </ol>
            {hasData && (
              <>
                <p>
                  <B>{label[primary]} against naive, in the stored run.</B>{" "}
                  {famTested.length} tenor{famTested.length === 1 ? "" : "s"} have a p-value (the test family);{" "}
                  {famPassed.length} pass the correction.
                </p>
                <div className="table-wrap">
                  <table className="dt">
                    <thead><tr>
                      <th>Tenor</th><th className="r">n</th>
                      <th className="r">Better than naive by (bps)</th>
                      <th className="r">p-value</th><th>Passes correction</th><th>Verdict</th>
                    </tr></thead>
                    <tbody>
                      {fam.map(r => (
                        <tr key={r.tenor}>
                          <td style={strong}>{r.tenor}</td>
                          <td className="r mono">{r.n_obs}</td>
                          <td className="r mono">
                            {r.gap_lo != null && r.gap_hi != null ? `${signed(r.gap_lo)} to ${signed(r.gap_hi)}` : DASH}
                          </td>
                          <td className="r mono">{r.dm_pvalue == null ? DASH : r.dm_pvalue.toFixed(4)}</td>
                          <td>{r.dm_fdr_pass == null ? DASH : r.dm_fdr_pass ? "yes" : "no"}</td>
                          <td>{r.verdict ? (VERDICT_TEXT[r.verdict] ?? r.verdict) : DASH}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </div>
        </Panel>

        <Panel title="When a Model Is Withheld" span={6}>
          <div style={body}>
            <p>A model is not forecast, rather than forecast badly, when its precondition fails. Nothing is
            filled in or estimated to cover the gap.</p>
            <ul style={list}>
              <li style={item}><B>Whole tenor:</B> fewer than <M>{spec.min_history}</M> prints in its window,
              or fewer than <M>{spec.min_scored}</M> scored forecasts. It has no row anywhere.</li>
              <li style={item}><B>{label.ols}:</B> fewer than <M>{spec.min_train_regression}</M> earlier
              auctions to train on. Short series, mostly bonds, get it late or never.</li>
              <li style={item}><B>{label.curve}:</B> the anchor tenor ({anchorTenor}) never gets it, and an
              auction with no earlier anchor print has no anchor move.</li>
              <li style={item}><B>{label.ecm}:</B> withheld unless the policy-corridor gate is open (at least{" "}
              <M>{spec.ecm_gate.min_verified_points}</M> verified corridor changes, repo at{" "}
              <M>{spec.ecm_gate.min_distinct_repo}</M> or more distinct levels, at least{" "}
              <M>{spec.ecm_gate.min_covered_years}</M> years covered), there are{" "}
              <M>{spec.min_train_regression}</M> or more training rows with policy data, and the fitted pull
              toward the repo rate is negative. A positive pull is explosive, not error correction, so it is
              withheld rather than published as one.</li>
              <li style={item}><B>{label.blend}:</B> fewer than two other models available at that auction.</li>
              <li style={item}><B>A statistic:</B> too few scored errors for a range or p-value, so that cell
              is a dash.</li>
            </ul>
            <p><B>What you see.</B> A withheld model simply has no row in the tables or bars in the chart,
            its statistics show a dash, and the headline falls back to naive wherever no challenger is
            established. The tables never carry an estimated or carried-forward stand-in.</p>
          </div>
        </Panel>

        <Panel title="Limitations" span={6}>
          <div style={body}>
            <ul style={list}>
              <li style={item}><B>One auction ahead only.</B> There is no multi-step path and no forecast of
              where a tenor will be after the next one.</li>
              <li style={item}><B>The calendar date is estimated</B> from the median gap between prints, not
              taken from BB&apos;s calendar.</li>
              <li style={item}><B>Samples are small for bonds.</B> {hasData
                ? <>Scored auctions per non-bill tenor: <M>{range(nBonds, String)}</M>, against{" "}
                <M>{range(nBills, String)}</M> for bills.</>
                : <>See the n column once a run is stored.</>}{" "}
              The bond window was lengthened after a shorter one left the long end unresolved, so that choice
              was made having seen results.</li>
              <li style={item}><B>Live forecast versus backtest.</B> The live forecast uses the anchor as of
              its run date. Each backtested forecast used the anchor just before its auction. When an auction
              falls days after the run, the live forecast is working from older anchor information than the
              backtest assumed.</li>
              <li style={item}><B>Only auction history.</B> No model sees an MPC decision, a BB circular, a
              funding squeeze or a devolvement. Those are step changes and the forecast will be most wrong
              exactly then.</li>
              <li style={item}><B>Diagnostics cover only the OLS factors</B> (stationarity, Granger, Newey-West
              inference, collinearity, residual autocorrelation) and, when the policy gate is open, the
              cutoff-to-repo relationship. The curve-carry model is judged by the backtest above, not by those
              tests.</li>
            </ul>
            <p style={sub}>This page contains no number typed by hand about the method: constants are read
            from the forecasting code and measured figures from the stored run. If the code changes, the
            build fails until the page is regenerated.</p>
          </div>
        </Panel>

      </div>
    </div>
  );
}
