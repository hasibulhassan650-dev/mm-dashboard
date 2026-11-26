// Where every number on the dashboard comes from.
//
// Data accuracy outranks everything on this desk, so NO URL here is typed from
// memory: each href is copied from a constant the fetcher actually reads
// (config.py, fetchers/*.py). tests/test_source_links.py fails the build if an
// href stops appearing in the fetchers, if a dataset has no entry, or if a nav
// page neither shows a source nor says why it does not.
//
// Keys are the dataset keys in engines/expectations.py (DATASETS) plus "news".
// A DERIVED dataset (computed by us) has no URL: it names what it derives from.

export interface SourceUrl { label: string; href: string }

export interface Source {
  label: string;
  urls: SourceUrl[];
  /** Supabase table the page reads (matches DATASETS.table; "" when not a table). */
  table: string;
  /** One line: who publishes it, and any caveat a reader must know. */
  cite: string;
  /** Keys of the sources this one is computed from (derived datasets only). */
  derivedFrom?: string[];
}

const BB = "https://www.bb.org.bd/en/index.php/";
const GSOM = "https://gsom.bb.org.bd/index.php/";

export const SOURCES: Record<string, Source> = {
  omo: {
    label: "OMO operations",
    urls: [{ label: "BB press releases", href: BB + "mediaroom/press_release" }],
    table: "omo_transactions",
    cite: "Bangladesh Bank press release, one per working day",
  },
  callmoney: {
    label: "Call money",
    urls: [{ label: "BB call money market", href: BB + "monetaryactivity/call_money_market" }],
    table: "call_money_rates",
    cite: "Bangladesh Bank call money page",
  },
  fxmarket: {
    label: "Interbank FX",
    urls: [{ label: "BB interbank FX market", href: BB + "fxmarket/interbank_fxmarket" }],
    table: "interbank_fx",
    cite: "Bangladesh Bank interbank FX market page (USD only)",
  },
  repo: {
    label: "Interbank repo",
    urls: [{ label: "BB interbank repo", href: BB + "financialactivity/interbankrepo" }],
    table: "interbank_repo",
    cite: "Bangladesh Bank interbank repo page",
  },
  fxrates: {
    label: "FX rates",
    urls: [{ label: "BB exchange rates", href: BB + "econdata/exchangerate" }],
    table: "fx_rates_daily",
    cite: "Bangladesh Bank exchange rate page; it describes the previous business day",
  },
  secondary: {
    label: "Secondary market (GSOM)",
    urls: [
      { label: "GSOM T-Bond", href: GSOM + "tbond" },
      { label: "GSOM FRTB", href: GSOM + "frtb" },
      { label: "GSOM T-Bill", href: GSOM + "tbill" },
    ],
    table: "mtm_snapshots",
    cite: "Bangladesh Bank GSOM",
  },
  refrate: {
    label: "Reference rates (BOFR/DOMMR)",
    urls: [{ label: "BB reference rates", href: BB + "monetaryactivity/money_market_ref_rate" }],
    table: "ref_rates",
    cite: "Bangladesh Bank money-market reference rate page",
  },
  yields: {
    label: "Primary auction results",
    urls: [{ label: "BB treasury auction results", href: BB + "monetaryactivity/treasury" }],
    table: "primary_yield_snapshots",
    cite: "Bangladesh Bank treasury auction results (cut-off yield)",
  },
  securities: {
    label: "Securities master",
    urls: [
      { label: "GSOM T-Bond", href: GSOM + "tbond" },
      { label: "GSOM FRTB", href: GSOM + "frtb" },
      { label: "GSOM T-Bill", href: GSOM + "tbill" },
    ],
    table: "securities",
    cite: "Bangladesh Bank GSOM",
  },
  reserves: {
    label: "FX reserves",
    urls: [{ label: "BB international reserves", href: BB + "econdata/intreserve" }],
    table: "reserves_monthly",
    cite: "Bangladesh Bank economic data, monthly",
  },
  remittance: {
    label: "Remittance",
    urls: [{ label: "BB wage remittance", href: BB + "econdata/wageremitance" }],
    table: "remittance_monthly",
    cite: "Bangladesh Bank economic data, monthly",
  },
  monetary: {
    label: "Monetary aggregates",
    urls: [
      { label: "BB inflation", href: BB + "econdata/inflation" },
      { label: "BB interest rates", href: BB + "econdata/intrate" },
      { label: "BB monetary survey", href: BB + "econdata/monetarysurvey" },
    ],
    table: "monetary_monthly",
    cite: "Bangladesh Bank economic data, monthly; a figure with no source is left blank",
  },
  auctions_forward: {
    label: "Auction calendar",
    urls: [{ label: "BB auction calendar", href: BB + "monetaryactivity/auc_calendar/1" }],
    table: "auction_events",
    cite: "Bangladesh Bank auction calendar; auctions stop where BB's calendar stops",
  },
  fxauction: {
    label: "BB FX auctions",
    urls: [{ label: "BB FX auction results", href: BB + "fxmarket/fx_auction_result" }],
    table: "fx_auction_results",
    cite: "Bangladesh Bank FX auction results; BB intervenes irregularly",
  },
  policy: {
    label: "Policy corridor",
    urls: [{ label: "BB homepage", href: BB + "home" }],
    table: "policy_rate_snapshots",
    cite: "Bangladesh Bank homepage; changes only when the MPC moves",
  },
  press: {
    label: "BB press releases",
    urls: [{ label: "BB press releases", href: BB + "mediaroom/press_release" }],
    table: "press_releases",
    cite: "Bangladesh Bank press-release listing",
  },
  bgiib: {
    label: "Islamic window (BGIIB)",
    urls: [{ label: "BB press releases", href: BB + "mediaroom/press_release" }],
    table: "bgiib_auctions",
    cite: "Bangladesh Bank press release of each weekly BGIIB auction",
  },
  sukuk: {
    label: "BGIS Sukuk outstanding",
    urls: [
      { label: "BB sukuk outstanding", href: BB + "monetaryactivity/sukukoutstanding/1" },
      { label: "BB sukuk auction notices", href: BB + "monetaryactivity/auc_notice_sukuk" },
      { label: "BB sukuk prospectuses", href: BB + "monetaryactivity/prospectus_sukuk" },
    ],
    table: "sukuk_outstanding",
    cite: "Bangladesh Bank sukuk pages",
  },
  buybacks: {
    label: "Bond buybacks",
    urls: [{ label: "BB press releases", href: BB + "mediaroom/press_release" }],
    table: "buyback_events",
    cite: "Bangladesh Bank press release (LMO); the accepted figure is face value, cash is not published",
  },
  holidays: {
    label: "Holiday calendar",
    urls: [{ label: "BB holiday calendar", href: BB + "mediaroom/holiday" }],
    table: "holiday_calendar",
    cite: "Bangladesh Bank annual gazette; closures also arrive by circular",
  },
  flows: {
    label: "Cash-flow ladder",
    urls: [],
    table: "daily_net_flow",
    cite: "computed here; news events are shown beside it, never summed into it",
    derivedFrom: ["coupons", "maturities", "auctions_forward", "buybacks", "sukuk"],
  },
  coupons: {
    label: "Coupon schedule",
    urls: [],
    table: "coupon_events",
    cite: "computed here from each bond's terms",
    derivedFrom: ["securities", "yields"],
  },
  maturities: {
    label: "Maturity schedule",
    urls: [],
    table: "maturity_events",
    cite: "computed here from each security's maturity date",
    derivedFrom: ["securities", "yields"],
  },
  news: {
    label: "News-derived events",
    urls: [],
    table: "",
    cite: "press reports of liquidity events; see each event's own link. Shown, never summed into the ladder",
  },
};

