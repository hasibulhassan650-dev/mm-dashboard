// Live API host (Vercel free tier). Hardcoded on purpose: the old Railway
// host died with the trial, and a stale NEXT_PUBLIC_API_URL in the Vercel
// dashboard was overriding the fallback and blanking the site. Ignore the
// env var; change this constant if the API ever moves again.
const BASE = "https://mm-dashboard-vac3.vercel.app";

async function get<T>(path: string, params?: Record<string, string | number>): Promise<T> {
  const url = new URL(BASE + path);
  if (params) Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, String(v)));
  const res = await fetch(url.toString(), { next: { revalidate: 300 } });
  if (!res.ok) throw new Error(`API ${path} → ${res.status}`);
  return res.json();
}

export const api = {
  omoSummary:      () => get<OmoSummaryRow[]>("/api/omo/summary"),
  omoOutstanding:  (days = 90) => get<OmoOutstandingRow[]>("/api/omo/outstanding", { days }),
  omoTransactions: (days = 60) => get<OmoTxnRow[]>("/api/omo/transactions", { days }),
  yieldCurve:      () => get<YieldRow[]>("/api/yields/curve"),
  yieldSecondary:  () => get<SecondaryYieldRow[]>("/api/yields/secondary"),
  yields:          (months = 12) => get<YieldRow[]>("/api/yields", { months }),
  // Explicit date window (YYYY-MM-DD) — supports the full 2007→present range.
  yieldsRange:     (from: string, to: string, tenor?: string) =>
                     get<YieldRow[]>("/api/yields", tenor ? { date_from: from, date_to: to, tenor } : { date_from: from, date_to: to }),
  yieldDateRange:  async (): Promise<{ min: string | null; max: string | null; count: number }> => {
                     try { return await get("/api/yields/range"); }
                     catch { return { min: null, max: null, count: 0 }; }
                   },
  // New endpoint — degrade to empty until the backend is deployed with /api/yields/slope.
  yieldSlope:      async (months = 24): Promise<CurveSlopeRow[]> => {
    try { return await get<CurveSlopeRow[]>("/api/yields/slope", { months }); }
    catch { return []; }
  },
  securities:      () => get<Security[]>("/api/securities"),
  flows:           (months = 6) => get<FlowRow[]>("/api/flows", { months }),
  callmoney:       (days = 90)  => get<CallMoneyResult>("/api/callmoney", { days }),
  fx:              (days = 365) => get<FxAuctionRow[]>("/api/fx", { days }),
  refrate:         (days = 90)  => get<RefRateRow[]>("/api/refrate", { days }),
  drilldown:       (date: string) => get<DrilldownResult>(`/api/flows/drilldown`, { date }),
  // New endpoint — degrade to empty until the backend is deployed with /api/flows/forecast.
  flowsForecast:   async (days = 28): Promise<LiquidityForecast> => {
    try { return await get<LiquidityForecast>("/api/flows/forecast", { days }); }
    catch { return { as_of: "", days: [], unit: "BDT crore" }; }
  },
  // Non-critical: must never break a page. Returns all-nulls if the endpoint is unavailable.
  freshness:       async (): Promise<Freshness> => {
    try { return await get<Freshness>("/api/meta/freshness"); }
    catch { return EMPTY_FRESHNESS; }
  },
  status:          async (): Promise<MetaStatus> => {
    try { return await get<MetaStatus>("/api/meta/status"); }
    catch { return { datasets: {}, last_run: null, last_run_errors: [], cadence: "" }; }
  },
  // New endpoint — degrade to empty corridor until the backend is deployed.
  policy:          async (): Promise<PolicyCorridor> => {
    try { return await get<PolicyCorridor>("/api/policy"); }
    catch { return { current: null, history: [] }; }
  },
  macro:           async (): Promise<MacroSeries> => {
    try { return await get<MacroSeries>("/api/macro/reserves"); }
    catch { return { series: [], latest: null }; }
  },
  // Pre-computed in GitHub Actions, never in the API. Degrades to an empty run
  // until the weekly workflow has populated the tables.
  forecast:        async (): Promise<ForecastPayload> => {
    try { return await get<ForecastPayload>("/api/forecast"); }
    catch { return { run_date: null, forecasts: [], metrics: [], track_record: [] }; }
  },
  forecastPredictions: async (tenor: string): Promise<BacktestPredictionRow[]> => {
    try { return await get<BacktestPredictionRow[]>(`/api/forecast/predictions?tenor=${encodeURIComponent(tenor)}`); }
    catch { return []; }
  },
  forecastDiagnostics: async (): Promise<DiagnosticRow[]> => {
    try { return await get<DiagnosticRow[]>("/api/forecast/diagnostics"); }
    catch { return []; }
  },
  monetary:        async (): Promise<MonetaryData> => {
    try { return await get<MonetaryData>("/api/macro/monetary"); }
    catch { return { monthly: [], latest: null, reserve_requirements: { current: null, history: [] } }; }
  },
};

