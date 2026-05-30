const BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

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
  // Non-critical: must never break a page. Returns all-nulls if the endpoint is unavailable.
  freshness:       async (): Promise<Freshness> => {
    try { return await get<Freshness>("/api/meta/freshness"); }
    catch { return EMPTY_FRESHNESS; }
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
};

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
}
export interface PolicyCorridor {
  current: PolicyRateSnapshot | null;
  history: PolicyRateSnapshot[];
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
  tenor_label: string; accepted_bdt_crore: number; rate_pct: number | null; direction: string;
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
  outstanding_bdt_mill: number;
}
export interface FlowRow {
  flow_date: string; coupon_inflow_bdt_mill: number; principal_inflow_bdt_mill: number;
  total_inflow_bdt_mill: number; auction_outflow_planned_mill: number;
  auction_outflow_confirmed_mill: number; net_borrowing_bdt_mill: number;
  coupon_payment_count: number; inflow_security_count: number; data_complete: boolean;
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
