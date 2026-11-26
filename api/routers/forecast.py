"""
api/routers/forecast.py — pre-computed auction cutoff-rate forecasts.

READ-ONLY BY DESIGN. Nothing is modelled here: this endpoint is deliberately a
thin SELECT over two tables that forecast/run_forecast.py populates from GitHub
Actions every week. Vercel's serverless Python cannot carry a stats stack, so
no modelling dependency may ever be imported in this file — if a future model
needs statsmodels, it goes in the CI job, not here.
"""
import datetime

from fastapi import APIRouter
from sqlalchemy import text
from db import get_session

router = APIRouter()


@router.get("")
def get_forecast():
    """
    Latest forecast run + the backtest that says how much to trust it.

    { run_date, forecasts: [...], metrics: [...], track_record: [...], track_summary: {...} | null }

    `forecasts`   one row per tenor per model from the most recent run, with the
                  last actual print for context.
    `metrics`     rolling out-of-sample skill as of the same run.
    `track_record` past forecasts whose auction has since printed and been scored
                  (the latest 200 ROWS, for display; do not count them).
    `track_summary` scored AUCTIONS and overdue-unscored ones (forecast_logic.track_summary).
    Returns empty lists (not an error) before the first CI run populates them.
    """
    session = get_session()
    try:
        run = session.execute(text(
            "SELECT MAX(forecast_run_date) AS d FROM auction_forecasts"
        )).fetchone()
        run_date = run._mapping["d"] if run else None
        if run_date is None:
            return {"run_date": None, "forecasts": [], "metrics": [], "track_record": []}

        # DISTINCT ON keeps only the FURTHEST-OUT target per tenor/model. A single
        # run can hold two targets for one tenor — re-running after that week's
        # auction has printed rolls the target forward while the superseded row
        # keeps the same run date. Without this the tab shows each bill twice and
        # the chart silently overplots the stale forecast on the live one.
        forecasts = session.execute(text("""
            SELECT DISTINCT ON (f.tenor, f.model)
                   f.tenor, f.instrument, f.model, f.target_auction_date,
                   f.point_yield, f.lo_yield, f.hi_yield,
                   p.cutoff_yield_pct AS last_actual_yield,
                   p.auction_date     AS last_auction_date
            FROM auction_forecasts f
            LEFT JOIN LATERAL (
                SELECT cutoff_yield_pct, auction_date
                FROM primary_yield_snapshots
                WHERE tenor_label = f.tenor AND cutoff_yield_pct IS NOT NULL
                ORDER BY auction_date DESC LIMIT 1
            ) p ON TRUE
            WHERE f.forecast_run_date = :d
            ORDER BY f.tenor, f.model, f.target_auction_date DESC
        """), {"d": run_date}).fetchall()

        metric_sql = """
            SELECT tenor, model, mae_bps, rmse_bps, rmse_recent_bps, dir_acc, hit_5bps, n_obs, dm_pvalue,
                   mae_lo, mae_hi, gap_lo, gap_hi, coverage, mae_first, mae_recent, verdict, dm_fdr_pass, band_bps, ev_pickup_bps, ev_shade_bps, ev_bench_shade_bps, ev_fill_target{extra}
            FROM backtest_metrics
            WHERE computed_date = (SELECT MAX(computed_date) FROM backtest_metrics)
            ORDER BY tenor, model
        """
        try:
            # curve_lambda is added to the table by db.py's migration list when the forecast job next starts; until then
            # the column is absent and the same query runs without it (the page shows a dash).
            metrics = session.execute(text(metric_sql.format(extra=", curve_lambda"))).fetchall()
        except Exception:
            session.rollback()
            metrics = session.execute(text(metric_sql.format(extra=""))).fetchall()

        track = session.execute(text("""
            SELECT tenor, model, target_auction_date, forecast_run_date,
                   point_yield, lo_yield, hi_yield, actual_yield
            FROM auction_forecasts
            WHERE actual_yield IS NOT NULL
            ORDER BY target_auction_date DESC, tenor, model
            LIMIT 200
        """)).fetchall()

        def rows(rs):
            return [{k: (str(v) if hasattr(v, "isoformat") else v)
                     for k, v in r._mapping.items()} for r in rs]

        from .forecast_logic import track_summary
        try:
            groups = session.execute(text("""
                SELECT tenor, target_auction_date,
                       MAX(CASE WHEN actual_yield IS NOT NULL THEN 1 ELSE 0 END) AS scored
                FROM auction_forecasts
                WHERE target_auction_date IS NOT NULL
                GROUP BY tenor, target_auction_date
            """)).fetchall()
            summary = track_summary([dict(g._mapping) for g in groups], datetime.date.today())
        except Exception:
            session.rollback()
            summary = None

        return {
            "run_date": str(run_date),
            "forecasts": rows(forecasts),
            "metrics": rows(metrics),
            "track_record": rows(track),
            "track_summary": summary,
        }
    finally:
        session.close()


@router.get("/scoreboard")
def get_scoreboard():
    """Live trust scoreboard: per tenor, what the page published before each auction against what printed.

    Built only from auction_forecasts (the published log); backtest_metrics just says which model was recommended in
    the run that published and what coverage it claimed. See forecast_logic.scoreboard for the rules.
    """
    from .forecast_logic import MIN_SCORED, scoreboard
    session = get_session()
    try:
        forecasts = session.execute(text("""
            SELECT tenor, model, target_auction_date, forecast_run_date,
                   point_yield, lo_yield, hi_yield, actual_yield
            FROM auction_forecasts
            WHERE actual_yield IS NOT NULL AND target_auction_date IS NOT NULL
              AND forecast_run_date < target_auction_date
        """)).fetchall()
        metrics = session.execute(text("""
            SELECT computed_date, tenor, model, verdict, mae_bps, coverage
            FROM backtest_metrics
        """)).fetchall()
        out = scoreboard([dict(r._mapping) for r in forecasts], [dict(r._mapping) for r in metrics])
        out["as_of"] = str(datetime.date.today())
        return out
    except Exception:
        session.rollback()
        return {"min_scored": MIN_SCORED, "tenors": [], "as_of": None}
    finally:
        session.close()


@router.get("/predictions")
def get_predictions(tenor: str = "91D"):
    """Backtest out-of-sample predictions vs actual for one tenor.

    A SIMULATION, not the published forecast log — see the note on
    BacktestPrediction. The tab labels it as such.
    """
    session = get_session()
    try:
        rows = session.execute(text("""
            SELECT auction_date, model, actual_yield, pred_yield
            FROM backtest_predictions
            WHERE tenor = :t
              AND computed_date = (SELECT MAX(computed_date) FROM backtest_predictions)
            ORDER BY auction_date, model
        """), {"t": tenor.upper()}).fetchall()
        return [{"auction_date": str(r._mapping["auction_date"]),
                 "model": r._mapping["model"],
                 "actual_yield": r._mapping["actual_yield"],
                 "pred_yield": r._mapping["pred_yield"]} for r in rows]
    finally:
        session.close()


@router.get("/diagnostics")
def get_diagnostics():
    """Latest Phase-4 statistical test results (ADF/KPSS/Granger/VIF/OLS/Ljung-Box)."""
    session = get_session()
    try:
        rows = session.execute(text("""
            SELECT tenor, test, subject, statistic, pvalue, lag, conclusion
            FROM research_diagnostics
            WHERE computed_date = (SELECT MAX(computed_date) FROM research_diagnostics)
            ORDER BY tenor, test, subject
        """)).fetchall()
        return [dict(r._mapping) for r in rows]
    finally:
        session.close()
