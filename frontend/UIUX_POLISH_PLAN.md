# BB Market Intelligence — UI/UX Polish Plan (extensive)

_Audit date: 2026-06-05. Scope: every page, component, chart, and the shared design system._

## Verdict up front

The foundation is **strong** — this is not a rebuild. There's a real design system
(`app/globals.css`): semantic tokens, dark/light themes, three density modes, a
Bloomberg-terminal aesthetic, hand-rolled SVG charts with crosshair tooltips, KPI
strips, sticky-header tables, a policy corridor viz, and a customise panel (accent /
density / gridlines / ticker speed). What's missing is the last 15%: **consistency,
honesty cues, and analytical depth.** The work below is polish, not demolition.

Grounding numbers from the audit:
- **155 inline `.toFixed()` / `.toLocaleString()` calls across 35 files** — a real
  `lib/format.ts` exists but is bypassed almost everywhere → inconsistent decimals,
  units, and dates.
- **Two parallel chart stacks**: standalone components (`CashFlowChart`, `FxChart`,
  `CallMoneyChart`, `ReservesChart`, …) **and** the newer terminal primitives
  (`components/terminal/charts.tsx`). Duplicate tooltip/axis logic, divergent styling.
- **Fake "live" still present**: `AppShell.tsx:77` renders a hard-coded `LIVE` ticker
  badge and `:155` a `Live · cloud-refreshed daily` line — the exact thing flagged as
  dishonest. The honest `UpdateStatus` panel already exists; the shell contradicts it.
- **Decorative search**: the topbar `<input>` (`AppShell.tsx:56`) has no handler.

---

## Tier 0 — Trust & honesty (do first; highest user value)

The dashboard is the *sole* source of truth, so every "freshness" signal must be real.

1. **Kill the fake "LIVE".** Replace the static `LIVE` ticker tag and
   `Live · cloud-refreshed daily` with a real state derived from `UpdateStatus` /
   `/api/meta/status`: e.g. `UPDATED 3h ago` (green if all datasets fresh, amber if any
   dataset is stale vs its expected cadence, red if a fetch failed). Files:
   `components/terminal/AppShell.tsx` (Ticker tag + view-meta), reuse `UpdateStatus`
   freshness logic.
2. **Per-dataset "as of" on every page header.** `Freshness` exists and is used on some
   pages — make it universal and consistent (same position, same `timeAgo` phrasing).
   Pages currently missing or inconsistent: audit all 12.
3. **Data-integrity badge in the shell.** Surface `data_health` (from `/api/meta/status`,
   already computed by `validate.py`) as a small shield in the topbar: green
   "all checks pass" / amber "N issues" that opens the issue list. Turns the silent
   integrity monitor into a visible trust signal.
4. **Provenance on hover.** Every figure should be traceable: tooltip or `InfoTip`
   showing source (GSOM page / OMO PDF / Treasury auction) + as-of date. Especially
   coupons (show the formula string already stored in `coupon_events.formula_string`)
   and OMO rows (link `source_pdf`).
5. **Keep the illustrative banners** (`callmoney` corridor, `monetary` page) — they are
   correct and honest. Add the same amber banner pattern anywhere seed/DRAFT data is
   shown so the treatment is uniform.

---

## Tier 1 — Consistency (removes the "unfinished" feel)

6. **Adopt `lib/format.ts` everywhere.** Replace all 155 inline calls with
   `fmtBDTmn / fmtCrore / fmtPct / fmtUSDmn / bps / fmtDate / timeAgo`. Enforce with an
   ESLint rule (ban raw `.toLocaleString()` in `app/` and chart components). Decide and
   document **one** money convention per context (crore for BB-native tables, BDT mn for
   cash-flow, "k mn" only in KPIs) — today cashflows mixes `mn`, `k`, and raw locale
   strings in the same view.
7. **Unify the chart stack.** Migrate the standalone chart components onto the
   `terminal/charts.tsx` primitives (`LineChart`, `StackedArea`, `ComboChart`,
   `Sparkline`) so every chart shares crosshair, tooltip, axis, grid-toggle, and theme
   tokens. Delete the superseded one-offs once parity is confirmed. Net: one tooltip
   style, one axis font, one hover behaviour across the whole app.
