# BD Money Market Dashboard — Production Deployment Guide

Turn this into a real website your team can access from any browser.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        Your Team's Browsers                     │
└────────────────────────────┬────────────────────────────────────┘
                             │
                    Next.js Frontend
                   (Vercel — free tier)
                   dashboard.yourorg.com
                             │
                    FastAPI Backend
                 (Railway — ~$5/month)
                   api.yourorg.com
                        │        │
              PostgreSQL DB    Python Pipeline
             (Supabase free)   (same Railway server)
                             │
                    Chrome (OMO + Treasury fetcher)
                    runs as scheduled jobs on Railway
```

**Why this stack:**
- **FastAPI** — wraps all your existing Python code, zero rewrite
- **PostgreSQL on Supabase** — free, production-grade, replaces SQLite
- **Next.js + Tailwind + shadcn/ui** — professional dashboard look
- **Vercel** — free frontend hosting, global CDN
- **Railway** — cheap server that can run Chrome for data fetching

Estimated cost: **$0–$7/month**
Build time: **3–5 days** if you follow this guide step by step

---

## Step 1 — Migrate Database to PostgreSQL

### 1.1 Create a Supabase project

1. Go to https://supabase.com → New Project
2. Note down your **Connection string** (looks like `postgresql://postgres:PASSWORD@db.xxx.supabase.co:5432/postgres`)

### 1.2 Update your code to support both SQLite and PostgreSQL

Install psycopg2:
```bash
pip install psycopg2-binary asyncpg
```

Update `config.py`:
```python
import os

# Use DATABASE_URL env var in production, fall back to SQLite locally
DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL:
    # Railway/Supabase injects DATABASE_URL automatically
    # Fix for SQLAlchemy: replace postgres:// with postgresql://
    DB_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
else:
    DB_URL = f"sqlite:///{DB_PATH}"
```

Update `db.py` — the WAL mode pragma only applies to SQLite:
```python
@event.listens_for(Engine, "connect")
def set_pragmas(dbapi_conn, _):
    # WAL mode only for SQLite
    if "sqlite" in str(dbapi_conn):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
```

### 1.3 Migrate existing SQLite data to Postgres

```bash
pip install pgloader
pgloader sqlite:///data/mm_dashboard.db postgresql://USER:PASS@HOST/DB
```

Or export/import manually:
```python
# run_once: migrate_to_postgres.py
from sqlalchemy import create_engine
import pandas as pd

sqlite_engine = create_engine("sqlite:///data/mm_dashboard.db")
pg_engine     = create_engine("YOUR_SUPABASE_URL")

for table in ["securities", "mtm_snapshots", "coupon_events", "maturity_events",
              "auction_events", "primary_yield_snapshots", "omo_transactions",
              "daily_net_flow", "holiday_calendar"]:
    df = pd.read_sql_table(table, sqlite_engine)
    df.to_sql(table, pg_engine, if_exists="replace", index=False)
    print(f"Migrated {table}: {len(df)} rows")
```

---

## Step 2 — Build the FastAPI Backend

Create `api/main.py`:

