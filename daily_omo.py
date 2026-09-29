"""Daily OMO schedule — raw data from BB PDFs, exactly as parsed."""
import sqlite3, datetime

con = sqlite3.connect('data/mm_dashboard.db')
cur = con.cursor()

cur.execute('SELECT DISTINCT transaction_date FROM omo_transactions ORDER BY transaction_date')
dates = [r[0] for r in cur.fetchall()]

HDR = f"{'INSTRUMENT':<12} {'TENOR':<6} {'AMOUNT (Cr)':>12} {'RATE%':>7}"
SEP = '-' * 42

for d in dates:
    date_obj = datetime.date.fromisoformat(d)
    dow = date_obj.strftime('%a')

    cur.execute('''SELECT instrument, tenor_label, accepted_bdt_crore, rate_pct, direction
                   FROM omo_transactions WHERE transaction_date=?
                   ORDER BY direction DESC, instrument, tenor_label''', (d,))
    rows = cur.fetchall()

    inj_rows = [r for r in rows if r[4] == 'INJECTION']
    abs_rows = [r for r in rows if r[4] == 'ABSORPTION']
    total_inj = sum(r[2] for r in inj_rows)
    total_abs = sum(r[2] for r in abs_rows)

    print(f'\n{"="*55}')
    print(f'  {d}  ({dow})   NET = {total_inj - total_abs:+,.2f} Cr')
    print(f'{"="*55}')

    if inj_rows:
        print(f'  INJECTION (+)')
        print(f'  {HDR}')
        print(f'  {SEP}')
        for instr, tenor, amt, rate, _ in inj_rows:
            rate_s = f'{rate:.2f}%' if rate else '   -'
            print(f'  {instr:<12} {tenor:<6} {amt:>12,.2f} {rate_s:>7}')
        print(f'  {"TOTAL":<12} {"":6} {total_inj:>12,.2f}')

    if abs_rows:
        print(f'  ABSORPTION (-)')
        print(f'  {HDR}')
        print(f'  {SEP}')
        for instr, tenor, amt, rate, _ in abs_rows:
            rate_s = f'{rate:.2f}%' if rate else '   -'
            print(f'  {instr:<12} {tenor:<6} {amt:>12,.2f} {rate_s:>7}')
        print(f'  {"TOTAL":<12} {"":6} {total_abs:>12,.2f}')

con.close()