export type ForecastModel = "naive" | "momentum" | "ols" | "curve" | "ecm" | "blend";

/** One out-of-sample backtest prediction. A simulation, not the published log. */
export interface BacktestPredictionRow {
  auction_date: string; model: ForecastModel;
  actual_yield: number | null; pred_yield: number | null;
}
/** A Phase-4 statistical test result. */
export interface DiagnosticRow {
  tenor: string; test: string; subject: string;
  statistic: number | null; pvalue: number | null;
  lag: number | null; conclusion: string | null;
}

export interface ForecastRow {
  tenor: string; instrument: string | null; model: ForecastModel;
  target_auction_date: string | null;
  point_yield: number; lo_yield: number; hi_yield: number;
  last_actual_yield: number | null; last_auction_date: string | null;
}
/** Rolling out-of-sample skill. dir_acc is null for naive — it calls no direction. */
export interface BacktestRow {
  tenor: string; model: ForecastModel;
  mae_bps: number | null; rmse_bps: number | null;
  /** RMSE over the recent slice — what the published band is actually built from. */
  rmse_recent_bps: number | null;
  dir_acc: number | null; hit_5bps: number | null; n_obs: number;
  /** Diebold-Mariano vs naive, Harvey-Leybourne-Newbold small-sample corrected. */
  dm_pvalue: number | null;
  /** Bootstrap 95% CI for this model's own MAE, in bps. */
  mae_lo: number | null; mae_hi: number | null;
  /** Bootstrap 95% CI for (naive MAE − this model's MAE), bps. Contains 0 = no proven edge. */
  gap_lo: number | null; gap_hi: number | null;
  /** Share of actual prints that landed inside the published band (target 0.95). */
  coverage: number | null;
  /** First-half vs second-half MAE — mae_recent is what today actually looks like. */
  mae_first: number | null; mae_recent: number | null;
  verdict: "established" | "unproven" | "worse" | "benchmark" | "exploratory" | null;
  /** Survived Benjamini-Hochberg across the pre-specified primary family.
   *  null for exploratory models, which are never promoted. */
  dm_fdr_pass: boolean | null;
  /** Published band half-width in bps (EWMA volatility-scaled). */
  band_bps: number | null;
}
export interface TrackRecordRow {
  tenor: string; model: ForecastModel;
  target_auction_date: string; forecast_run_date: string;
  point_yield: number; lo_yield: number; hi_yield: number; actual_yield: number;
}
export interface ForecastPayload {
  run_date: string | null;
  forecasts: ForecastRow[];
  metrics: BacktestRow[];
  track_record: TrackRecordRow[];
}

export interface MonetaryRow {
  month: string;
  cpi_p2p: number | null; cpi_12mo_avg: number | null;
  m2_growth: number | null; reserve_money_growth: number | null; private_credit_growth: number | null;
  wavg_deposit: number | null; wavg_lending: number | null;
  note?: string;
}
export interface ReserveRequirement {
  effective_date: string; crr: number | null; slr: number | null; note?: string;
}
export interface MonetaryData {
  monthly: MonetaryRow[];
  latest: MonetaryRow | null;
  reserve_requirements: { current: ReserveRequirement | null; history: ReserveRequirement[] };
}

export interface MacroRow {
  month: string;
  gross_reserves_usd_bn: number | null;
  net_reserves_bpm6_usd_bn: number | null;
  remittance_usd_mn: number | null;
  note?: string;
}
export interface MacroSeries {
  series: MacroRow[];
  latest: MacroRow | null;
}

export interface PolicyRateSnapshot {
  effective_date: string;
  repo: number | null; slf: number | null; sdf: number | null;
  bank_rate: number | null; note?: string;
  crr?: number | null; slr?: number | null;
  source?: string; verified?: boolean; last_checked?: string | null;
  source_last_update?: string | null;
}
export interface PolicyCorridor {
  current: PolicyRateSnapshot | null;
  history: PolicyRateSnapshot[];
  verified?: boolean;
}

export interface DatasetStatus {
  label: string;
  ingested: string | null;
  latest_data: string | null;
  rows: number;
  current?: boolean;        // cadence-aware: up to date for what BB has published
  kind?: "daily" | "event"; // daily series vs auction/operation series
}
export interface DataHealth {
  ok: boolean | null;
  issue_count?: number;
  issues?: string[];
  by_table?: Record<string, number>;
}
export interface MetaStatus {
  datasets: Record<string, DatasetStatus>;
  last_run: string | null;
  last_run_errors: string[];
  data_health?: DataHealth | null;
  cadence: string;
  checked_recently?: boolean;
  as_of?: string;
  last_working_day?: string;
}

