## Weekly deep audit (full history)
_2026-09-29 14:48 UTC_

### ✅ OMO ledger vs BB's printed maturities — clean
_every live tranche maturing on a day must equal what BB printed as maturing_

### ✅ Liquidity ladder vs its source events — clean
_daily_net_flow must equal a fresh recompute from coupon/maturity/auction rows_

### 1 known exception(s) — understood, not ours to fix
- 2026-05-17 AR: our tranches 1,773 cr vs BB printed 1,657 cr — phantom tranche · KNOWN: BB matured the 19-Apr AR 28D on 18-May, a day after T+28 (verified against the press releases in the Sep-2026 audit)
