/**
 * Full concept explanations, opened by clicking a term on the research tab.
 *
 * These are deliberately NOT dictionary definitions — the glossary already has
 * those. Each one is written to teach the whole idea to someone bright who has
 * never studied statistics: what it is, why anyone invented it, how it actually
 * works on a worked example using this dashboard's real numbers, how to read it
 * here, and the mistake people usually make.
 *
 * Worked examples use figures measured on 2026-08-11. They are illustrations of
 * the method, not live values — the tables above are always the live numbers.
 */
export interface Concept {
  /** Matches GlossaryEntry.term, case-insensitively. */
  term: string;
  oneLine: string;
  /** Everyday analogy — no finance, no maths. */
  intuition: string;
  /** How it really works, with a worked example. Paragraphs. */
  mechanics: string[];
  /** What it means specifically on the research tab. */
  onThisPage: string;
  /** The common misunderstanding. */
  pitfall: string;
}

export const CONCEPTS: Concept[] = [
  {
    term: "Naive Benchmark",
    oneLine: "The forecast that says the next auction rate equals the last one — the bar every model must clear.",
    intuition:
      "Imagine forecasting tomorrow's temperature by saying 'the same as today'. It sounds like no forecast at all, yet it is right far more often than most elaborate methods, because weather does not lurch about at random from one day to the next. Interest rates behave the same way.",
    mechanics: [
      "There is a deep reason this is hard to beat. An auction rate is set by banks bidding against each other, all reading the same news. If everybody could see that next week's rate would be higher, they would bid differently today — and the rate would move today. So by the time an auction happens, everything predictable is already priced in. What is left to move the rate is genuinely new information, and new information is, by definition, unpredictable.",
      "A series that only moves on unpredictable news is called a random walk. For a pure random walk, 'the last value' is mathematically the best possible forecast. Nothing can beat it. So when a model does beat it, that model is making a real claim: it has found information the market had not yet absorbed.",
      "Worked example. On the 91-day bill, naive is wrong by about 9.7 basis points on average across the whole history, and lands within 5 bps of the true rate about half the time. That is the score to beat. A model that gets 9.5 has not really achieved anything; a model that gets 5.0 has found something.",
    ],
    onThisPage:
      "Every model in the scoreboard is measured against naive, and the 'Better than naive by' column is literally naive's error minus that model's error. Where nothing beats it, the tab says so and recommends bidding off the last print — which is the case for all three bill tenors.",
    pitfall:
      "Treating naive as a strawman. It is the opposite: it is the hardest honest benchmark there is for a market-set rate, and most published forecasting models in finance quietly fail to beat it.",
  },
  {
    term: "Curve Carry",
    oneLine: "Move a bond's forecast by however far the weekly bill curve has travelled since that bond last auctioned.",
    intuition:
      "Suppose you only weigh yourself on the first of every month, but you weigh your identical twin every week. If your twin has lost 3 kg since the last time you stepped on the scale, the smart guess for your own weight is not 'the same as last month' — it is 'last month, minus about 3 kg'. You have fresher information about someone who moves the way you move.",
    mechanics: [
      "Treasury bills auction every week. Treasury bonds auction roughly once a month. So on the day a 10-year bond comes to auction, its own previous print is a month old — but the 91-day, 182-day and 364-day bills have printed four more times since then, and the whole rate environment may have shifted.",
      "The naive forecast ignores all of that. It is not failing because bond rates are unpredictable; it is failing because it is deliberately using out-of-date information when fresher, public, free information exists.",
      "So the model measures how far the 364-day bill has moved between this tenor's last auction and now, multiplies that by a factor called the pass-through (λ), and shifts the last bond cutoff by that amount. Forecast = last cutoff + λ × (move in the bill anchor).",
      "Worked example. The 5-year bond last cleared at 9.68%. Since that auction, the 364-day bill fell 24 bps. The fitted pass-through for the 5-year is about 1.13, so the model predicts 9.68% − (1.13 × 0.24) ≈ 9.41%. Naive would still be sitting at 9.68%, ignoring the move entirely.",
      "The result: on 2Y, 5Y and 10Y this roughly halves the average error versus naive — 5Y goes from about 55 bps to about 22 — and it calls the direction correctly around 95% of the time.",
    ],
    onThisPage:
      "Curve carry is the recommended model for every bond tenor where its edge is statistically established. It is switched off for the 364-day bill itself, because there the anchor would just be the tenor's own history — it cannot carry itself.",
    pitfall:
      "Assuming it must work because the story is appealing. The story was only the hypothesis; what makes it usable is that the improvement survived a bootstrap confidence interval and a small-sample significance test, and that the fitted pass-through came out where curve theory says it should.",
  },
  {
    term: "Pass-through",
    oneLine: "How much of a move in short-term bill rates carries through to a given tenor — measured, not assumed.",
    intuition:
      "When the tide comes in, boats in the harbour all rise — but not by exactly the same amount. Some are in shallow water and follow the tide almost exactly; some are moored where the water moves less. The pass-through is 'how much does this particular boat rise when the tide rises by one metre'.",
    mechanics: [
      "The model fits λ by looking at history: across all past auctions of this tenor, when the bill anchor moved by some amount, how much did this tenor's cutoff move? The line through those points, forced through the origin, gives λ. Forced through the origin matters — it means that if the anchor has not moved, the model predicts no move, which is the correct behaviour when nothing has happened.",
      "The fitted values came out at roughly 0.97 to 1.13 for the 2Y through 15Y — very close to one-for-one, meaning the Bangladeshi curve tends to shift almost in parallel. About 0.78 at 20Y, where the long end is anchored by duration demand and moves less. And about 0.40 for the 91-day and 182-day bills, because those print on the same day as the anchor, so much of the move is already shared rather than carried.",
      "This pattern is the single strongest reason to trust the model. Nobody told it that long bonds should be less sensitive than medium ones, or that same-day bills should show partial pass-through. It discovered numbers that match how a yield curve is supposed to behave.",
    ],
    onThisPage:
      "λ is logged on every weekly run. A tenor whose λ drifts a long way from these levels is telling you the relationship between that tenor and the bill curve is changing — which is itself worth knowing, and a reason to distrust that tenor's forecast until it settles.",
    pitfall:
      "Reading λ as a law of nature. It is an estimate from a finite sample and it can move. That is why it is monitored rather than hard-coded.",
  },
  {
    term: "MAE",
    oneLine: "The average size of the forecast's miss, ignoring whether it missed high or low.",
    intuition:
      "If you guess five people's heights and you are off by 3, 1, 4, 2 and 5 centimetres, your average miss is 3 cm. That is the MAE. It answers the plain question 'how wrong am I usually?' — and nothing more clever than that.",
    mechanics: [
      "Take every forecast, subtract the actual outcome, throw away the minus signs, and average. The minus signs are discarded on purpose: a forecast that is 10 bps too high and one that is 10 bps too low are equally wrong, and if you kept the signs they would cancel and make a terrible forecaster look perfect.",
      "Everything here is quoted in basis points so tenors can be compared. A 91-day bill and a 20-year bond live at different rate levels, but 'wrong by 12 bps' means the same thing for both.",
      "Worked example. On the 5-year bond, naive's MAE is about 55 bps and curve carry's is about 22 bps. So switching model cuts the typical miss by roughly 33 bps — that is the practical size of the improvement, in the units you actually bid in.",
    ],
    onThisPage:
      "The 'Typical miss' column. It is shown as 'early → recent' because the market got choppier: on the 91-day bill the miss was about 5 bps in the earlier period and about 14 bps recently. The recent figure is the one to plan against.",
    pitfall:
      "Comparing MAE across tenors and concluding the bill models are 'better' than the bond models. A 9 bps miss on a bill and a 24 bps miss on a bond are not the same achievement — bond rates simply move much more. Always compare a model to naive on the same tenor, never to a different tenor.",
  },
  {
    term: "RMSE",
    oneLine: "Like MAE, but big misses are punished much harder — and it is what the published band is built from.",
    intuition:
      "Two bus routes both make you an average of 5 minutes late. One is always 5 minutes late; the other is on time four days a week and 25 minutes late on the fifth. Same average, very different experience. RMSE is the measure that notices the difference, because occasional disasters matter more than steady small errors.",
    mechanics: [
      "Square every error first, average the squares, then take the square root. Squaring is what does the work: an error of 20 contributes four times as much as an error of 10, not twice as much. So a model with rare huge misses gets a much worse RMSE than its MAE would suggest.",
      "Comparing the two numbers is informative on its own. When RMSE is far larger than MAE, the model is usually fine but occasionally badly wrong — which matters a great deal if a single bad auction can hurt the book.",
      "Worked example. On the 91-day bill, MAE is about 9.7 bps but RMSE is about 17.8. That gap says most weeks are close, and a few weeks are not — exactly what you would expect for a rate that sits still and then jumps on news.",
    ],
    onThisPage:
      "The 95% band around every forecast is ±1.96 × RMSE, computed from recent errors only. So RMSE is not just a score — it is the thing that sets how wide the published range is.",
    pitfall:
      "Assuming RMSE and MAE rank models the same way. They can disagree, and when they do it is telling you the models fail differently: one is steadily mediocre, the other is usually excellent and occasionally awful.",
  },
  {
    term: "Out-of-sample",
    oneLine: "Scored only on auctions the model never saw while it was being built.",
    intuition:
      "A student who studies twenty past exam questions and then 'sits an exam' made of those same twenty questions will score brilliantly, and you will have learned nothing about whether they understand the subject. The only meaningful test uses questions they have not seen.",
    mechanics: [
      "A model fitted on a stretch of history can always be made to look good on that same stretch — just add enough parameters and it will trace every wiggle. This is why in-sample accuracy is nearly worthless as evidence, and why so many published trading strategies evaporate in live use.",
      "The backtest here works strictly forward. To score the auction at time t, the model is refitted using only auctions before t, then asked to predict t, and the error is recorded. Then it rolls forward one auction and does it again. No number from the future ever touches a fit.",
      "Every feature is also lagged, meaning it can only use information that existed before bids were submitted. The bill anchor is matched with same-day matches explicitly disallowed, so a bond auctioning on the same day as a bill cannot secretly peek at that bill's result.",
    ],
    onThisPage:
      "Every score on the tab is out-of-sample. The 'n' column is how many such forecasts were scored — 143 for the 91-day bill, around 40 for bonds.",
    pitfall:
      "Assuming the backtest is the same as a live track record. It is not — it is a simulation, however honest. The live log (currently 21 scored forecasts) is the real proof, and it can only grow one auction at a time.",
  },
  {
    term: "Confidence Interval",
    oneLine: "A range of values the true answer plausibly takes — and if that range includes zero, you have not proven anything.",
    intuition:
      "You flip a coin 10 times and get 6 heads. Is it biased? You cannot say — 6 out of 10 happens easily with a fair coin. Flip it 1,000 times and get 600 heads, and now you can. The confidence interval is what turns 'I measured something' into 'here is how sure I am', and it gets narrower as you gather more evidence.",
    mechanics: [
      "Every number measured from a finite sample is uncertain. A model's improvement over naive is not a fact — it is an estimate from a specific set of past auctions, and a different set would have given a slightly different number. The interval says how much that number would wobble.",
      "The decisive question is whether the interval for the improvement includes zero. If it does, then 'this model is actually no better at all' remains a live possibility, and the improvement cannot be claimed.",
      "Worked example, and this changed the conclusions. On the 91-day bill, momentum beat naive by 0.53 bps — which looks like a win. But its interval runs from −0.2 to +1.1. Zero sits inside it, so momentum might genuinely be worse. That number was previously reported as an edge; it is not one.",
      "Compare the 5-year bond, where curve carry beats naive by an interval of +16.8 to +49.8. The whole range is above zero. Even the pessimistic end of it is a large improvement, so the edge is real.",
    ],
    onThisPage:
      "The 'Better than naive by' column shows this interval directly. It is coloured green only when the entire range sits above zero.",
    pitfall:
      "Reading a wide interval as 'the model is bad'. It usually means 'we do not have enough auctions yet to tell' — which is a statement about evidence, not about the model. The 20-year bond was reported as having no edge for exactly this reason, and gained one as soon as it was given more history to be judged on.",
  },
  {
    term: "Bootstrap",
    oneLine: "Re-run the whole calculation on thousands of reshuffled versions of history to see how much the answer wobbles.",
    intuition:
      "You want to know how reliable your exam average is, but you only ever sat one set of exams. So you repeatedly draw random handfuls of your past papers, recompute the average each time, and watch the spread. If the answer swings between 55% and 85% depending on which papers you drew, your 'average' was not a solid number.",
    mechanics: [
      "The formal version: resample the observed errors, with replacement, 2,000 times. Each resample is a plausible alternative history. Recompute the statistic on each, then read the 2.5th and 97.5th percentiles of those 2,000 answers — that is the 95% interval.",
      "One crucial refinement is used here. Auction errors are correlated week to week: a volatile stretch produces several big misses in a row. If you resampled individual errors independently, you would break that clustering and pretend the sample contains far more independent information than it really does — producing intervals that are much too narrow, and false confidence.",
      "So the resampling is done in blocks: contiguous runs of consecutive auctions are drawn, keeping neighbouring errors together. This is called a moving-block bootstrap, and it gives honestly wider intervals.",
      "The seed is fixed, so a published interval is reproducible rather than shifting slightly every time the page rebuilds.",
    ],
    onThisPage:
      "Every 'Better than naive by' range, and every verdict that depends on it, comes from this procedure.",
    pitfall:
      "Believing the bootstrap manufactures information. It cannot. If you only have 20 auctions, no amount of resampling creates a 21st — it simply stops you from mistaking 20 observations for certainty.",
  },
  {
    term: "p-value",
    oneLine: "The probability of getting a result this good by pure luck, if the model actually had no skill at all.",
    intuition:
      "A friend claims they can predict coin flips. They get 6 of 10 right. You are unimpressed — that happens by chance about a third of the time. They get 19 of 20 right, and now you are interested, because pure luck almost never does that. The p-value puts a number on 'how impressed should I be'.",
    mechanics: [
      "Start by assuming the unflattering thing: the model has no edge whatsoever, and any apparent improvement is noise. Then ask how often noise alone would produce a result at least as good as the one observed. That frequency is the p-value.",
      "A small p-value means luck rarely produces this, so something real is probably happening. A large one means luck produces this routinely, so the result proves nothing. The conventional threshold is 0.05 — roughly a one-in-twenty chance of being fooled.",
      "Worked example. Curve carry on the 2-year bond has p = 0.0022, meaning results this good would arise by chance about twice in a thousand tries. Momentum on the 91-day bill has p = 0.26, meaning about one time in four — which is nowhere near good enough to claim an edge.",
    ],
    onThisPage:
      "The 'Could it be luck?' column. Bold means below 0.05. It is used together with the confidence interval, not instead of it — a verdict of 'established' requires both.",
    pitfall:
      "Reading it as 'the probability the model is useless'. It is the reverse conditional: the probability of this evidence assuming uselessness. And a p-value above 0.05 does not prove there is no effect — it often just means the sample is too small to see one, which is exactly what happened with the 20-year bond.",
  },
  {
    term: "Diebold-Mariano",
    oneLine: "A test built specifically to judge whether one forecaster is genuinely more accurate than another.",
    intuition:
      "Two weather forecasters both predict a month of days. One is slightly more accurate overall. Is one actually better, or did they just get an easier month? Because both faced the same days, you can compare them day by day rather than on averages — which is a far more sensitive test.",
    mechanics: [
      "The test looks at the difference in squared error between two models, auction by auction, and asks whether the average of that difference is reliably above zero. Because the two models forecast the same auctions, the comparison is paired, which removes the effect of some weeks simply being harder than others.",
      "It also corrects for the fact that consecutive errors are correlated. Without that correction, a run of easy weeks would masquerade as skill.",
      "One further correction matters a lot here. The standard version of this test is badly behaved on small samples — with around 20 bond auctions it declares victories that are not there, rejecting far more often than it should. The Harvey-Leybourne-Newbold correction rescales the statistic and compares it against a t-distribution instead of a normal one. Every p-value shown on this page is the corrected version.",
      "Worked example of why that matters: on the 10-year bond the uncorrected test gives p = 0.0298 and the corrected one gives 0.0467. Both clear 0.05, but only just — and on a weaker result the correction is the difference between claiming an edge and not.",
    ],
    onThisPage:
      "It is the second of the two conditions for an 'established' verdict, alongside a bootstrap interval that excludes zero.",
    pitfall:
      "Running it on a handful of observations and trusting the raw output. Small samples are exactly where this test misleads, which is why the correction is applied by default rather than as an afterthought.",
  },
  {
    term: "Selection Bias",
    oneLine: "Choosing the best-looking result after seeing the results — which guarantees you overstate it.",
    intuition:
      "Enter 100 people into a coin-flipping contest. Someone will flip 8 heads in a row and look supernatural. If you then present that person as a gifted flipper, you have proved nothing — with 100 tries, someone was always going to do it. Picking the winner after the fact is not evidence.",
    mechanics: [
      "This tab runs six models across eight tenors — around forty comparisons. Even if every model were worthless, several would beat naive by chance alone. Reporting whichever one won, with its flattering error number, would be exactly the coin-flipping contest.",
      "This is one of the main reasons backtested strategies disappoint in live use, and it is very easy to do accidentally: you try things, keep what works, and publish that. The trying-and-discarding is invisible in the final result.",
      "The defence is to fix the decision rule before looking. Here the rule is: a challenger is used only when its bootstrap interval excludes zero and the corrected significance test agrees. If nothing clears that bar, the page falls back to naive — even when another model's headline number looks better.",
      "This was a real correction, not a hypothetical one. An earlier version of this page picked the lowest-error model per tenor, which advertised edges on the bills that did not survive testing.",
    ],
    onThisPage:
      "It is why the 'Verdict' column exists, and why several tenors recommend naive despite another model showing a lower error.",
    pitfall:
      "Thinking it only applies to dishonest analysis. It applies to careful, well-meaning analysis by default — the only protection is deciding the rules in advance.",
  },
  {
    term: "Coverage",
    oneLine: "Checking whether the '95% range' really did contain the answer 95% of the time.",
    intuition:
      "A delivery service promises a 2-hour window and says it is right 95% of the time. You can check that claim: note the window, note when they actually arrive, count. If they only make it two-thirds of the time, the window is marketing, not information.",
    mechanics: [
      "The published band is ±1.96 × RMSE. That multiplier of 1.96 comes from the normal distribution — it is only a genuine 95% range if the errors follow a bell curve. Financial errors often do not: they have fat tails, meaning extreme moves happen more often than a bell curve predicts, which would make the band too narrow.",
      "Rather than assume, count. Across every backtested forecast, how often did the actual print land inside the band? The answer here is roughly 90–95%, depending on tenor — close to what is advertised, slightly conservative in places.",
      "That is a genuine finding and it could easily have gone the other way. If coverage had come out at 80%, the band would have needed widening, and every position sized off it would have been under-hedged.",
    ],
    onThisPage:
      "The 'Band holds' column. Treat anything materially below 90% as a warning that the range is being oversold.",
    pitfall:
      "Ignoring the band because the point forecast is more interesting. The band is the part that tells you how much money to put behind the view.",
  },
  {
    term: "Directional Accuracy",
    oneLine: "How often the model called up-versus-down correctly, ignoring how far off it was.",
    intuition:
      "A friend says 'the traffic will be worse than yesterday' and is right nine times out of ten. They cannot tell you your journey time to the minute, but you would still leave earlier. Knowing the direction is often enough to act on.",
    mechanics: [
      "For each auction, compare the sign of the predicted change with the sign of the actual change. The share that match is the directional accuracy. Fifty percent is a coin flip.",
      "For a bidder this is frequently more valuable than average error, because it tells you which side of the last print to lean, which is often the whole decision.",
      "Worked example. Curve carry calls direction correctly about 95% of the time on the 5-year and 10-year bonds. That is a strong signal even though its average miss of roughly 22 bps sounds large — the point is that when the bill curve has fallen, the bond nearly always follows.",
      "Naive shows a dash in this column rather than a number. It predicts no change at all, so it never makes a directional call and there is nothing to score. That is honest rather than a missing value.",
    ],
    onThisPage:
      "The 'Right direction' column. Read it alongside the typical miss: high directional accuracy with a wide band means 'confident about which way, vague about how far'.",
    pitfall:
      "Reading a high figure as a licence to size aggressively. Being right about direction 95% of the time says nothing about the size of the move, and the 5% can be brutal.",
  },
  {
    term: "Lookback Window",
    oneLine: "How much history a model learns from and is judged on — and it is a genuine trade-off, not a detail.",
    intuition:
      "Learning to drive in a city you left ten years ago teaches you roads that have since been rebuilt. Learning from only last week gives you too few journeys to spot any pattern. You want the longest stretch of history that still resembles the present.",
    mechanics: [
      "Too short and you lack the examples to tell skill from luck. Too long and you learn from a world that no longer exists — in Bangladesh's case a period of administered rate caps and very different volatility.",
      "This dashboard keeps two windows because the two jobs pull in opposite directions. The evidence window decides whether an edge is real and wants as many observations as possible. The band window decides how wide the published range is and wants only the current regime.",
      "So bills use 3 years for evidence — they auction weekly, which is already 150+ examples — while bonds use 5 years, because auctioning monthly makes 3 years only about 20 observations. The band, for every tenor, uses only the recent half of the record.",
      "Worked example of why this matters. At a 3-year window the 20-year bond showed no detectable edge from curve carry, with an interval of −13.3 to +22.6. Given 5 years it becomes +2.3 to +13.7 with a p-value of 0.028. The effect was there all along; three years simply could not see it.",
      "Longer is not automatically better. Using the entire history back to 2007, the 10-year and 20-year edges vanish again — because the pre-2015 managed-rate era genuinely behaved differently.",
    ],
    onThisPage:
      "It explains why bond and bill conclusions rest on different amounts of history, and why the band never uses the flattering calm years.",
    pitfall:
      "Assuming 'no effect found' means 'no effect exists'. Very often it means the window was too short to detect one. Absence of evidence and evidence of absence are different claims.",
  },
  {
    term: "Basis Point",
    oneLine: "One hundredth of a percentage point — the unit interest rates are actually discussed in.",
    intuition:
      "Saying 'the rate rose by 0.08 percentage points' is a mouthful and easy to misread. Saying 'it rose 8 basis points' is quick and unambiguous. It exists for the same reason recipes use grams rather than fractions of a kilogram.",
    mechanics: [
      "1 basis point = 0.01% = 0.0001. A move from 9.3000% to 9.3800% is 8 basis points. Often written bps and said 'bips'.",
      "The reason it matters here: rate moves are small in percentage terms but large in money terms. On a 1,000 crore position, a few basis points is a meaningful sum, so the unit needs to be fine enough to argue about.",
      "Every forecast error on this page is quoted in basis points precisely so that tenors sitting at different rate levels can be compared on one scale.",
    ],
    onThisPage:
      "All errors, bands and improvements are in bps. The forecast levels themselves are shown as percentages.",
    pitfall:
      "Confusing a basis point with a percent. A 50 bps cut is half a percentage point, not fifty percent — a hundred-fold difference.",
  },
  {
    term: "Granger Causality",
    oneLine: "A test of whether knowing one thing's past helps predict another's future — which is not the same as causing it.",
    intuition:
      "Shops filling with umbrellas reliably precedes rainy weeks. Umbrella stocking helps predict rain, so it 'Granger-causes' rain in the technical sense — while obviously not causing weather at all. Both respond to the forecast. The test measures predictive usefulness, nothing more.",
    mechanics: [
      "The procedure: first predict the target using only its own past. Then predict it again using its own past plus the candidate driver's past. If the second is significantly better, the driver Granger-causes the target.",
      "It was used here to check the auction-demand factors — cover ratio, its change, relative auction size, distance from the trailing mean — against the change in the cutoff rate, at lags of one to four auctions.",
      "The result was uniformly negative. Not one factor passed, at any lag, on 148 or more auctions. All p-values above 0.18.",
      "That single finding explains why the regression model built on those factors performs worse than naive. The factors describe an auction that has already happened; describing is not predicting. And every useless coefficient still has to be estimated from finite data, so the estimation error goes straight into the forecast — making it actively worse rather than merely no better.",
    ],
    onThisPage:
      "It appears in the diagnostics table, and it is the reason the regression model is kept on display despite losing: knowing why something fails is worth more than hiding it.",
    pitfall:
      "The name. It is an unfortunate historical label — it says nothing whatsoever about real-world causation.",
  },
  {
    term: "Stationarity",
    oneLine: "Whether a series has a stable level it keeps returning to, or wanders off indefinitely.",
    intuition:
      "A dog on a lead in a park wanders, but stays near its owner — you can sensibly talk about where it 'usually' is. A dog off the lead heading across town has no usual position; asking for its average location is meaningless. The first is stationary, the second is not.",
    mechanics: [
      "This matters because most statistical machinery assumes a stable average. Applied to a wandering series it produces confident nonsense, including the classic trap of two unrelated drifting series appearing strongly correlated simply because both drift.",
      "Two tests are run, and they are deliberately opposites. ADF starts by assuming the series wanders and looks for evidence that it does not. KPSS starts by assuming it is stable and looks for evidence that it is not. When both point the same way you have strong evidence; when they disagree, the safe reading is to treat the series as wandering.",
      "This is why every model on this page forecasts the change in the cutoff rate rather than its level. Rate levels wander; changes bounce around zero and are far better behaved.",
    ],
    onThisPage:
      "In the diagnostics table you will see ADF and KPSS reported for both the level and the change of every tenor's cutoff.",
    pitfall:
      "Assuming the two tests should always agree. They are constructed with opposite starting assumptions, so disagreement is informative rather than a sign something has gone wrong.",
  },
  {
    term: "Cointegration",
    oneLine: "Two series that each wander freely but never drift far apart — tethered rather than independent.",
    intuition:
      "A person walking a dog on a long lead. Track either one alone and they wander unpredictably. But the distance between them never grows beyond the lead. Neither position is stable; the gap is.",
    mechanics: [
      "Formally: two non-stationary series are cointegrated if some combination of them is stationary. Usually that combination is simply the gap between them.",
      "The economic idea for this project is that auction cutoff rates should be tethered to Bangladesh Bank's policy rate. Cutoffs wander and the policy rate steps around, but the spread between them should not drift forever — if cutoffs got far above the corridor, forces should pull them back.",
      "If that holds, the correct model is an error-correction model: how far the rate currently sits from its anchor tells you which way it should move next.",
      "This is the one test on the roadmap that cannot yet be run. It needs the policy corridor as a step function through history, and the database currently holds a single verified corridor observation. Fitting it on unverified numbers would produce a confident, wrong model — so it is built and deliberately switched off.",
    ],
    onThisPage:
      "See the 'Error-Correction Model — Status' panel, which reports exactly what is missing and how to unlock it.",
    pitfall:
      "Confusing it with correlation. Two series can be highly correlated over a stretch and still drift apart forever; cointegration is specifically about the long-run tether.",
  },
];

const BY_TERM = new Map(CONCEPTS.map(c => [c.term.toLowerCase(), c]));

/** Full concept explanation for a term, if one has been written. */
export function concept(term: string): Concept | undefined {
  return BY_TERM.get(term.toLowerCase());
}