export type FreshnessKey =
  | "securities" | "yields" | "secondary" | "omo"
  | "fx" | "callmoney" | "refrate" | "flows";
export type Freshness = Record<FreshnessKey, string | null>;

const EMPTY_FRESHNESS: Freshness = {
  securities: null, yields: null, secondary: null, omo: null,
  fx: null, callmoney: null, refrate: null, flows: null,
};

export interface OmoSummaryRow {
  instrument: string; direction: string; tranches: number;
  outstanding_bdt_crore: number; next_maturity: string; last_maturity: string;
}
export interface OmoOutstandingRow {
  date: string; instrument: string; outstanding_bdt_crore: number;
  direction?: string;
}
export interface OmoTxnRow {
  transaction_date: string; maturity_date: string; instrument: string;
  tenor_label: string; accepted_bdt_crore: number; rate_pct: number | null;
  rate_range: string | null; direction: string;
}
export interface SecondaryYieldRow {
  isin: string; settlement_date: string; market_yield_pct: number;
  outstanding_bdt_mill: number; security_name_norm: string;
  security_type: string; maturity_date: string; remaining_years: number;
}

export interface YieldRow {
  tenor_label: string; tenor_years: number; security_type: string;
  cutoff_yield_pct: number; auction_date: string;
  offered_bdt_crore?: number; accepted_bdt_crore?: number;
}
export interface CurveSlopeRow {
  date: string;
  y_91d: number | null; y_2y: number | null; y_10y: number | null;
  two_ten: number | null; short_ten: number | null;
}
export interface Security {
  isin: string; security_name_norm: string; security_type: string;
  issue_date: string; maturity_date: string; coupon_rate_pct: number;
  /** "HALF_YEARLY" | "YEARLY" | "NONE" — /api/securities returns this; the
   *  type was missing it, which broke PortfolioTool's coupon-schedule logic. */
  coupon_frequency?: string | null;
  outstanding_bdt_mill: number;
}
export interface FlowRow {
  flow_date: string; coupon_inflow_bdt_mill: number; principal_inflow_bdt_mill: number;
  total_inflow_bdt_mill: number; auction_outflow_planned_mill: number;
  auction_outflow_confirmed_mill: number; net_borrowing_bdt_mill: number;
  coupon_payment_count: number; inflow_security_count: number; data_complete: boolean;
}

export interface LiquidityForecastDay {
  date: string; weekday: string;
  omo_return_crore: number; omo_repay_crore: number;
  govt_inflow_crore: number; auction_out_crore: number;
  net_crore: number; cum_net_crore: number;
  omo_items: { instrument: string; direction: string; crore: number }[];
  flows_confirmed: boolean;
}
export interface LiquidityForecast {
  as_of: string; days: LiquidityForecastDay[]; unit: string;
  auction_horizon?: string | null;
}

export interface CallMoneyDailySummary {
  trade_date: string;
  total_volume_crore: number;
  total_deals: number;
  overnight_volume_crore: number | null;
  overnight_deals: number | null;
  overnight_wavg_rate: number | null;
  overnight_high: number | null;
  overnight_low: number | null;
}
export interface CallMoneyBreakdownRow {
  trade_date: string; product: string; maturity_days: number | null;
  amount_crore: number; highest_rate_pct: number | null;
  lowest_rate_pct: number | null; average_rate_pct: number | null;
  num_deals: number | null;
}
export interface CallMoneyResult {
  daily_summary: CallMoneyDailySummary[];
  latest_breakdown: CallMoneyBreakdownRow[];
  latest_date: string | null;
}

export interface RefRateRow {
  trade_date: string; rate_type: string; product: string;
  amount_crore: number | null; rate_pct: number | null; num_deals: number | null;
}

export interface FxAuctionRow {
  auction_date: string; settlement_date: string | null; auction_type: string;
  num_bids: number | null; bid_amount_usd_mill: number | null;
  bid_range: string | null; num_accepted: number | null;
  accepted_amount_usd_mill: number | null;
  cutoff_rate: number | null; weighted_avg_rate: number | null;
}

export interface DrilldownResult {
  date: string;
  summary: {
    maturity_inflow_mill: number; coupon_inflow_mill: number;
    total_inflow_mill: number; auction_outflow_mill: number; net_borrowing_mill: number;
  };
  maturities: { isin: string; security_name_norm: string; security_type: string;
    payment_date: string; principal_bdt_mill: number; roll_days: number }[];
  coupons: { isin: string; security_name_norm: string; coupon_rate_used_pct: number;
    payment_date: string; amount_bdt_mill: number; formula_string: string }[];
  auctions: { auction_date: string; security_type: string; tenor_label: string;
    offered_amount_bdt_mill: number; accepted_amount_bdt_mill: number;
    weighted_avg_yield_pct: number; outflow_status: string; roll_days: number }[];
}
