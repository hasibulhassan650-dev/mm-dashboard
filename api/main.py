"""
api/main.py — FastAPI backend for MM Dashboard.
Reads from Supabase (PostgreSQL). Deployed on Railway.

Endpoints:
  GET /health
  GET /api/omo/outstanding        daily outstanding series by instrument
  GET /api/omo/transactions       raw OMO transaction rows
  GET /api/yields                 primary market yield history
  GET /api/yields/curve           latest yield curve (most recent per tenor)
  GET /api/securities             securities list
  GET /api/flows                  daily net flows (coupons + maturities)
  GET /api/auctions               auction events
"""
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

from routers import omo, yields, securities, flows, callmoney, fx, refrate, meta, policy

app = FastAPI(title="MM Dashboard API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten to Vercel domain after deploy
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(omo.router,         prefix="/api/omo",        tags=["OMO"])
app.include_router(yields.router,      prefix="/api/yields",     tags=["Yields"])
app.include_router(securities.router,  prefix="/api/securities", tags=["Securities"])
app.include_router(flows.router,       prefix="/api/flows",      tags=["Flows"])
app.include_router(callmoney.router,   prefix="/api/callmoney",  tags=["CallMoney"])
app.include_router(fx.router,          prefix="/api/fx",          tags=["FX"])
app.include_router(refrate.router,     prefix="/api/refrate",     tags=["RefRate"])
app.include_router(meta.router,        prefix="/api/meta",        tags=["Meta"])
app.include_router(policy.router,      prefix="/api/policy",      tags=["Policy"])


@app.get("/health")
def health():
    return {"status": "ok"}