```python
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker
import os, datetime

from db import (Security, MtmSnapshot, AuctionEvent, CouponEvent,
                MaturityEvent, DailyNetFlow, PrimaryYieldSnapshot, OMOTransaction)

DB_URL = os.environ.get("DATABASE_URL", "sqlite:///data/mm_dashboard.db")
engine = create_engine(DB_URL.replace("postgres://","postgresql://",1))
Session = sessionmaker(bind=engine)

app = FastAPI(title="BD Money Market API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # restrict to your domain in production
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Daily flows ──────────────────────────────────────────────────
@app.get("/api/daily-flows")
def get_daily_flows(days: int = 90):
    session = Session()
    cutoff  = datetime.date.today() - datetime.timedelta(days=days)
    rows    = session.query(DailyNetFlow).filter(
        DailyNetFlow.flow_date >= cutoff
    ).order_by(DailyNetFlow.flow_date).all()
    session.close()
    return [{"date": str(r.flow_date),
             "maturity_inflow": r.principal_inflow_bdt_mill,
             "coupon_inflow":   r.coupon_inflow_bdt_mill,
             "auction_outflow": r.auction_outflow_best_mill,
             "net_borrowing":   r.net_borrowing_bdt_mill} for r in rows]

# ── Yield curve ──────────────────────────────────────────────────
@app.get("/api/yield-curve/secondary")
def get_secondary_yield_curve():
    session = Session()
    latest  = session.query(func.max(MtmSnapshot.settlement_date)).scalar()
    rows    = session.query(MtmSnapshot, Security).join(
        Security, MtmSnapshot.isin == Security.isin
    ).filter(MtmSnapshot.settlement_date == latest).all()
    session.close()
    today = datetime.date.today()
    data  = []
    for snap, sec in rows:
        if snap.market_yield_pct and sec.maturity_date:
            rem = (sec.maturity_date - today).days / 365.25
            data.append({"isin": snap.isin, "name": sec.security_name_norm,
                         "type": sec.security_type, "rem_years": round(rem, 3),
                         "yield_pct": snap.market_yield_pct})
    return sorted(data, key=lambda x: x["rem_years"])

@app.get("/api/yield-curve/primary")
def get_primary_yield_curve():
    session = Session()
    rows    = session.query(PrimaryYieldSnapshot).filter(
        PrimaryYieldSnapshot.cutoff_yield_pct.isnot(None)
    ).order_by(PrimaryYieldSnapshot.tenor_label, PrimaryYieldSnapshot.auction_date).all()
    session.close()
    return [{"tenor": r.tenor_label, "tenor_years": r.tenor_years,
             "yield_pct": r.cutoff_yield_pct, "auction_date": str(r.auction_date),
             "type": r.security_type} for r in rows]

# ── OMO ─────────────────────────────────────────────────────────
@app.get("/api/omo/transactions")
def get_omo_transactions(days: int = 60):
    session = Session()
    cutoff  = datetime.date.today() - datetime.timedelta(days=days)
    rows    = session.query(OMOTransaction).filter(
        OMOTransaction.transaction_date >= cutoff
    ).order_by(OMOTransaction.transaction_date).all()
    session.close()
    return [{"transaction_date": str(r.transaction_date),
             "maturity_date":    str(r.maturity_date),
             "instrument":       r.instrument,
             "tenor_label":      r.tenor_label,
             "tenor_days":       r.tenor_days,
             "amount_crore":     r.accepted_bdt_crore,
             "rate_pct":         r.rate_pct,
             "direction":        r.direction} for r in rows]

# ── Securities ───────────────────────────────────────────────────
@app.get("/api/securities")
def get_securities():
    session = Session()
    rows    = session.query(Security).order_by(Security.maturity_date).all()
    session.close()
    return [{"isin": r.isin, "name": r.security_name_norm, "type": r.security_type,
             "maturity_date": str(r.maturity_date), "coupon_pct": r.coupon_rate_pct,
             "outstanding_mill": r.outstanding_bdt_mill} for r in rows]

# ── Trigger pipeline (protected by API key) ──────────────────────
@app.post("/api/admin/refresh")
def trigger_refresh(api_key: str = Query(...)):
    if api_key != os.environ.get("ADMIN_KEY", "changeme"):
        return {"error": "unauthorized"}, 401
    from engines.pipeline import run_pipeline
    summary = run_pipeline()
    return summary
```

Create `api/requirements.txt`:
```
fastapi==0.115.0
uvicorn[standard]==0.34.0
sqlalchemy==2.0.36
psycopg2-binary==2.9.10
python-dotenv==1.0.1
pdfplumber==0.11.4
beautifulsoup4==4.12.3
lxml==5.3.0
undetected-chromedriver==3.5.5
selenium==4.27.1
httpx==0.28.1
```

Test locally:
```bash
pip install fastapi uvicorn
uvicorn api.main:app --reload --port 8000
# Visit http://localhost:8000/docs for auto-generated API docs
```

---

## Step 3 — Build the Next.js Frontend

### 3.1 Scaffold the project

```bash
npx create-next-app@latest mm-web --typescript --tailwind --app
cd mm-web
npx shadcn@latest init
npx shadcn@latest add card badge tabs button table
npm install recharts lucide-react @tanstack/react-query axios date-fns
```

### 3.2 Project structure

