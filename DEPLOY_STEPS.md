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

### 5. Deploy to Streamlit Cloud (5 min)

1. Go to https://share.streamlit.io → Sign in with GitHub
2. New app → Select your repo `mm-dashboard`
3. Main file path: `streamlit_app.py`
4. Click **Deploy**

**Add your Supabase secret:**
- In the deploy screen → Advanced settings → Secrets
- Paste this (replace with your real URL):
```toml
DATABASE_URL = "postgresql://postgres.abcdef:YOUR_PASSWORD@pooler.supabase.com:6543/postgres"
```

Your app is now live at:
`https://YOUR_USERNAME-mm-dashboard-streamlit-app-XXXX.streamlit.app`

Share this URL with your team.

---

### 6. Running Chrome fetches from your local machine

OMO data and Treasury yields need Chrome — run these from your local machine.
Your local app will write to the same Supabase DB that the cloud app reads from.

Set the env var locally so your local app uses Supabase too:
```cmd
set DATABASE_URL=postgresql://postgres.abcdef:YOUR_PASSWORD@pooler.supabase.com:6543/postgres
streamlit run streamlit_app.py --server.port 8505
```

Then click "📥 Fetch OMO Data" and "📈 Fetch Yield History" as normal.
The cloud dashboard will reflect the new data within seconds.

---

## What runs where

| Task | Where | How often |
|------|-------|-----------|
| GSOM data (securities, yield curve) | GitHub Actions | Daily 9am auto |
| Auction calendar | GitHub Actions | Daily 9am auto |
| Daily cash flows | GitHub Actions | Daily 9am auto |
| OMO press release PDFs | Your local machine | Manual (click button) |
| Treasury primary yields | Your local machine | Manual (click button) |
| Dashboard UI | Streamlit Cloud | Always on, free |
| Database | Supabase | Always on, free |

---

## Cost breakdown

| Service | Free tier | Your usage |
|---------|-----------|------------|
| Supabase | 500MB, 2 projects | ~50MB ✓ |
| Streamlit Cloud | 1 private app | 1 app ✓ |
| GitHub Actions | 2000 min/month | ~5 min/day = 150 min/month ✓ |
| Vercel (optional) | unlimited | if you add Next.js frontend |

**Total: $0/month**

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