8. **Number/sign/colour discipline.** One rule for deltas (▲ green = good unless inverted),
   one for negative money (neg colour + parentheses or minus, not both), tabular-figures
   (`tnum`) everywhere numbers align in columns (some tables already do, some don't).
9. **Panel & section rhythm.** Standardise panel header height, KPI strip gaps, and the
   `view-head` block so every page breathes identically. A couple of pages set inline
   `gridTemplateColumns` overrides (e.g. cashflows) instead of the `.kpi-strip.four` class.

---

## Tier 2 — Per-page depth & polish

**Overview (`/`)** — Make it a true "desk cockpit": top strip of the 4-5 signals that
matter at 8am (call WAR vs corridor, latest 91D/10Y, net liquidity stance, today's
net cash flow, reserves). Each KPI links to its page. Add a "what changed since
yesterday" line.

**Cash Flows (`/cashflows`)** — (a) replace inline `k()`/locale with format util;
(b) highlight *today* row more strongly and show the un-dragged scheduled date (now
fixed in data); (c) coupon cells should expose the formula on hover; (d) add a
cumulative net-borrowing line + a "next 7 days" mini-summary; (e) the status column
("partial"/✓) needs a legend/tooltip.

**OMO (`/omo`)** — (a) **rate ranges now render** (e.g. `4.00–5.25%`) — verify the
column widths; (b) add an instrument filter + a net injection/absorption KPI with
"tight/flush" stance (partly present); (c) maturity ladder (what rolls off when);
(d) link each op to its source PDF.

**Yields (`/yields`)** — (a) curve overlay at multiple dates; (b) bid-to-cover +
devolvement badges (`BidCoverChart` exists — ensure it's surfaced); (c) 2s10s / spread
history (`CurveSlopeChart` exists); (d) annotate policy-rate changes on the time axis.

**Call Money (`/callmoney`)** — (a) overlay the policy corridor band behind WAR (data
is illustrative — keep the banner); (b) volume + rate combo already good; (c) show
highest/lowest/weighted spread intraday.

**FX Auctions (`/fx`)** — (a) bid vs accepted, cut-off vs weighted, cover ratio over
time; (b) USD/BDT spot context; (c) format USD mn consistently.

**Ref Rates (`/refrate`)** — (a) the empty-state is now user-facing (good); (b) plot
SMART/MODR/reference series with corridor context; (c) clarify which rates are
published vs derived.

**External (`/macro`)** & **Monetary (`/monetary`)** — keep the **illustrative** banner
until BBS/BB fetchers are wired (Phase 2). Visually de-emphasise (lower contrast,
"pending" chip) so they're clearly second-class until real.

**Securities (`/securities`)** — (a) sortable/filterable table (type, tenor, coupon,
outstanding); (b) per-ISIN drawer: full coupon schedule with scheduled vs payment
dates + formula; (c) link to portfolio.

**Portfolio (`/portfolio`)** — (a) the paste-ISINs tool exists; polish input affordance
(sample + validation feedback); (b) show weighted yield, MTM, ±100bp DV01; (c) save
a holdings list to localStorage.

**Glossary (`/glossary`)** — wire `InfoTip` from every jargon term across pages back to
the glossary entry (DOMMR, BOFR, GSOM, SLF, IBLF, SDF, CB_REPO, AR, FRTB, devolvement,
bid-to-cover, …).

**Drilldown (`/drilldown`)** — (a) it's the payoff of a date click: show maturities,
coupons (with formula), auctions for that day; (b) make it a slide-over from cashflows
instead of a full nav; (c) totals + net at top.

---

## Tier 3 — Interaction, motion, accessibility

10. **Loading & error states.** App-router `loading.tsx`/`error.tsx` exist at root —
    add route-level skeletons so panels show shimmer, not layout shift, on slow fetches.
11. **Functional search / command palette.** Make the topbar `/` search real: fuzzy-jump
    to a page, an instrument (ISIN/name), or a tenor. Or remove it if out of scope —
    a dead input reads as unfinished.
12. **Keyboard & focus.** Visible focus rings on nav, tabs, table rows; `/` to focus
    search; `Esc` already closes modals; arrow-key nav on tables.
13. **Reduced motion.** The ticker scroll and the `pulse` status dot should respect
    `prefers-reduced-motion` (pause/àfreeze). Currently always-on.
14. **Contrast & colour-blind safety.** Verify `--fg-dim`/`--fg-mute` on `--panel` hit
    WCAG AA; don't rely on red/green alone for sign (pair with ▲/▼ + value).
15. **Touch targets & mobile.** Re-test the 860px/520px breakpoints: tables → horizontal
    scroll with sticky first column; KPI strips already collapse; verify the customise
    FAB doesn't cover content.

---

## Tier 4 — Analytical "wow" (on existing data, no new scrapers)

16. **Liquidity stance gauge** (OMO net + call WAR vs corridor) on Overview.
17. **Curve animation / small-multiples** of the yield curve month-by-month.
18. **Cash-flow calendar heatmap** (month grid, intensity = net flow) as an alt view.
19. **Cross-links everywhere** (an auction in cashflows → its yield point → the security).
20. **Export parity** — every panel's `DownloadButton` exports exactly what's shown
    (already partly there via `ExportAll`).

---

## Suggested sequencing

1. **Tier 0** (trust) — small, high-impact, mostly `AppShell` + `Freshness` + a status
   shield. Ship first.
2. **Tier 1** (format util + chart unification) — mechanical but removes the
   "inconsistent" feel app-wide. Do behind one PR per concern.
3. **Tier 2** page depth — one page per slice, highest-traffic first (Overview, Cash
   Flows, OMO, Yields).
4. **Tier 3** a11y/motion/search.
5. **Tier 4** analytics flourishes.

## For Claude Design (handoff)

Share this file + `app/globals.css` (the token system) + screenshots of each page.
The ask: a refined visual language *within* the existing terminal aesthetic — type
scale, spacing rhythm, chart palette (currently accent-green heavy), and the freshness/
integrity status components from Tier 0. Keep the dual-theme + density tokens; don't
introduce a component library that fights the hand-rolled SVG charts.