```
mm-web/
├── app/
│   ├── layout.tsx          # root layout, navbar
│   ├── page.tsx            # home → redirect to /dashboard
│   └── dashboard/
│       ├── page.tsx        # main dashboard
│       ├── yield-curve/page.tsx
│       ├── omo/page.tsx
│       └── securities/page.tsx
├── components/
│   ├── DailyFlowChart.tsx
│   ├── YieldCurveChart.tsx
│   ├── OmoOutstandingChart.tsx
│   ├── OmoNetChart.tsx
│   └── Navbar.tsx
├── lib/
│   └── api.ts              # typed API client
└── .env.local
```

### 3.3 `.env.local`

```env
NEXT_PUBLIC_API_URL=http://localhost:8000
```

### 3.4 `lib/api.ts` — typed API client

```typescript
const BASE = process.env.NEXT_PUBLIC_API_URL;

export async function getDailyFlows(days = 90) {
  const res = await fetch(`${BASE}/api/daily-flows?days=${days}`);
  return res.json();
}
export async function getSecondaryYieldCurve() {
  const res = await fetch(`${BASE}/api/yield-curve/secondary`);
  return res.json();
}
export async function getPrimaryYieldCurve() {
  const res = await fetch(`${BASE}/api/yield-curve/primary`);
  return res.json();
}
export async function getOmoTransactions(days = 60) {
  const res = await fetch(`${BASE}/api/omo/transactions?days=${days}`);
  return res.json();
}
export async function getSecurities() {
  const res = await fetch(`${BASE}/api/securities`);
  return res.json();
}
```

### 3.5 `app/layout.tsx` — professional navbar

```tsx
import { Inter } from "next/font/google";
import Link from "next/link";
import "./globals.css";

const inter = Inter({ subsets: ["latin"] });

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className={`${inter.className} bg-slate-950 text-slate-50 min-h-screen`}>
        <nav className="border-b border-slate-800 bg-slate-900/80 backdrop-blur sticky top-0 z-50">
          <div className="max-w-7xl mx-auto px-6 h-14 flex items-center gap-8">
            <span className="font-bold text-lg tracking-tight">
              🏦 BD Money Market
            </span>
            <div className="flex gap-6 text-sm text-slate-400">
              <Link href="/dashboard"       className="hover:text-white transition-colors">Dashboard</Link>
              <Link href="/dashboard/yield-curve" className="hover:text-white transition-colors">Yield Curve</Link>
              <Link href="/dashboard/omo"         className="hover:text-white transition-colors">OMO</Link>
              <Link href="/dashboard/securities"  className="hover:text-white transition-colors">Securities</Link>
            </div>
            <div className="ml-auto text-xs text-slate-500">
              Data: Bangladesh Bank
            </div>
          </div>
        </nav>
        <main className="max-w-7xl mx-auto px-6 py-8">{children}</main>
      </body>
    </html>
  );
}
```

### 3.6 `components/OmoOutstandingChart.tsx` — example chart

