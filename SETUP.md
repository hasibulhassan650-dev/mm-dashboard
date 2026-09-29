# Porting mm_dashboard to a new PC

The live site is **unaffected** by this — GitHub Actions (fetchers), Railway (API) and
Vercel (frontend) keep running in the cloud. You're only moving the **dev environment**
(VS Code + Claude Code). The new PC just needs the code + secrets to reach the same
Supabase database.

## 1. Install prerequisites on the new PC
- **Git**
- **Python 3.11** (matches the cloud/CI exactly; 3.12 is fine too — avoid 3.14, some
  wheels lag on it)
- **Node.js LTS** (20 or 22) — for the Next.js frontend
- **Google Chrome** — the OMO/Treasury/Call-Money/FX/Ref-Rate fetchers drive a real
  Chrome to clear BB's F5/TSPD bot protection
- **VS Code** + the **Claude Code** extension (sign in after install)

## 2. Get the code
```bash
git clone https://github.com/hasibulhassan650-dev/mm-dashboard.git
cd mm-dashboard
```
Authenticate with **your own** GitHub login (`gh auth login`, or an SSH key, or a fresh
Personal Access Token). Do **not** copy this PC's token-embedded remote URL — set up
clean auth on the new machine.

## 3. Bring the 2 secret files (not in git)
Copy these from the old PC to the **same paths** on the new PC (USB / private transfer —
never email or commit them):
- `.env`               → repo root  (contains `DATABASE_URL`)
- `frontend/.env.local` → the frontend API base URL

If you can't copy them, recreate:
- `.env`: `DATABASE_URL=<your Supabase connection string>` (Supabase → Project →
  Settings → Database → Connection string, "Session"/pooler)
- `frontend/.env.local`: `NEXT_PUBLIC_API_URL=<your Railway API URL>`

## 4. Install dependencies
```bash
# Backend
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# Frontend
cd frontend
npm install
cd ..
```

## 5. Verify it works
```bash
python validate.py          # should print {"ok": true, "issue_count": 0, ...}
cd frontend && npm run build # should compile clean, then: npm run dev
```
- `validate.py` passing = the new PC reached Supabase with your `.env`.
- `npm run dev` → open the local URL; the Data-Updates panel should show live status.

## 6. (Optional) Bring Claude Code's project memory
Claude Code keeps per-project memory on the machine, not in git. To carry the context
Claude has about this project, copy the memory folder to the matching path under the new
PC's user profile:
`~/.claude/projects/<project-hash>/memory/`
It's optional — memory rebuilds as you work.

## Notes
- **You do not need to migrate the database.** All real data lives in Supabase; both PCs
  talk to the same cloud DB via `.env`.
- The Chrome-based fetchers only run when you invoke them (or the cloud cron does). Day-
  to-day dashboard viewing needs only the API + frontend, which read from Supabase.
- Absolute Python paths used in ad-hoc commands on the old PC (e.g. a specific
  `python.exe`) don't apply here — just use the new PC's `python`.

---

# Working from TWO PCs (A and B) — full parity, kept in sync

Both PCs can do **everything**: edit code with Claude Code, run the fetchers/pipeline
against Supabase, run the frontend, and `git push` to deploy (Railway + Vercel rebuild
on every push to `main`). They stay in sync through the two shared sources of truth:
**GitHub for code, Supabase for data.** Each PC is an equal client.

## One-time on PC B
Do steps 1–5 above (install tools, clone, copy the 2 secret files, install deps, verify).
That alone makes PC B a full peer of PC A.

## Daily rule — one line
> **`git pull` before you start, `git push` when you stop.**

Because both machines push to the same `main`:
- **Start of a session:** `git pull` to get whatever the other PC pushed. If you forget
  and start editing stale code, you'll get a merge conflict later.
- **End of a session:** commit + push so the other PC (and the cloud) has your work.
- **Never leave uncommitted work on one PC and switch to the other** — that work isn't on
  GitHub, so the other PC can't see it. Commit + push first.

## Data is already shared — no action needed
Because `.env` on both PCs points at the same Supabase URL, running the pipeline on
**either** PC updates the **same** live database. The writes are idempotent (upserts), so
it's safe — just don't run the heavy full fetch on both PCs at the exact same minute.

## Claude Code across two PCs
- Sign in to the Claude Code extension with the **same Anthropic account** on both.
- Claude's project **memory is per-machine** (not in git). PC B starts with a fresh
  memory and rebuilds it as you work; optionally copy `~/.claude/projects/<hash>/memory/`
  from PC A to seed it (see step 6).

## If the two PCs ever diverge (conflict)
`git pull` reports a conflict when both edited the same lines. Easiest recovery: on the
PC with the work you want to keep, `git push`; on the other, `git pull` (or, if it has
nothing worth keeping, `git reset --hard origin/main` to match GitHub). Ask Claude Code
to resolve a specific conflict if you're unsure.

