import { tenorYears, byTenor as byTenorOrder } from "@/lib/tenor";
import { api, BacktestRow, ForecastRow, ForecastModel } from "@/lib/api";
import RelatedLinks from "@/components/RelatedLinks";
import { Panel } from "@/components/terminal/ui";
import ForecastIntervalChart from "@/components/ForecastIntervalChart";
import BacktestTrackChart from "@/components/BacktestTrackChart";
import DownloadButton from "@/components/DownloadButton";
import ResearchIntro from "@/components/ResearchIntro";
import ForecastMethod from "@/components/ForecastMethod";
import spec from "@/lib/forecastMethod.json";
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
// Thresholds the page quotes come from forecastMethod.json (generated from the forecasting code), never retyped.
const CI_PCT = Math.round(spec.bootstrap.ci_level * 100);
const DM_ALPHA = spec.verdict.dm_alpha;
const DIAG_ALPHA = spec.diagnostics.alpha;
const spanOf = (xs: number[], f: (v: number) => string) =>
  xs.length === 0 ? DASH : Math.min(...xs) === Math.max(...xs) ? f(xs[0]) : `${f(Math.min(...xs))} to ${f(Math.max(...xs))}`;

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
  const [fc, track, diags, board] = await Promise.all([
    api.forecast(),
    api.forecastPredictions(TRACK_TENOR),
    api.forecastDiagnostics(),
    api.forecastScoreboard(),
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
  // Scored AUCTIONS (one per tenor and target date, however many models and runs forecast it), counted by the API.
  // fc.track_record is only the latest rows for display and is never counted here. Absent on an older API: a dash.
  const ts = fc.track_summary ?? null;
  // Economic value is only stored for established models, so this list is
  // already filtered to edges the page is willing to stand behind.
  const evRows = tenors
    .map(t => {
      const e = byTenor.get(t)!;
      const m = MODEL_ORDER.find(k => e[k]?.ev_pickup_bps != null);
      return m ? { tenor: t, model: m, metric: e[m]! } : null;
    })
    .filter((x): x is { tenor: string; model: ForecastModel; metric: BacktestRow } => x !== null);

  const empty = fc.run_date == null;
  // The tab served two-week-old forecasts in Aug 2026 with no visible warning
  // while the scheduled job was failing. Age is now shown on the page itself.
  const staleDays = fc.run_date
    ? Math.floor((Date.now() - new Date(fc.run_date).getTime()) / 86_400_000)
    : null;
  const gate = diags.find(d => d.test === "policy_gate");
  const gateBlocked = gate?.conclusion?.startsWith("BLOCKED") ?? false;

  // Figures the prose below quotes, read from the stored run instead of typed.
  const naive91 = byTenor.get(TRACK_TENOR)?.naive ?? null;
  // Bill or not comes from each series' own security type (stored with its forecasts), never from a list of tenors.
  const instrOf = new Map<string, string | null>(fc.forecasts.map(f => [f.tenor, f.instrument] as [string, string | null]));
  const nBonds = fc.metrics
    .filter(m => m.model === "naive" && instrOf.get(m.tenor) != null && instrOf.get(m.tenor) !== spec.bill_instrument)
    .map(m => m.n_obs);
  const grangerTests = diags.filter(d => d.test === "granger" && d.pvalue != null);
  const grangerHits = grangerTests.filter(d => (d.pvalue as number) < DIAG_ALPHA);
  const diagP = (test: string, subject: string) =>
    diags.filter(d => d.test === test && d.subject === subject && d.pvalue != null).map(d => d.pvalue as number);
  const spreadAdfP = diagP("adf", "spread_policy");
  const cointP = diagP("coint", "cutoff~repo");
  const ecmRows = fc.metrics.filter(m => m.model === "ecm");
  // The ECM's status is the verdict the weekly diagnostics run COMPUTED from the stored p-values ("WORD - detail"),
  // never a word typed here. Without one (the run has not happened since this was added) the page says so.
  const ecmV = diags.find(d => d.test === "ecm_verdict")?.conclusion ?? null;
  const ecmWord = ecmV ? ecmV.split(" - ")[0] : gateBlocked ? "BLOCKED" : "NOT YET COMPUTED";
  const ecmWhy = ecmV ? ecmV.split(" - ").slice(1).join(" - ")
    : gateBlocked ? (gate?.conclusion ?? "").replace(/^(BLOCKED|OPEN) — /, "")
    : "the next weekly diagnostics run records the verdict";
  const ecmColor = ecmWord === "REJECTED" ? "var(--neg)" : ecmWord === "SUPPORTED" ? "var(--pos)" : "var(--warn)";
  const lams = fc.metrics.filter(m => m.model === "curve" && m.curve_lambda != null)
    .sort((a, b) => byTenorOrder(a.tenor, b.tenor));
  const lamText = lams.length
    ? lams.map(m => `${m.tenor} ${(m.curve_lambda as number).toFixed(2)}`).join(" · ")
    : "not available yet (it appears after the next weekly run)";
  const spreadStationary = spreadAdfP.filter(v => v < DIAG_ALPHA).length;
  const cointSignificant = cointP.filter(v => v < DIAG_ALPHA).length;

  return (
    // Fragment: the page needs no client state at the top level — each Term
    // manages its own tooltip and concept reader.
    <>
      {!empty && staleDays != null && staleDays > 8 && (
        <div style={{
          border: "1px solid var(--warn)", borderRadius: "var(--radius-sm)",
          padding: "8px 12px", marginBottom: "var(--gap)", fontSize: 12.5,
          color: "var(--warn)", background: "color-mix(in oklab, var(--warn) 10%, transparent)",
        }}>
          ⚠ FORECASTS ARE {staleDays} DAYS OLD — the weekly job runs Thursday, so anything past
          about 8 days means it has not completed. These numbers may pre-date auctions that have
          already printed. Check the Weekly Forecast workflow before bidding off this page.
        </div>
      )}

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
          <div className="kpi-val">
            <span className="kpi-num">{ts ? ts.scored_auctions : DASH}</span><span className="kpi-unit">auctions scored</span>
          </div>
          <div className="kpi-sub" style={ts && ts.overdue_unscored ? { color: "var(--warn)" } : undefined}
            title={ts?.overdue_tenors.length ? `unscored: ${ts.overdue_tenors.join(", ")}` : undefined}>
            {!ts ? "not reported by the API yet"
              : ts.overdue_unscored ? `${ts.overdue_unscored} more past their date, still unscored`
              : ts.scored_auctions ? "published forecasts vs actual prints" : "builds as auctions print"}
          </div>
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
                <th className="r"><Term t="Coverage">Band low</Term></th>
                <th className="r"><Term t="Coverage">Band high</Term></th>
                <th className="r">Typical miss<br /><span style={{ fontWeight: 400, color: "var(--fg-mute)" }}>recent, bps</span></th>
                <th className="r">Right direction</th>
                <th>Verdict</th>
              </tr></thead>
              <tbody>
                {headline.map(({ row: f, model, metric }, i) => {
                  const chg = f.last_actual_yield != null ? f.point_yield - f.last_actual_yield : null;
                  const strong = metric.dm_pvalue != null && metric.dm_pvalue < DM_ALPHA;
                  return (
                    <tr key={i}>
                      <td style={{ fontWeight: 600 }}>{f.tenor}</td>
                      <td>
                        <span style={{ color: model === "naive" ? "var(--fg-mute)" : "var(--accent)", fontWeight: 500 }}>
                          {MODEL_LABEL[model]}
                        </span>
                        {strong && <span title={`Beats naive at p<${DM_ALPHA} (Diebold-Mariano)`}
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

        <Panel title="Live Trust Scoreboard — What Was Published, Against What Printed" span={12} pad={false}
          sub={'built only from forecasts that were stored BEFORE their auction · the model scored is the one this page recommended in the run that published · '
            + `a tenor shows no figures until it has ${board.min_scored ?? spec.min_scored} scored auctions`}>
          <div style={{ padding: "10px 14px", fontSize: 12, color: "var(--fg-mute)", borderBottom: "1px solid var(--border-soft)" }}>
            This is the check the backtest cannot be. &quot;Inside the band&quot; is how often the live band really held;
            &quot;claimed&quot; is the backtest&apos;s own measurement of the same thing. Where live is clearly below claimed,
            the band was too narrow. Win rate is over auctions where the recommended model was not naive itself.
            A forecast made on or after its target date is never counted.
          </div>
          <div className="table-wrap">
            <table className="dt">
              <thead><tr>
                <th>Tenor</th><th>Model</th><th className="r">Scored auctions</th>
                <th className="r">Live miss<br /><span style={{ fontWeight: 400 }}>bps, mean</span></th>
                <th className="r">Naive miss<br /><span style={{ fontWeight: 400 }}>bps, mean</span></th>
                <th className="r">Beat naive</th>
                <th className="r">Inside band, live</th>
                <th className="r">Inside band, claimed</th>
              </tr></thead>
              <tbody>
                {board.tenors.map(r => (
                  <tr key={r.tenor}>
                    <td style={{ fontWeight: 600 }}>{r.tenor}</td>
                    <td>{MODEL_LABEL[r.recommended_model] ?? r.recommended_model}</td>
                    <td className="r mono">{r.scored_auctions}</td>
                    {r.status === "ok" ? (
                      <>
                        <td className="r mono" style={{ fontWeight: 600 }}>{r.live_mae_bps?.toFixed(1) ?? DASH}</td>
                        <td className="r mono" style={{ color: "var(--fg-mute)" }}>{r.naive_mae_bps?.toFixed(1) ?? DASH}</td>
                        <td className="r mono">{r.win_rate == null ? DASH : `${pct1(r.win_rate)} of ${r.win_rate_n}`}</td>
                        <td className="r mono" style={{ fontWeight: 600 }}>{pct1(r.inside_band)}</td>
                        <td className="r mono" style={{ color: "var(--fg-mute)" }}>{pct1(r.claimed_coverage)}</td>
                      </>
                    ) : (
                      <td colSpan={5} style={{ color: "var(--warn)", fontSize: 11.5 }}>
                        insufficient — {r.scored_auctions} of {r.min_scored} scored auctions needed before any figure is shown
                      </td>
                    )}
                  </tr>
                ))}
                {board.tenors.length === 0 && (
                  <tr><td colSpan={8} style={{ textAlign: "center", color: "var(--fg-mute)", padding: 16 }}>
                    No published forecast has been scored against a print yet, or the API has not been updated to report it.
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

        {evRows.length > 0 && (
          <Panel title="What the Edge Is Worth" span={12} pad={false}
            sub={`bidding the model instead of the last print, both shaded to the same ${
              Math.round((evRows[0].metric.ev_fill_target ?? 0.9) * 100)}% fill rate — `
              + "equal fills means equal notional deployed, so no reinvestment assumption is needed"}>
            <div style={{ padding: "10px 14px", fontSize: 12, color: "var(--fg-mute)",
                          borderBottom: "1px solid var(--border-soft)" }}>
              BB fills bids from the lowest yield up; the cutoff is the highest accepted. Bid above it
              and you miss the auction, bid below and you are filled but leave yield on the table. A
              better forecast is worth money because it lets you bid closer to the cutoff without
              missing. Shown only for tenors whose edge is{" "}
              <Term t="Selection Bias">statistically established</Term>.
            </div>
            <div className="table-wrap">
              <table className="dt">
                <thead><tr>
                  <th><Term t="Tenor" /></th><th>Model</th>
                  <th className="r">Shade needed</th>
                  <th className="r">Naive needs</th>
                  <th className="r">Yield pickup</th>
                  <th className="r">Per 100cr bid<br /><span style={{ fontWeight: 400 }}>BDT lakh, to maturity</span></th>
                </tr></thead>
                <tbody>
                  {evRows.map(({ tenor, model, metric: m }) => {
                    // Cash on a 100 crore bid, held to maturity, UNDISCOUNTED.
                    // Not summed across tenors: a 91D and a 20Y commitment are
                    // not the same money and adding them would be meaningless.
                    const yrs = tenorYears(tenor) ?? 1;
                    const lakh = 100 * ((m.ev_pickup_bps ?? 0) / 10000) * yrs * 100;
                    return (
                      <tr key={tenor}>
                        <td style={{ fontWeight: 600 }}>{tenor}</td>
                        <td style={{ color: "var(--accent)" }}>{MODEL_LABEL[model]}</td>
                        <td className="r mono">{m.ev_shade_bps?.toFixed(1) ?? DASH} bps</td>
                        <td className="r mono" style={{ color: "var(--fg-mute)" }}>{m.ev_bench_shade_bps?.toFixed(1) ?? DASH} bps</td>
                        <td className="r mono" style={{ fontWeight: 600, color: "var(--pos)" }}>
                          +{m.ev_pickup_bps?.toFixed(1) ?? DASH} bps
                        </td>
                        <td className="r mono">{lakh.toFixed(1)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div style={{ padding: "10px 14px", fontSize: 11.5, color: "var(--fg-mute)",
                          borderTop: "1px solid var(--border-soft)" }}>
              <b style={{ color: "var(--warn)" }}>Read this before quoting the number.</b> The cash
              column is undiscounted and assumes the paper is held to maturity, so a 20Y row spans
              twenty years of coupons from a single auction — it is not annual P&amp;L, and the rows
              must not be added together. The simulation also assumes all-or-nothing fills, a fixed
              notional at every auction, no competitor reaction, and no reflexivity: bidding
              differently would at the margin move the very cutoff being forecast. Treat the{" "}
              <b style={{ color: "var(--fg)" }}>bps pickup</b> as the honest figure and the cash as an
              illustration.
            </div>
          </Panel>
        )}

        <Panel title="Backtest — Model vs Naive" span={12} pad={false}
          sub={'every model tested against "same as last time" on auctions it never saw · '
            + `"better than naive by" is a ${CI_PCT}% range from ${spec.bootstrap.draws.toLocaleString("en-US")} bootstrap resamples — if it straddles 0, the edge is not proven`}
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
                  <span style={{ fontWeight: 400 }}>bps, {CI_PCT}% range</span></th>
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
                          <td className="r mono" style={{ color: r.dm_pvalue != null && r.dm_pvalue < DM_ALPHA ? "var(--fg)" : "var(--fg-mute)" }}>
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
                        <td className="r mono" style={{ fontWeight: d.pvalue != null && d.pvalue < DIAG_ALPHA ? 600 : 400 }}>
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
            sub="the pull-to-anchor model from the project guide (Phase 5.3) · the verdict is recomputed every week from the stored test results">
            <div style={{ fontSize: 12.5, lineHeight: 1.75, color: "var(--fg-mute)", maxWidth: 980 }}>
              <div style={{
                display: "inline-flex", alignItems: "center", gap: 8, marginBottom: 12,
                padding: "6px 12px", borderRadius: "var(--radius-sm)", fontSize: 12,
                border: `1px solid ${ecmColor}`, color: ecmColor,
                background: `color-mix(in oklab, ${ecmColor} 10%, transparent)`,
              }}>
                <b>{ecmWord}</b>
                <span style={{ color: "var(--fg-dim)" }}>{ecmWhy}</span>
              </div>

              <p><b style={{ color: "var(--fg)" }}>The idea.</b> An ECM says a cutoff sitting far above
              the policy rate should be pulled back toward it, at a speed the data estimates. The
              project guide calls this the most theoretically honest specification for a corridor
              regime.</p>

              <p><b style={{ color: "var(--fg)" }}>The data.</b>{" "}
              The corridor history comes from Bangladesh Bank&apos;s own MPD circulars, parsed from the
              circular PDFs. Every entry is verified twice: each circular states the rate it is replacing, so
              the chain validates link by link, and the final corridor it produces equals what BB publishes live
              today.</p>

              {(spreadAdfP.length > 0 || cointP.length > 0) && (
                <>
                  <p><b style={{ color: "var(--fg)" }}>What the tests say.</b></p>
                  <ul style={{ paddingLeft: 20, margin: "0 0 10px" }}>
                    {spreadAdfP.length > 0 && (
                      <li style={{ marginBottom: 5 }}>The spread (cutoff − repo) is{" "}
                      <b style={{ color: "var(--fg)" }}>stationary on {spreadStationary} of {spreadAdfP.length} tenors</b>
                      {" "}(<Term t="Stationarity">ADF</Term> p = {spanOf(spreadAdfP, v => v.toFixed(2))}, threshold {DIAG_ALPHA}).</li>
                    )}
                    {cointP.length > 0 && (
                      <li style={{ marginBottom: 5 }}>Engle-Granger finds <Term t="Cointegration" /> between cutoff and
                      policy rate on <b style={{ color: "var(--fg)" }}>{cointSignificant} of {cointP.length} tenors</b>
                      {" "}(p = {spanOf(cointP, v => v.toFixed(2))}, threshold {DIAG_ALPHA}).</li>
                    )}
                    <li>
                      {ecmRows.length === 0
                        ? "No tenor has enough scored ECM forecasts to publish ECM numbers"
                        : <>The ECM publishes scored forecasts on only {ecmRows.map((m, i) => (
                            <span key={m.tenor}>{i ? "; " : ""}{m.tenor} (n = {m.n_obs}, {m.verdict ?? DASH})</span>
                          ))}</>}
                      {" "}— a model with fewer than {spec.min_scored} scored forecasts, or whose fitted pull toward the
                      repo rate is not negative, is withheld. See its rows in the backtest table for its error against naive.</li>
                  </ul>
                </>
              )}

              {ecmWord === "REJECTED" && (
                <p>The tests agree, which is what makes this credible rather than a single unlucky test: over
                this sample BB&apos;s cutoffs are <i>not</i> anchored to the policy corridor in a way an
                error-correction model can exploit, so the ECM is not recommended for any tenor.</p>
              )}
              {ecmWord === "SUPPORTED" && (
                <p>Cointegration alone does not make the ECM the recommended model: its forecasts are judged by
                the same verdict rule as every other model, in the backtest table.</p>
              )}

              <p style={{ color: "var(--fg-dim)" }}><b style={{ color: "var(--fg)" }}>Worth knowing
              anyway.</b> The corridor history is a first-class series in the database, so the spread is
              available to any future model, and this result is re-tested automatically every week. If BB
              tightens the corridor&apos;s grip on auction pricing, the verdict will change by itself.</p>
            </div>
          </Panel>
        )}

        <ForecastMethod fc={fc} label={MODEL_LABEL} />

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
            <p>Concretely: on {TRACK_TENOR}, naive is wrong by{" "}
            <b style={{ color: "var(--fg)" }}>{naive91?.mae_bps != null ? `${naive91.mae_bps.toFixed(1)} bps` : DASH} on
            average</b> and lands within ±{spec.hit_bps} bps {naive91?.hit_5bps != null ? pct1(naive91.hit_5bps) : DASH} of
            the time. To be useful, a model has to do better than that <i>consistently</i>, not once.</p>

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
            <p>The fitted λ is how the curve behaves in numbers: a λ near one means a move in the bill
            anchor passes almost one-for-one into that tenor, a smaller λ means that part of the curve
            moves less than the bills. λ is estimated from the data, not assumed, and it is re-estimated at
            every step of the backtest. The λ applied to this week&apos;s live forecast is stored with the curve model&apos;s metrics:{" "}
            <b style={{ color: "var(--fg)" }}>{lamText}</b>.</p>

            <H>How an edge is judged, and the band</H>
            <p>A lower average error in a backtest is a <i>claim</i>, not a fact, and the page does not
            pick the lowest-error model by eye. The bootstrap range, the small-sample significance test,
            the multiple-testing correction, the fixed verdict rule and the construction of the band (and
            the coverage actually measured for it) are set out in full, with every constant read from the
            code, in <a href="#method" style={{ color: "var(--accent)" }}>How the Yield Curve Is
            Forecast</a> below.</p>

            <H>Why the average error understates today</H>
            <p>Splitting the backtest in half is uncomfortable reading. On {TRACK_TENOR} the typical miss of naive was{" "}
            <b style={{ color: "var(--fg)" }}>{naive91?.mae_first != null && naive91?.mae_recent != null
              ? `${naive91.mae_first.toFixed(1)} bps early on and ${naive91.mae_recent.toFixed(1)} bps recently`
              : "shown early → recent in the table"}</b>; compare the other tenors in the same column. The models did not break — the market got more volatile. But it means a
            single lifetime-average error is the wrong number to size against, which is why the tables show
            <b style={{ color: "var(--fg)" }}> early → recent</b> and the headline uses the recent figure.</p>

            <H>The insight that failed, and why that is useful too</H>
            <p><b style={{ color: "var(--fg)" }}>OLS</b> regresses the cutoff change on auction-demand
            factors — cover ratio, its change, relative size, distance from the trailing mean. Its verdicts are in
            the backtest table: &quot;could be luck&quot; or &quot;worse than naive&quot; means the factors add noise. The
            Granger tests point the same way: only {grangerHits.length} of {grangerTests.length} factor-by-tenor
            tests show a factor that <b style={{ color: "var(--fg)" }}>leads</b> the cutoff at p &lt; {DIAG_ALPHA}, and
            each is the best of several lags with no correction for running that many tests, so a few
            are expected by chance alone. Mostly these factors describe an auction that has already
            happened, and describing is not predicting. Each
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
            A lower MAE could be luck across a few dozen auctions. DM tests whether the difference in squared
            error is statistically real. <b style={{ color: "var(--fg)" }}>p &lt; {DM_ALPHA} is necessary for the
            edge to count; it is not sufficient (the bootstrap range must also sit above zero).</b> Momentum is the example: where its
            p-value does not clear the bar the verdict column reads &quot;could be luck&quot;, and if it did
            clear it the label would be &quot;exploratory only&quot;, because only the primary model can be
            promoted.</p>
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
            It is {spec.band.z} × an EWMA estimate of the model&apos;s own out-of-sample error, so its width is
            measured from past misses, not assumed; how often prints really landed inside it is the Band holds
            column. A narrow band on a bill means your rate view is worth acting on; a very wide band on a
            bond means the model is explicitly telling you it cannot call that auction, and size accordingly.</p>
            <p><b style={{ color: "var(--fg)" }}>Direction first, level second.</b> On bonds the curve
            model&apos;s edge is largely directional (see the Right direction column). Use it to decide which side of the
            last print to bid, not to pin a basis point.</p>
            <p><b style={{ color: "var(--fg)" }}>Where the model is blind.</b> It sees only auction
            history. It cannot see an MPC decision, a BB circular, a quarter-end funding squeeze, a
            large government payment, or a devolvement. Those are step-changes, and they are precisely
            when the forecast will be most wrong. Overlay your own judgement on those weeks and record
            both calls — the model forecast and your adjusted one — so you can tell later which added
            value.</p>
            <p><b style={{ color: "var(--fg)" }}>Watch λ for regime change.</b> If a tenor&apos;s
            pass-through drifts materially from its usual level (it is stored with each weekly run), the relationship between that tenor
            and the bill curve is changing — a signal in its own right, and a reason to stop trusting
            the curve forecast until it settles.</p>
            <p><b style={{ color: "var(--fg)" }}>Reflexivity.</b> If the desk bids differently because
            of this model, at the margin it affects the cutoff it is forecasting. Small at your size,
            but it is the reason a published track record matters more than a backtest.</p>
          </div>
        </Panel>

        <Panel title="Honest Limitations" span={12}>
          <div style={{ fontSize: 12.5, lineHeight: 1.7, color: "var(--fg-mute)", maxWidth: 980 }}>
            <p><b style={{ color: "var(--fg)" }}>Bond samples are small.</b> Non-bill tenors have{" "}
            {spanOf(nBonds, String)} scored auctions each, against far more for bills. The curve model&apos;s
            improvement is large and mechanically sensible, but it rests on a short record. Which tenors are established, and which are not, is in the verdict column and the
            table in the method section; read the p-value, not the pattern.</p>

            <p><b style={{ color: "var(--fg)" }}>The curve model can make an aggressive call.</b> When the
            anchor has moved sharply since a long bond last auctioned, a λ near one projects a large move
            at that tenor. That is the mechanism working as designed, not a glitch, but it is an
            extrapolation from an unusually big anchor move, and the band around it is wide for exactly that
            reason. Size to the band.</p>

            <p><b style={{ color: "var(--fg)" }}>Why the guide&apos;s full factor set isn&apos;t here.</b> Call money, OMO
            and reference rates only start March 2026 in this database, so the call-money
            spread and an excess-liquidity factor cannot be built without inventing history. They are
            deliberately absent rather than fitted on placeholders. The policy corridor, by contrast, is stored
            as verified effective-dated history from BB&apos;s own circulars and feeds the error-correction
            model (<span className="mono">d_policy</span>) through the policy gate above.</p>

            <p><b style={{ color: "var(--fg)" }}>The band is measured, not assumed.</b> It is {spec.band.z} × an EWMA estimate of the model&apos;s own
            out-of-sample error from an expanding-window backtest — refit on everything up to week t, predict week t,
            score, roll forward. No future data ever touches a fit, so the width is what this model actually delivered,
            not what a distributional assumption would flatter it into claiming.</p>

            <p><b style={{ color: "var(--fg)" }}>A wide band is information, not a defect.</b> Where a model cannot
            call an auction, its band says so in basis points. Read the band before the point estimate.</p>

            {spec.target_from_calendar
              ? <p><b style={{ color: "var(--fg)" }}>Target auction dates</b> are BB&apos;s published auction calendar
                where it holds a date after the last print, and otherwise inferred from the median recent gap between
                prints. Expect inferred ones to shift around holidays.</p>
              : <p><b style={{ color: "var(--fg)" }}>Target auction dates are inferred</b> from the median recent gap between
                prints, not from BB&apos;s published auction calendar. Expect them to shift around holidays.</p>}

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
