# CLAUDE.md — 12-rule template

These rules apply to every task in this project unless explicitly overridden.
Bias: caution over speed on non-trivial work.

## Rule 1 — Think Before Coding
State assumptions explicitly. Ask rather than guess.
Push back when a simpler approach exists. Stop when confused.

## Rule 2 — Simplicity First
Minimum code that solves the problem. Nothing speculative.
No abstractions for single-use code.

## Rule 3 — Surgical Changes
Touch only what you must. Don't improve adjacent code.
Match existing style. Don't refactor what isn't broken.

## Rule 4 — Goal-Driven Execution
Define success criteria. Loop until verified.
Strong success criteria let Claude loop independently.

## Rule 5 — Use the model only for judgment calls
Use for: classification, drafting, summarization, extraction.
Do NOT use for: routing, retries, deterministic transforms.
If code can answer, code answers.

## Rule 6 — Token budgets are not advisory
Per-task: 40,000 tokens for a small fix, 150,000 for a multi-file feature. Per-session: 500,000.
(The old 4,000 / 30,000 was below the cost of reading three files, so it was breached by every task and meant nothing.)
Searches go to a subagent so raw output stays out of the main context.
If approaching budget, summarize and start fresh. Surface the breach. Do not silently overrun.

## Rule 7 — Surface conflicts, don't average them
If two patterns contradict, pick one (more recent / more tested).
Explain why. Flag the other for cleanup.

## Rule 8 — Read before you write
Before adding code, read exports, immediate callers, shared utilities.
If unsure why existing code is structured a certain way, ask.

## Rule 9 — Tests verify intent, not just behavior
Tests must encode WHY behavior matters, not just WHAT it does.
A test that can't fail when business logic changes is wrong.

## Rule 10 — Checkpoint after every significant step
Summarize what was done, what's verified, what's left.
Don't continue from a state you can't describe back.

## Rule 11 — Match the codebase's conventions, even if you disagree
Conformance > taste inside the codebase.
If you think a convention is harmful, surface it. Don't fork silently.

## Rule 12 — Fail loud
"Completed" is wrong if anything was skipped silently.
"Tests pass" is wrong if any were skipped.
Default to surfacing uncertainty, not hiding it.

---

# This project (read before the rules above are applied)

Bangladesh Taka money-market dashboard for an ALM / treasury desk. **Data accuracy outranks everything**: never
lose accuracy, never miss a product, never fabricate. Build things so the owner never has to maintain them.

## How it is wired
- Frontend `frontend/` (Next.js, Vercel, password gate always on) -> API `api/` (FastAPI, Vercel) -> Supabase Postgres.
  Fetchers/engines/parsers live at the repo root.
- `api/` deploys rooted at `api/`: it **cannot import** root `config`/`db`/`engines`. New API logic goes in
  `api/routers/*_logic.py` (tests enforce this with an AST guard).
- **Bangladesh Bank's firewall CAPTCHAs GitHub's runners**, so BB data is read from the owner's PC (Windows scheduled
  tasks from `install_news_task.ps1`). GitHub refresh jobs are a backup and report through the single
  `data-integrity` issue. A failed CI refresh is usually BB blocking the runner, not a bug.
- The repo is **PUBLIC**: never commit a secret. Keys live in `.env` (git-ignored) or GitHub secrets.

## Data rules
- Unsure means PENDING plus an evidence-backed record (`data/seeds/known_absences.yaml`,
  `calendar_decisions.yaml`), never a guess. A gap BB cannot fill is recorded as such, not hidden.
- No hardcoded lists of products: OMO instruments (`OMO_INSTRUMENTS` in `frontend/lib/terminal.ts`) and bond
  tenors come from the data. Registries (nav, exports, expectations, theme tokens) are guarded by tests.
- Desk entries are never BB data. News-derived events are shown, never summed into the ladder.
- Explanations of past incidents and their lessons: the auto-memory index (`MEMORY.md`).

## Checks (use these, not the whole suite, while iterating)
- `set -o pipefail; python -m pytest tests -q` (pipefail, or a failing pytest looks green behind `| tail`)
- `python validate.py` (integrity verdict as JSON), `python audit.py --window 180` (per-dataset sweep)
- `cd frontend && npx tsc --noEmit`
