# Deploy Steps — 100% Free

## One-time setup (30 minutes total)

---

### 1. Supabase — Create your cloud database (10 min)

1. Go to https://supabase.com → Sign up free → New Project
2. Choose a region close to Bangladesh (Singapore or Mumbai)
3. Wait ~2 min for project to provision
4. Go to: Project → Settings → Database → Connection string → **URI** tab
5. Copy the connection string — looks like:
   `postgresql://postgres.abcdef:YOUR_PASSWORD@aws-0-ap-southeast-1.pooler.supabase.com:6543/postgres`

---

### 2. Migrate your data to Supabase (5 min)

On your Windows machine:

```cmd
set DATABASE_URL=postgresql://postgres.abcdef:YOUR_PASSWORD@pooler.supabase.com:6543/postgres
python migrate_to_supabase.py
```

You should see all tables migrated with row counts.

---

### 3. Push code to GitHub (5 min)

```cmd
cd C:\Users\Administrator\Desktop\mm_dashboard
git init
git add .
git commit -m "initial commit"
```

Go to https://github.com → New repository → name it `mm-dashboard` → Public or Private

```cmd
git remote add origin https://github.com/YOUR_USERNAME/mm-dashboard.git
git push -u origin main
```

---

### 4. Add GitHub Actions secret (2 min)

In your GitHub repo:
- Settings → Secrets and variables → Actions → New repository secret
- Name: `DATABASE_URL`
- Value: your Supabase connection string

The daily pipeline will now run automatically at 9 AM Bangladesh time every day.
You can also trigger it manually: Actions tab → Daily Data Pipeline → Run workflow

---

### 5. Deploy the site and the API (Vercel)

Two Vercel projects from the same repo: the site rooted at `frontend/` and the API rooted at `api/`.
The API cannot import the root `config`, `db` or `engines`; its logic lives in `api/routers/*_logic.py`.
Set the variables in the table below, then push: Vercel deploys on every push to `main`.

### 6. Where BB data is read

Bangladesh Bank's firewall CAPTCHAs GitHub's runners, so BB pages are read from the owner's PC by Windows
scheduled tasks. Install them once, from the repo folder:

```cmd
powershell -ExecutionPolicy Bypass -File .\install_news_task.ps1
```

GitHub's refresh jobs are a backup and report through the single `data-integrity` issue. A failed CI
refresh is usually BB blocking the runner, not a bug.

---

## What runs where

| Task | Where | How often |
|------|-------|-----------|
| BB pages (OMO, treasury, GSOM, FX, call money, circulars) | Your PC (scheduled tasks) | Hourly light, daily full |
| News / Liquidity Watch | GitHub Actions and your PC | Every 30 min / 5 min |
| Integrity check and alert issue | GitHub Actions | After each refresh |
| Site | Vercel | Always on |
| API | Vercel | Always on |
| Database | Supabase | Always on |

---

## Environment variables on Vercel

Set on **both** Vercel projects (the site and the API) unless noted:

| Variable | Where | Needed for |
|---|---|---|
| `DATABASE_URL` | API, and the GitHub Actions secrets | everything |
| `SITE_PASSWORD` | site | overrides the baked-in password hash. Optional — the gate works without it |
| `AUTH_SECRET` | site | signs the session cookie. Optional; falls back to the hash |
| `DESK_WRITE_SECRET` | **site AND API, the same value** | `/desk` — entering an auction cut-off on auction day |

### DESK_WRITE_SECRET — the one step that has to be taken deliberately

`/desk` is where the desk records the cut-off it saw at the auction, hours
before BB publishes it. That makes it the only **write** path in an API that is
otherwise read-only and publicly reachable, so it **fails closed**: with the
variable unset the API refuses every write with a 503, and the form says so and
stays disabled rather than accepting a figure and silently dropping it.

There is no default and no fallback — a secret committed to a public repo is
not a secret. The browser never sees this one either: the page posts
same-origin to the Next app, which holds the value server-side and forwards it
with the request, so the API's CORS stays GET-only.

Any value will do. Generate one with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Set it in **both** projects or neither. Site-only means the forward is rejected
by the API; API-only means the form never sends anything.

A desk mark is never stored as BB data: it lives in its own table, is labelled
provisional everywhere it appears, and BB's published cut-off is preferred the
moment it exists.

### Liquidity Watch — runs with nothing to configure

`/liquidity-watch` lists liquidity events reported in the Bangladeshi press that BB's own
tables do not hold (a subsidy release, government borrowing from BB, support to a bank, a
CRR/SLR change). The **News Watch** workflow reads the feeds every 30 minutes and needs no
secret: a free rule reader (`engines/news_rules.py`) does the reading, and every event it
reports is checked in code (the quote must be in the article, the amount is re-read from it).
The page says which reader is in use.

Two optional upgrades, both repository **secrets** (Settings → Secrets and variables → Actions):

* **A free language model** — set all three of `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` for
  any OpenAI-compatible endpoint with a free tier (Gemini: base URL
  `https://generativelanguage.googleapis.com/v1beta/openai`, key from aistudio.google.com/apikey).
  A failing call falls back to the rule reader, so nothing goes unread.
* **Claude** — `ANTHROPIC_API_KEY` (pay-per-token; capped at 40 calls a day). Wins over the free
  model if both are set, and enables the backfill: run News Watch with `action = backfill`,
  `since = 2026-07-01` and Claude searches Bangladesh's outlets month by month (history is marked
  as already announced, so it sends no emails).

News-derived events are never added to a ladder, cumulative or model total.
