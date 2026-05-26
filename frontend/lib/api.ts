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
  yields:          (months = 12) => get<YieldRow[]>("/api/yields", { months }),
  securities:      () => get<Security[]>("/api/securities"),
  flows:           (months = 6) => get<FlowRow[]>("/api/flows", { months }),
};

export interface OmoSummaryRow {
  instrument: string; direction: string; tranches: number;
  outstanding_bdt_crore: number; next_maturity: string; last_maturity: string;
}
export interface OmoOutstandingRow {
  date: string; instrument: string; outstanding_bdt_crore: number;
}
export interface OmoTxnRow {
  transaction_date: string; maturity_date: string; instrument: string;
  tenor_label: string; accepted_bdt_crore: number; rate_pct: number | null; direction: string;
}
export interface YieldRow {
  tenor_label: string; tenor_years: number; security_type: string;
  cutoff_yield_pct: number; auction_date: string;
  offered_bdt_crore?: number; accepted_bdt_crore?: number;
}
export interface Security {
  isin: string; security_name_norm: string; security_type: string;
  issue_date: string; maturity_date: string; coupon_rate_pct: number;
  outstanding_bdt_mill: number;
}
export interface FlowRow {
  flow_date: string; coupon_inflow_bdt_mill: number; principal_inflow_bdt_mill: number;
  total_inflow_bdt_mill: number; auction_outflow_planned_mill: number;
  net_borrowing_bdt_mill: number;
}