```tsx
"use client";
import { useMemo } from "react";
import { AreaChart, Area, XAxis, YAxis, CartesianGrid,
         Tooltip, Legend, ResponsiveContainer, ReferenceLine } from "recharts";
import { format } from "date-fns";

const COLOURS = {
  CB_REPO: "#3b82f6",
  SLF:     "#14b8a6",
  IBLF:    "#8b5cf6",
  AR:      "#f59e0b",
  SDF:     "#ef4444",
};

const INSTRUMENTS = ["CB_REPO","SLF","IBLF","AR","SDF"] as const;

interface Txn {
  transaction_date: string;
  maturity_date:    string;
  instrument:       string;
  amount_crore:     number;
  direction:        string;
}

export function OmoOutstandingChart({ transactions }: { transactions: Txn[] }) {
  const today = new Date();

  // Build daily outstanding series
  const series = useMemo(() => {
    const start = new Date(today);
    start.setDate(start.getDate() - 28);
    const end   = new Date(today);
    end.setDate(end.getDate() + 14);

    const days: Record<string, any>[] = [];
    for (let d = new Date(start); d <= end; d.setDate(d.getDate() + 1)) {
      const dStr = d.toISOString().split("T")[0];
      const row: Record<string, any> = { date: dStr };
      for (const instr of INSTRUMENTS) {
        row[instr] = transactions
          .filter(t =>
            t.instrument === instr &&
            t.transaction_date <= dStr &&
            t.maturity_date    >  dStr
          )
          .reduce((sum, t) => sum + (t.amount_crore || 0), 0);
      }
      days.push(row);
    }
    return days;
  }, [transactions]);

  return (
    <ResponsiveContainer width="100%" height={400}>
      <AreaChart data={series}>
        <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
        <XAxis dataKey="date"
               tickFormatter={v => format(new Date(v), "dd-MMM")}
               tick={{ fill: "#94a3b8", fontSize: 11 }} />
        <YAxis tickFormatter={v => `${(v/1000).toFixed(0)}k`}
               tick={{ fill: "#94a3b8", fontSize: 11 }} />
        <Tooltip
          contentStyle={{ background: "#0f172a", border: "1px solid #334155" }}
          formatter={(val: any, name: string) => [`${Number(val).toLocaleString()} Cr`, name]}
          labelFormatter={v => format(new Date(v), "dd MMM yyyy")} />
        <Legend />
        <ReferenceLine x={today.toISOString().split("T")[0]}
                       stroke="#64748b" strokeDasharray="4 4" label={{ value: "Today", fill: "#94a3b8", fontSize: 11 }} />
        {INSTRUMENTS.filter(i => i !== "SDF").map(instr => (
          <Area key={instr} type="monotone" dataKey={instr}
                stackId="injection" stroke={COLOURS[instr]}
                fill={COLOURS[instr]} fillOpacity={0.3} />
        ))}
        <Area key="SDF" type="monotone" dataKey="SDF"
              stackId="absorption" stroke={COLOURS.SDF}
              fill={COLOURS.SDF} fillOpacity={0.3} />
      </AreaChart>
    </ResponsiveContainer>
  );
}
```

### 3.7 `app/dashboard/omo/page.tsx`

```tsx
import { getOmoTransactions } from "@/lib/api";
import { OmoOutstandingChart } from "@/components/OmoOutstandingChart";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export const revalidate = 300;  // ISR: refresh every 5 minutes

export default async function OmoPage() {
  const transactions = await getOmoTransactions(60);

  const today = new Date().toISOString().split("T")[0];
  const active = transactions.filter((t: any) =>
    t.transaction_date <= today && t.maturity_date > today
  );

  const totalInjection = active
    .filter((t: any) => t.direction === "INJECTION")
    .reduce((s: number, t: any) => s + t.amount_crore, 0);

  const totalAbsorption = active
    .filter((t: any) => t.direction === "ABSORPTION")
    .reduce((s: number, t: any) => s + t.amount_crore, 0);

  const net = totalInjection - totalAbsorption;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Open Market Operations</h1>

      {/* KPI row */}
      <div className="grid grid-cols-3 gap-4">
        <Card className="bg-slate-900 border-slate-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-slate-400">Total Injection</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold text-green-400">
              ৳{totalInjection.toLocaleString()} Cr
            </p>
            <p className="text-xs text-slate-500">CB Repo + SLF + IBLF + AR</p>
          </CardContent>
        </Card>

        <Card className="bg-slate-900 border-slate-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-slate-400">Total Absorption</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold text-red-400">
              ৳{totalAbsorption.toLocaleString()} Cr
            </p>
            <p className="text-xs text-slate-500">SDF</p>
          </CardContent>
        </Card>

        <Card className="bg-slate-900 border-slate-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-slate-400">Net Position</CardTitle>
          </CardHeader>
          <CardContent>
            <p className={`text-2xl font-bold ${net > 0 ? "text-green-400" : "text-red-400"}`}>
              {net > 0 ? "▲" : "▼"} ৳{Math.abs(net).toLocaleString()} Cr
            </p>
            <p className="text-xs text-slate-500">
              {net > 0 ? "Net Injecting" : "Net Absorbing"}
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Chart */}
      <Card className="bg-slate-900 border-slate-800">
        <CardHeader>
          <CardTitle>Outstanding Liquidity by Instrument</CardTitle>
          <p className="text-sm text-slate-400">
            Cumulative active transactions — shaded area = future maturities
          </p>
        </CardHeader>
        <CardContent>
          <OmoOutstandingChart transactions={transactions} />
        </CardContent>
      </Card>
    </div>
  );
}
```

---

## Step 4 — Set Up Scheduled Data Fetching