// Which sources each nav page shows. Every nav href must appear here or in
// PAGE_SOURCES_EXEMPT (tests/test_source_links.py). /drilldown is not in the
// nav (it needs ?date=) but shows sources all the same.
export const PAGE_SOURCES: Record<string, string[]> = {
  "/omo": ["omo"],
  "/callmoney": ["callmoney", "policy"],
  "/fxmarket": ["fxmarket", "fxrates"],
  "/fx": ["fxauction"],
  "/repo": ["repo"],
  "/refrate": ["refrate", "policy"],
  "/yields": ["yields", "secondary"],
  "/auctions": ["auctions_forward", "yields"],
  "/auction-data": ["yields", "auctions_forward"],
  "/securities": ["securities"],
  "/macro": ["reserves", "remittance"],
  "/monetary": ["monetary"],
  "/schedule": ["coupons", "maturities", "auctions_forward"],
  "/cashflows": ["flows"],
  "/forecast": ["flows", "callmoney", "policy"],
  "/drilldown": ["flows", "omo", "buybacks", "news"],
  "/islamic": ["bgiib", "sukuk"],
  "/press": ["press"],
  "/liquidity-watch": ["news"],
};

// Pages with no source line, each with the reason.
export const PAGE_SOURCES_EXEMPT: Record<string, string> = {
  "/": "overview: a summary of the other pages, each of which shows its own source",
  "/guardian": "data-integrity monitor: it reports on the sources, it is not a data page",
  "/export": "downloads of the other pages' data, which carry their sources",
  "/desk": "desk entries are typed in by the desk, never Bangladesh Bank data",
  "/portfolio": "the desk's own holdings, entered by the desk",
  "/glossary": "definitions of terms only, no data is shown",
  "/explore": "free exploration over many datasets; a single source line would mislead",
  "/research": "free-form research over many datasets; a single source line would mislead",
};
