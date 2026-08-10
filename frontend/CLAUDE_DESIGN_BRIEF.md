# BB Market Intelligence — Design Brief for Claude (paste this into claude.ai)

> Paste this whole file into a **claude.ai Project**, attach 4–6 screenshots of the
> current pages, then use the starter prompt at the bottom. Iterate on the Artifact
> it produces. When you like it, bring the result back to Claude Code to wire it up.

## What it is
A Bloomberg-style **money-market intelligence terminal** for Bangladesh Bank data —
the user's *sole* source of market data + analytics (no Excel, no PowerBI). It runs
in the cloud and auto-refreshes 3×/day. Audience: a **treasury / ALM desk** that needs
to read the market at a glance and drill into detail. Tone: **institutional, dense,
precise, trustworthy** — not consumer-flashy.

## Keep the aesthetic (don't restyle from scratch)
It already has a real, coherent terminal design system. The redesign should **refine
within it**, not replace it: better type scale, spacing rhythm, chart palette, and the
trust/freshness components. Keep: dual light/dark themes, the density modes, and the
hand-rolled SVG charts (don't introduce a chart library or a heavy component kit that
fights them).

## Design tokens (the existing system — reuse these exact values)
Fonts: **IBM Plex Sans** (UI) + **IBM Plex Mono** (all numbers, tabular figures).
Radius: 10px (cards) / 7px (controls). Accent is green; keep it but it's overused —
introduce a disciplined secondary palette for charts.

```
Semantic:   accent #1f9e6e   positive #2bb673   negative #e5564b   info #4f8ff7   warn #d99a1f
Dark   →    bg #0a0c0f   elev #0d1014   panel #13171d   panel-2 #171c23
            border #242c35   fg #e8edf2   fg-dim #9aa7b4   fg-mute #66717e
Light  →    bg #efece4   elev #f6f4ee   panel #ffffff   panel-2 #f8f6f1
            border #e0dcd1   fg #1b1e22   fg-dim #5a636e   fg-mute #8a929c
Density →   compact / regular / comfy  (pad+gap 12/16/22px, row height 30/36/44px)
```

## Layout shell (keep the structure)
Left sidebar (12 sections) · topbar (breadcrumb, search, Data-Updates status, export,
theme toggle) · scrolling "LIVE" ticker · page = view-head + a 12-col grid of panels +
KPI strips. Components in play: KPI cards w/ sparkline, panels w/ header, sticky-header
data tables, segmented tabs, legend toggles, policy-rate corridor viz, drill-down modal.

## The 12 pages
Overview · Cash Flows · OMO · Yields · Call Money · FX Auctions · Ref Rates ·
External (macro) · Monetary · Securities · Portfolio · Glossary.

## What to improve (priority order)
1. **Trust layer**: a freshness/"last refresh run" indicator and a data-integrity
   shield that feel authoritative (no fake "LIVE"). Design these components.
2. **Type & spacing rhythm**: a tighter, more deliberate scale so every page breathes
   identically. Numbers are the hero — make the mono/tabular treatment sing.
3. **Chart palette & styling**: move beyond accent-green-everything to a cohesive,
   colour-blind-safe categorical palette; refine axes, tooltips, crosshair, legends.
4. **The Overview as a desk cockpit**: the 5 signals that matter at 8am, each a
   KPI that links to its page; a "what changed since yesterday" line.
5. **Data density without clutter**: table styling, KPI strips, panel hierarchy.
6. **Motion & states**: subtle, reduced-motion-aware; skeleton loaders; empty states.

## Deliverable I want from you (Claude Design)
A **single self-contained Artifact** (React + inline styles or Tailwind, dark theme
first, light theme variant) mocking the **Overview page** end to end using the tokens
above and realistic placeholder BB numbers — sidebar, topbar, ticker, KPI strip,
2–3 charts, a table, and the new freshness/integrity components. Then iterate per my
feedback. Don't wire real data — that happens later in the codebase.

---

## STARTER PROMPT (copy/paste after attaching screenshots)

> You are my senior product designer. Read the attached brief and screenshots. Design a
> refined version of the **Overview** page for this Bangladesh Bank money-market terminal,
> **staying inside the existing dark/light token system and IBM Plex type** — refine,
> don't reinvent. Produce a single self-contained React Artifact (dark theme, with a
> light variant toggle) showing the sidebar, topbar, ticker, a KPI strip of the 5 key
> desk signals, 2–3 charts (use a cohesive colour-blind-safe palette, not all green), a
> data table, and a polished "Data Updates / last refresh run" status component plus a
> data-integrity shield. Use realistic placeholder numbers. Optimise for an institutional
> treasury desk: dense, precise, trustworthy, numbers in tabular mono. After the first
> version, I'll give feedback to iterate. Don't wire real data.

Then refine with follow-ups like: *"tighten the KPI cards", "try the chart palette on the
yield curve too", "show the light theme", "make the freshness component the hero of the
topbar", "design the Cash Flows page next using the same language."*
