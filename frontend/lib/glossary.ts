// Money-market terminology used across the dashboard. Keyed by the term/acronym.
export interface GlossaryEntry {
  term: string;
  full?: string;       // full expansion of an acronym
  def: string;         // plain-language definition
  category: "Rates" | "Instruments" | "Operations" | "Securities" | "Metrics" | "FX" | "Macro";
}

export const GLOSSARY: GlossaryEntry[] = [
  {
    term: "DOMMR", category: "Rates",
    def: "A Bangladesh Bank money-market reference rate published on the BB reference-rate page, quoted by product (Overnight, 1W, 1M, 3M). Used here as a benchmark for short-term funding.",
  },
  {
    term: "BOFR", category: "Rates",
    def: "A Bangladesh Bank money-market reference rate published alongside DOMMR, quoted by product. Used here as a benchmark for short-term funding.",
  },
  {
    term: "Call Money", category: "Rates",
    def: "Uncollateralised interbank lending, mostly overnight. The overnight call rate is the headline gauge of day-to-day banking-system liquidity.",
  },
  {
    term: "Repo / CB_REPO", full: "Repurchase agreement", category: "Operations",
    def: "BB lends cash to banks against securities for a set tenor (liquidity injection). CB_REPO is the central-bank repo line in the OMO data.",
  },
  {
    term: "SLF", full: "Standing Lending Facility", category: "Operations",
    def: "A standing facility through which banks borrow overnight from BB at a rate that acts as the ceiling of the interest-rate corridor.",
  },
  {
    term: "SDF", full: "Standing Deposit Facility", category: "Operations",
    def: "A standing facility through which banks park surplus funds at BB; its rate acts as the floor of the interest-rate corridor.",
  },
  {
    term: "IBLF", full: "Islamic Banks Liquidity Facility", category: "Operations",
    def: "A BB liquidity facility for Shariah-compliant banks, appearing as an OMO instrument.",
  },
  {
    term: "AR", full: "Assured Repo / Liquidity support", category: "Operations",
    def: "A term liquidity-support repo instrument in the OMO data, typically longer-dated (e.g. 180D).",
  },
  {
    term: "OMO", full: "Open Market Operations", category: "Operations",
    def: "BB's buying/selling of securities and repo operations to manage banking-system liquidity. Net injection = cash added; net absorption = cash withdrawn.",
  },
  {
    term: "GSOM", full: "Government Securities — Open Market (MTM page)", category: "Securities",
    def: "The BB portal publishing mark-to-market (MTM) valuations and yields for outstanding government securities (the source of the secondary-market curve).",
  },
  {
    term: "MTM", full: "Mark to Market", category: "Metrics",
    def: "Revaluing a security at its current market price/yield rather than its book value.",
  },
  {
    term: "T-Bill", full: "Treasury Bill", category: "Securities",
    def: "Short-term (≤1 year) zero-coupon government debt, issued at a discount.",
  },
  {
    term: "T-Bond", full: "Treasury Bond", category: "Securities",
    def: "Longer-term (2–20 year) coupon-bearing government debt.",
  },
  {
    term: "FRTB", full: "Floating Rate Treasury Bond", category: "Securities",
    def: "A treasury bond whose coupon resets periodically against a reference rate rather than being fixed.",
  },
  {
    term: "Cutoff yield", category: "Metrics",
    def: "The highest accepted yield (lowest accepted price) at a primary auction — the marginal clearing level.",
  },
  {
    term: "Weighted-avg yield", category: "Metrics",
    def: "The average accepted yield at an auction weighted by accepted amount — typically a touch below the cutoff.",
  },
  {
    term: "Bid-to-cover", category: "Metrics",
    def: "Total amount bid ÷ amount accepted at an auction. Higher = stronger demand. Below ~1.0 implies the issue was under-subscribed.",
  },
  {
    term: "Devolvement", category: "Metrics",
    def: "When bids fall short of the amount on offer and primary dealers are obliged to absorb the shortfall. A sign of weak auction demand or rich pricing.",
  },
  {
    term: "Yield-curve slope (2s10s)", category: "Metrics",
    def: "The 10-year yield minus the 2-year yield. Positive (steep) = normal; negative (inverted) can signal tightening/stress expectations.",
  },
  {
    term: "PV01 / DV01", category: "Metrics",
    def: "The change in a position's value for a 1 basis-point move in yield — the standard measure of interest-rate sensitivity.",
  },
  {
    term: "Crore (cr)", category: "Metrics",
    def: "South-Asian unit = 10 million (10,000,000). 1 crore BDT = 10 mn BDT. Amounts on the OMO/call-money pages are in crore.",
  },
  {
    term: "Cutoff rate (FX)", category: "FX",
    def: "The marginal accepted USD/BDT rate at a BB FX intervention auction.",
  },
  {
    term: "Gross reserves", category: "FX",
    def: "Total official foreign-currency reserves held by Bangladesh Bank, before deducting short-term liabilities. The headline reserves figure.",
  },
  {
    term: "Net reserves (BPM6)", category: "FX",
    def: "Reserves measured on the IMF Balance of Payments Manual 6th edition basis — gross reserves less specified short-term FX liabilities (e.g. ACU, swaps). Lower than gross; the figure the IMF programme tracks.",
  },
  {
    term: "Remittance", category: "FX",
    def: "Wage-earner remittance inflows — money sent home by Bangladeshis working abroad. A primary source of FX supply and a key external-sector indicator.",
  },
  {
    term: "Weighted-avg rate (FX)", category: "FX",
    def: "The accepted-amount-weighted average USD/BDT rate at an FX auction.",
  },
  {
    term: "CPI (point-to-point)", category: "Macro",
    def: "Consumer Price Index inflation measured as the % change versus the same month a year earlier (year-on-year). The headline monthly inflation print from BBS. The 12-month average smooths it over the trailing year.",
  },
  {
    term: "M2 / Broad money", category: "Macro",
    def: "Broad money supply — currency in circulation plus demand and time deposits. Its YoY growth gauges monetary expansion.",
  },
  {
    term: "Reserve money", category: "Macro",
    def: "Base/high-powered money — currency plus banks' reserves at the central bank. The monetary base BB controls most directly.",
  },
  {
    term: "Private-sector credit", category: "Macro",
    def: "Bank lending to the private sector. Its YoY growth is a key gauge of credit conditions and a BB monetary-policy target.",
  },
  {
    term: "Lending-deposit spread", category: "Macro",
    def: "Weighted-average lending rate minus weighted-average deposit rate across the banking system — a measure of bank intermediation margin and cost-of-funds pressure.",
  },
  {
    term: "CRR", full: "Cash Reserve Ratio", category: "Macro",
    def: "The share of deposits banks must hold as cash reserves with Bangladesh Bank. A reserve-requirement tool; conventional banks differ from Islamic banks.",
  },
  {
    term: "SLR", full: "Statutory Liquidity Ratio", category: "Macro",
    def: "The share of deposits banks must hold in liquid assets (cash, gold, approved securities) including the CRR. Higher SLR drains lendable funds.",
  },
];
