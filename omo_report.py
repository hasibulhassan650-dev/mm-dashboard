"""Full OMO outstanding report — per transaction, days to maturity."""
import sqlite3, datetime

con = sqlite3.connect('data/mm_dashboard.db')
cur = con.cursor()

today = datetime.date.today()
today_str = today.isoformat()

INSTR_ORDER = ['CB_REPO','SLF','IBLF','AR','SDF']
INSTR_LABEL = {
    'CB_REPO': 'Central Bank Repo (CB Repo)',
    'SLF':     'Standing Lending Facility (SLF)',
    'IBLF':    'Islami Banks Liquidity Facility (IBLF)',
    'AR':      'Assured Repo (AR)',
    'SDF':     'Standing Deposit Facility (SDF)',
}

print(f"BB Open Market Operations — Outstanding as of {today.strftime('%d %b %Y')}")
print("="*85)

grand_inj = 0
grand_abs = 0

for instr in INSTR_ORDER:
    cur.execute('''
        SELECT transaction_date, tenor_label, tenor_days, accepted_bdt_crore, rate_pct, maturity_date, direction
        FROM omo_transactions
        WHERE instrument=? AND transaction_date<=? AND maturity_date>?
        ORDER BY maturity_date
    ''', (instr, today_str, today_str))
    rows = cur.fetchall()

    if not rows:
        continue

    total = sum(r[3] for r in rows)
    direction = rows[0][6]
    sign = '+INJECT' if direction == 'INJECTION' else '-ABSORB'

    print(f"\n{'─'*85}")
    print(f"  {INSTR_LABEL[instr]}   [{sign}]   Total outstanding: {total:,.2f} Cr")
    print(f"{'─'*85}")
    print(f"  {'TXN DATE':<13} {'TENOR':<6} {'AMOUNT (Cr)':>12} {'RATE%':>7} {'MATURES':>13} {'DAYS LEFT':>10}")
    print(f"  {'-'*13} {'-'*6} {'-'*12} {'-'*7} {'-'*13} {'-'*10}")

    for txn_date, tenor_lbl, tenor_days, amt, rate, mat_date, _ in rows:
        mat_d     = datetime.date.fromisoformat(mat_date)
        days_left = (mat_d - today).days
        rate_str  = f"{rate:.2f}%" if rate else "  —"
        urgency   = " ⚠" if days_left <= 3 else (" !" if days_left <= 7 else "")
        print(f"  {txn_date:<13} {tenor_lbl:<6} {amt:>12,.2f} {rate_str:>7} {mat_date:>13} {days_left:>8}d{urgency}")

    if direction == 'INJECTION':
        grand_inj += total
    else:
        grand_abs += total

print(f"\n{'='*85}")
print(f"  SUMMARY")
print(f"{'='*85}")
print(f"  Total Injection Outstanding : {grand_inj:>12,.2f} Cr  (CB Repo + SLF + IBLF + AR)")
print(f"  Total Absorption Outstanding: {grand_abs:>12,.2f} Cr  (SDF)")
print(f"  NET Liquidity in Market     : {grand_inj-grand_abs:>12,.2f} Cr")

print(f"\n{'='*85}")
print(f"  MATURITY SCHEDULE (next 60 days)")
print(f"{'='*85}")
print(f"  {'DATE':<13} {'INSTRUMENT':<12} {'AMOUNT (Cr)':>12} {'DAYS LEFT':>10}  IMPACT")

cur.execute('''
    SELECT maturity_date, instrument, SUM(accepted_bdt_crore), direction
    FROM omo_transactions
    WHERE maturity_date > ? AND maturity_date <= date(?, '+60 days')
    GROUP BY maturity_date, instrument, direction
    ORDER BY maturity_date, instrument
''', (today_str, today_str))

prev_date = None
for mat_date, instr, amt, direction in cur.fetchall():
    mat_d     = datetime.date.fromisoformat(mat_date)
    days_left = (mat_d - today).days
    impact    = "liquidity leaves market" if direction == 'INJECTION' else "liquidity returns to market"
    sign      = "-" if direction == 'INJECTION' else "+"
    if mat_date != prev_date:
        print()
        prev_date = mat_date
    urgency = " ⚠⚠" if days_left <= 3 else (" ⚠" if days_left <= 7 else "")
    print(f"  {mat_date:<13} {instr:<12} {sign}{amt:>11,.2f} {days_left:>8}d{urgency}   {impact}")

con.close()