On the Railway server, create a cron job that runs the pipeline daily:

`jobs/daily_pipeline.py`:
```python
"""Run by cron every day at 8:00 AM Bangladesh time."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from engines.pipeline import run_pipeline
from engines.pipeline import run_omo_fetch

if __name__ == "__main__":
    print("Running daily pipeline...")
    summary = run_pipeline()
    print("Pipeline:", summary)

    print("Running OMO fetch...")
    omo = run_omo_fetch(days_back=5, max_files=5)
    print("OMO:", omo)
```

`Procfile` (Railway reads this):
```
web: uvicorn api.main:app --host 0.0.0.0 --port $PORT
```

`railway.json`:
```json
{
  "$schema": "https://railway.app/railway.schema.json",
  "build": {
    "builder": "NIXPACKS"
  },
  "deploy": {
    "startCommand": "uvicorn api.main:app --host 0.0.0.0 --port $PORT",
    "cronJobs": [
      {
        "schedule": "0 2 * * *",
        "command": "python jobs/daily_pipeline.py"
      }
    ]
  }
}
```

---

## Step 5 — Deploy

### 5.1 Backend on Railway

```bash
# Install Railway CLI
npm install -g @railway/cli
railway login

# In your mm_dashboard folder
railway init
railway add --database postgresql     # creates a PG database, sets DATABASE_URL
railway up                            # deploys the FastAPI app

# Set environment variables
railway variables set ADMIN_KEY=your-secret-key-here
```

Your API is now live at `https://mm-dashboard-production.up.railway.app`

### 5.2 Frontend on Vercel

```bash
cd mm-web
npm install -g vercel
vercel

# Set environment variable
vercel env add NEXT_PUBLIC_API_URL
# Enter: https://mm-dashboard-production.up.railway.app
```

Your website is now live at `https://mm-web.vercel.app`

### 5.3 Custom domain (optional)

In Vercel dashboard:
- Settings → Domains → Add `dashboard.yourcompany.com`
- Add a CNAME record in your DNS pointing to `cname.vercel-dns.com`

---

## Step 6 — Password Protection (for team access)

The simplest approach — add HTTP Basic Auth to the Next.js middleware:

`mm-web/middleware.ts`:
```typescript
import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

export function middleware(request: NextRequest) {
  const auth = request.headers.get("authorization");

  if (auth) {
    const [, base64] = auth.split(" ");
    const [user, pass] = atob(base64).split(":");
    if (user === "team" && pass === process.env.DASHBOARD_PASSWORD) {
      return NextResponse.next();
    }
  }

  return new NextResponse("Authentication required", {
    status: 401,
    headers: { "WWW-Authenticate": 'Basic realm="BD Money Market"' },
  });
}

export const config = { matcher: "/((?!api|_next).*)" };
```

Add to Vercel:
```bash
vercel env add DASHBOARD_PASSWORD
# Enter: your-team-password
```

Everyone on your team uses username `team` + the password you set.

---

## Step 7 — Final Checklist Before Sharing

```
□ Railway API returns data at /api/daily-flows
□ Vercel frontend connects to Railway API (no CORS errors)
□ Password protection works (browser prompts for credentials)
□ Daily cron pipeline runs without errors (check Railway logs)
□ OMO fetch works on the server (Chrome installed on Railway — see note)
□ Custom domain set up (optional but professional)
□ Share URL + credentials with team
```

### Note on Chrome for OMO/Treasury fetching on Railway

Railway uses Linux containers. Install Chrome by adding to `nixpacks.toml`:
```toml
[phases.setup]
nixPkgs = ["google-chrome-stable", "chromedriver"]
```

Or use Playwright instead of undetected_chromedriver (Playwright has better Linux support):
```bash
pip install playwright
playwright install chromium
```

---

## Quick Start Summary

| Step | Time | Command |
|------|------|---------|
| Create Supabase DB | 10 min | supabase.com → new project |
| Add FastAPI layer | 1–2 hr | copy `api/main.py` above |
| Scaffold Next.js | 30 min | `npx create-next-app` |
| Build chart components | 2–4 hr | copy components above, extend |
| Deploy Railway | 30 min | `railway up` |
| Deploy Vercel | 15 min | `vercel` |
| Add password | 15 min | copy middleware above |
| **Total** | **~1 day** | |
