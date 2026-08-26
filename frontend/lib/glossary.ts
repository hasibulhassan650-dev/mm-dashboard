// Money-market terminology used across the dashboard. Keyed by the term/acronym.
export interface GlossaryEntry {
  term: string;
  full?: string;       // full expansion of an acronym
  def: string;         // plain-language definition
  /** Same idea explained for someone with no finance or stats background —
   *  the "Explain simply" toggle swaps every definition and tooltip to this. */
  eli10?: string;
  category: "Rates" | "Instruments" | "Operations" | "Securities" | "Metrics"
          | "FX" | "Macro" | "Forecasting";
}

/** Look up an entry by term, case-insensitively. */
export function lookup(term: string): GlossaryEntry | undefined {
  const k = term.toLowerCase();
  return GLOSSARY.find(e => e.term.toLowerCase() === k);
}

/** URL-safe anchor for a term, used by /glossary and every inline link. */
export function slug(term: string): string {
  return term.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
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
    def: "A term liquidity-support repo instrument in the OMO data, typically longer-dated (e.g. 180D). Injects liquidity.",
  },
  {
    term: "CM_REPO", full: "Capital Market Repo", category: "Operations",
    def: "A special repo line through which BB supplies liquidity to banks to support their capital-market (stock-market) exposures. Injects liquidity.",
  },
  {
    term: "MLS", full: "Mudaraba Liquidity Support", category: "Operations",
    def: "A Shariah-compliant liquidity injection for Islamic banks, structured on the Mudaraba profit-sharing basis rather than interest. Injects liquidity.",
  },
  {
    term: "SLS", full: "Special Liquidity Support", category: "Operations",
    def: "An ad-hoc special liquidity-support line BB extends to banks needing funds beyond the standard standing facilities. Injects liquidity.",
  },
  {
    term: "SRF", full: "Special Repo Facility", category: "Operations",
    def: "A special repo facility that supplies liquidity against securities outside the regular central-bank repo, typically on bespoke terms. Injects liquidity.",
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
    def: "The highest accepted yield (lowest accepted price) at a primary auction — the marginal clearing level. This is the number the /research models forecast.",
    eli10: "An auction where banks say what interest rate they want. The government takes the cheapest offers and stops once it has borrowed enough. The rate where it stops is the cutoff — that's the number we try to guess before it happens.",
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
    def: "When bids fall short of the amount on offer and primary dealers are obliged to absorb the shortfall. A sign of weak auction demand or rich pricing — and the resulting cutoff may be administratively set rather than market-determined.",
    eli10: "When not enough people bid at the auction, so the organisers have to buy the leftovers themselves. The price that comes out then isn't really a market price.",
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

  // ── Forecasting & statistics (the /research tab) ──────────────────────────
  {
    term: "Tenor", category: "Forecasting",
    def: "How long the security lasts before it is repaid — 91D means 91 days, 10Y means 10 years. Each tenor is forecast separately because they behave differently.",
    eli10: "How long you're lending the money for. 91 days is short, 10 years is long. Short and long loans behave differently, so we treat them separately.",
  },
  {
    term: "Basis Point", full: "bps", category: "Forecasting",
    def: "One hundredth of a percentage point. A move from 9.30% to 9.38% is 8 basis points. All forecast errors here are quoted in bps so tenors are comparable.",
    eli10: "A tiny slice of a percent — one hundredth of one. If a rate goes from 9.30% to 9.38%, that's 8 of these tiny slices. It's just a way to talk about small changes without lots of zeros.",
  },
  {
    term: "Naive Benchmark", category: "Forecasting",
    def: "The forecast that says the next cutoff equals the last one. It is the benchmark every model must beat, because if a rate is close to a random walk this is the theoretically correct forecast.",
    eli10: "The lazy guess: \"tomorrow will be the same as today.\" It sounds silly, but it's surprisingly hard to beat — so we make every clever idea prove it does better than the lazy guess.",
  },
  {
    term: "Momentum", category: "Forecasting",
    def: "A model that assumes part of the last change carries into the next: forecast = last cutoff + β × last change.",
    eli10: "If the rate went up last time, maybe it keeps drifting up a bit. This model follows the recent trend a little way.",
  },
  {
    term: "Curve Carry", category: "Forecasting",
    def: "The model that moves a bond's forecast by λ × how far the weekly bill curve has travelled since that bond last auctioned. It works because bonds auction ~monthly while bills auction weekly, so the previous bond print is stale.",
    eli10: "Long loans are auctioned about once a month, but short loans are auctioned every week. So the short ones tell us what the market has been doing lately. This model just says: whatever the weekly ones did, the monthly one probably did something similar.",
  },
  {
    term: "Blend", category: "Forecasting",
    def: "The plain average of the other models. Averaging forecasts is durable because independent errors partly cancel, so the blend usually lands near the best single model without needing to know which that is in advance.",
    eli10: "Instead of trusting one guesser, ask several and take the middle. When one is too high and another too low, the mistakes cancel out.",
  },
  {
    term: "Pass-through", full: "λ (lambda)", category: "Forecasting",
    def: "How much of a move in the bill curve carries into a given tenor. Fitted, not assumed: ~0.97–1.13 for 2Y–15Y (near-parallel shifts), 0.78 at 20Y, ~0.40 for 91D/182D.",
    eli10: "If short-term rates move 10 steps, how many steps does this one move? About 10 for medium loans, fewer for the very longest ones. We measured it rather than guessing.",
  },
  {
    term: "MAE", full: "Mean Absolute Error", category: "Forecasting",
    def: "The average size of the forecast miss, ignoring direction, in bps. The headline accuracy measure — lower is better.",
    eli10: "On average, how far off were we? If you guess someone's height and you're usually wrong by 5cm, your MAE is 5cm. Smaller means better guessing.",
  },
  {
    term: "RMSE", full: "Root Mean Squared Error", category: "Forecasting",
    def: "Like MAE but squares the errors first, so occasional large misses are punished harder. The published band is built from this.",
    eli10: "Like the average miss, but big mistakes count extra. It's the number we use to draw the 'how sure are we' range.",
  },
  {
    term: "Out-of-sample", category: "Forecasting",
    def: "Tested on auctions the model never saw while it was being fitted. The only honest way to measure a forecast — scoring a model on data it learned from always flatters it.",
    eli10: "You have to take the test on questions you didn't get to study. If you check your answers against the ones you already peeked at, of course you look smart.",
  },
  {
    term: "Backtest", category: "Forecasting",
    def: "Replaying history week by week: fit on everything up to auction t, predict t, score it, roll forward. Nothing from the future ever touches a fit.",
    eli10: "Pretending we're back in the past and guessing what happens next, over and over, so we can count how often we were right — without cheating by peeking ahead.",
  },
  {
    term: "Directional Accuracy", category: "Forecasting",
    def: "The share of auctions where the model called up-versus-down correctly. Often more useful to a bidder than MAE, because it says which side of the last print to lean.",
    eli10: "Forget how close we were — did we at least say 'up' when it went up? Sometimes just knowing the direction is enough to be useful.",
  },
  {
    term: "Confidence Interval", category: "Forecasting",
    def: "A range of plausible values for a number we estimated, not a certainty. Here: if the range for a model's improvement includes zero, the model might genuinely be no better at all.",
    eli10: "Instead of one answer, a range: \"somewhere between here and here.\" If that range includes zero, it means the thing might not actually help at all — we can't tell yet.",
  },
  {
    term: "Bootstrap", category: "Forecasting",
    def: "Re-computing a result on 2,000 resampled versions of the same history to see how much it wobbles. Done in blocks here, because auction errors are correlated week to week and resampling singly would understate the uncertainty.",
    eli10: "Shuffle your past results thousands of times and redo the maths each time. If the answer keeps changing wildly, it wasn't a solid answer to begin with.",
  },
  {
    term: "p-value", category: "Forecasting",
    def: "The chance of seeing a result this good if the model actually had no edge. Below 0.05 is the usual bar for calling an edge real; above ~0.1 means it could easily be luck.",
    eli10: "The chance you got this lucky by accident. A small number means \"probably not luck.\" A big number means \"this could easily just be chance.\"",
  },
  {
    term: "Diebold-Mariano", full: "DM test", category: "Forecasting",
    def: "A statistical test of whether one forecast is genuinely more accurate than another, rather than better by chance. Corrected here for small samples (Harvey-Leybourne-Newbold), which matters on ~20 bond auctions.",
    eli10: "A fair way to check whether one guesser is really better than another, or just had a good run. We use the version that's careful when you only have a few results.",
  },
  {
    term: "Selection Bias", category: "Forecasting",
    def: "The error of choosing the best-looking model after seeing the results. With many models and many tenors, something always wins by luck, and reporting that winner flatters it. Avoided here by a rule fixed in advance.",
    eli10: "If you run 20 races and only tell people about the one you won, you look like a great runner. Picking the winner afterwards isn't proof — so we decide the rules before looking.",
  },
  {
    term: "Coverage", category: "Forecasting",
    def: "The share of actual outcomes that landed inside the published 95% band. Should be about 95%; measured here at 90–95%, so the band is roughly honest.",
    eli10: "We say \"we're 95% sure it lands in this range.\" Coverage checks whether that was true. If it only lands there half the time, the range was a lie.",
  },
  {
    term: "Granger Causality", category: "Forecasting",
    def: "A test of whether knowing X's past improves the prediction of Y beyond Y's own past. It is about predictive usefulness, NOT real-world causation.",
    eli10: "Does knowing what one thing did yesterday help you guess what another thing does tomorrow? Note: this does not prove one causes the other — just that it helps you guess.",
  },
  {
    term: "Stationarity", full: "ADF / KPSS tests", category: "Forecasting",
    def: "Whether a series has a stable level it returns to, or wanders indefinitely. ADF and KPSS have opposite null hypotheses by design, so agreement between them is strong evidence.",
    eli10: "Does this number bounce around a home base, or does it just drift off forever? Two different tests check this, and we trust it most when both agree.",
  },
  {
    term: "Cointegration", category: "Forecasting",
    def: "Two series that wander individually but stay tethered to each other over time. If cutoffs and the policy rate are cointegrated, an error-correction model is the right specification.",
    eli10: "Two things that each wander around, but never get too far apart — like a dog on a long leash. The dog roams, but always comes back toward the person.",
  },
  {
    term: "ECM", full: "Error-Correction Model", category: "Forecasting",
    def: "A model where a rate sitting far from its anchor is pulled back toward it, at a speed the data estimates. Built here but switched off until verified policy-corridor history exists.",
    eli10: "The dog-on-a-leash idea turned into a prediction: if it has wandered too far, expect it to come back. We built this but turned it off, because we don't have trustworthy enough history yet.",
  },
  {
    term: "OLS", full: "Ordinary Least Squares", category: "Forecasting",
    def: "Standard linear regression — fits a straight-line relationship between a target and several drivers by minimising squared error.",
    eli10: "Drawing the best straight line through a cloud of dots, so you can use it to guess new points.",
  },
  {
    term: "Lookback Window", category: "Forecasting",
    def: "How much history a model is fitted and judged on. Split here: bills use 3 years (they auction weekly), bonds 5 years (they auction monthly and need more examples), while the band always uses only recent data.",
    eli10: "How far back we look when learning. Look back too little and you don't have enough examples; look back too far and you're learning from a world that no longer exists.",
  },
];
