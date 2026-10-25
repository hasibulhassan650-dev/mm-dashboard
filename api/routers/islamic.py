"""The Islamic window: BGIIB auctions.

BB auctions Bangladesh Government Islami Investment Bond weekly, in 3-month
and 6-month tenors, and publishes the result as a press release. Those figures
were captured nowhere until Oct-2026, so an entire instrument was missing from
a dashboard whose first rule is not to miss a product.

Two figures carry the meaning, and they are not interchangeable with anything
on the conventional side of the book. **EPR** is the Expected Profit Rate —
the Islamic-finance counterpart of a yield, and the number to hold against a
T-Bill cut-off of the same tenor. **PSR** is the profit-sharing ratio with the
government ("90:10"): a split, not a rate, and never to be averaged.

The comparison against T-Bills is served alongside, because that is the
question the desk actually asks — but only where both exist on the same day;
nothing is interpolated to manufacture a pair.
"""
import datetime

from fastapi import APIRouter, Query
from fastapi.responses import Response
from sqlalchemy import text

from db import get_session

router = APIRouter()

# A BGIIB tenor against the T-Bill it is naturally compared with.
_COMPARABLE = {"3M": "91D", "6M": "182D"}


@router.get("")
def get_islamic(weeks: int = Query(26, ge=1, le=260)):
    """Recent BGIIB auctions, newest first, with the comparable T-Bill cut-off."""
    session = get_session()
    try:
        today = datetime.date.today()
        since = today - datetime.timedelta(weeks=weeks)
        rows = session.execute(text("""
            SELECT auction_date, tenor_label, tenor_months, bids_offered, offered_crore,
                   bids_accepted, accepted_crore, psr, war_epr_pct, base_rate_range,
                   epr_range, source_serial, source
            FROM bgiib_auctions
            WHERE auction_date >= :since
            ORDER BY auction_date DESC, tenor_months
        """), {"since": str(since)}).fetchall()
        auctions = [dict(r._mapping) for r in rows]

        # BGIIB is auctioned on Tuesdays and T-Bills on Sundays, every week
        # without exception, so there is no such thing as a same-day pair. An
        # exact-date comparison returned nothing at all, which is a useless
        # kind of honest: the desk holds a BGIIB EPR against the last T-Bill
        # cut-off it has seen. So that is what this gives — the most recent
        # cut-off ON OR BEFORE the BGIIB auction, carrying its own date so it
        # can never be read as the same day's print. Nothing is interpolated
        # and nothing after the auction is used.
        bills: dict = {}
        if auctions:
            lo = min(a["auction_date"] for a in auctions) - datetime.timedelta(days=14)
            for r in session.execute(text("""
                SELECT auction_date, tenor_label, cutoff_yield_pct
                FROM primary_yield_snapshots
                WHERE auction_date >= :lo AND tenor_label IN ('91D', '182D', '364D')
                  AND cutoff_yield_pct > 0
                ORDER BY auction_date
            """), {"lo": str(lo)}).fetchall():
                bills.setdefault(r[1], []).append((r[0], float(r[2])))

        for a in auctions:
            want = _COMPARABLE.get(a["tenor_label"])
            prior = [(d, v) for d, v in bills.get(want, [])
                     if d <= a["auction_date"]] if want else []
            bill_date, bill = prior[-1] if prior else (None, None)
            a["comparable_tbill_tenor"] = want
            a["comparable_tbill_cutoff_pct"] = bill
            a["comparable_tbill_date"] = str(bill_date) if bill_date else None
            a["epr_minus_tbill_bps"] = (
                round((float(a["war_epr_pct"]) - bill) * 100, 1)
                if (bill is not None and a["war_epr_pct"] is not None) else None)

        by_tenor = {}
        for a in auctions:
            t = by_tenor.setdefault(a["tenor_label"], {
                "tenor_label": a["tenor_label"], "auctions": 0,
                "accepted_crore": 0.0, "latest_epr_pct": None, "latest_date": None})
            t["auctions"] += 1
            t["accepted_crore"] = round(t["accepted_crore"] + (a["accepted_crore"] or 0), 2)
            if t["latest_date"] is None:
                t["latest_date"] = str(a["auction_date"])[:10]
                t["latest_epr_pct"] = a["war_epr_pct"]

        # BGIS Sukuk, from BB's outstanding table. Never allowed to take the BGIIB
        # half down with it: a failed sukuk query is reported, not raised.
        try:
            from .sukuk_logic import sukuk_block
            sukuk = sukuk_block(session, today)
        except Exception as exc:
            session.rollback()
            sukuk = {"error": f"{type(exc).__name__}: {exc}"[:200]}

        return {
            "as_of": str(today),
            "weeks": weeks,
            "sukuk": sukuk,
            "count": len(auctions),
            "latest_auction": str(auctions[0]["auction_date"])[:10] if auctions else None,
            "by_tenor": [by_tenor[k] for k in sorted(by_tenor)],
            "auctions": auctions,
        }
    finally:
        session.close()


XLSX_MEDIA = ("application/vnd.openxmlformats-officedocument."
              "spreadsheetml.sheet")


@router.get("/export")
def export_islamic(weeks: int = Query(104, ge=1, le=520)):
    """The Islamic window as a workbook.

    Built because the dataset existed and the export hub did not list it — a
    series the desk can see on screen but cannot take away is half a feature.
    Carries the T-Bill comparison with ITS OWN date in a separate column, so
    nobody reading the spreadsheet mistakes it for a same-day print.
    """
    from .export_logic import build_workbook, filename

    p = get_islamic(weeks=weeks)
    rows = [{
        "Auction Date": a["auction_date"],
        "Tenor": a["tenor_label"],
        "Bids Offered": a["bids_offered"],
        "Offered (Cr)": a["offered_crore"],
        "Bids Accepted": a["bids_accepted"],
        "Accepted (Cr)": a["accepted_crore"],
        "Profit Sharing Ratio": a["psr"],
        "Wtd Avg EPR %": a["war_epr_pct"],
        "Base Rate Range": a["base_rate_range"],
        "EPR Range": a["epr_range"],
        "Comparable T-Bill": a["comparable_tbill_tenor"],
        "T-Bill Cut-off %": a["comparable_tbill_cutoff_pct"],
        "T-Bill Auction Date": a["comparable_tbill_date"],
        "EPR less T-Bill (bp)": a["epr_minus_tbill_bps"],
        "BB Release": a["source_serial"],
        "Source": a["source"],
    } for a in p["auctions"]]
    summary = [{
        "Tenor": t["tenor_label"], "Auctions": t["auctions"],
        "Accepted (Cr)": t["accepted_crore"],
        "Latest EPR %": t["latest_epr_pct"], "Latest Auction": t["latest_date"],
    } for t in p["by_tenor"]]

    sk = p.get("sukuk") or {}
    sukuk_rows = [{
        "ISIN": i["isin"], "Project": i["project"], "Tenor": i["tenor_label"],
        "Contract": i["contract"], "Issue Date": i["issue_date"], "Maturity Date": i["maturity_date"],
        "Profit/Rental Rate %": i["profit_rate_pct"], "Frequency": i["profit_frequency"],
        "Tradability": i["tradability"], "Outstanding (Cr)": i["outstanding_crore"],
    } for i in sk.get("outstanding", [])]
    upcoming_rows = [{
        "Scheduled Date": u["date"], "ISIN": u["isin"], "Project": u["project"], "Kind": u["kind"],
        "Amount (Cr)": u["amount_crore"],
    } for u in sk.get("upcoming", [])]

    body = build_workbook(
        "Bangladesh Islamic Window — BGIIB and BGIS Sukuk",
        [("BGIIB auctions", rows), ("By tenor", summary),
         ("BGIS Sukuk", sukuk_rows), ("Sukuk next 12m", upcoming_rows)],
        facts=[("Window", f"last {weeks} weeks to {p['as_of']}"),
               ("Latest auction", p["latest_auction"] or "none"),
               ("Unit", "BDT crore; EPR and cut-offs in per cent"),
               ("Source", "Bangladesh Bank — BGIIB auction result press releases")],
        caveats=[
            "EPR is the Expected Profit Rate, the Islamic-finance counterpart of a yield "
            "and the figure to hold against a T-Bill cut-off of the same tenor.",
            "PSR (e.g. 90:10) is the profit-sharing ratio with the government. It is a "
            "SPLIT, not a rate: do not average it or chart it as a yield.",
            "The T-Bill comparison is the last comparable cut-off ON OR BEFORE each BGIIB "
            "auction, and its own auction date is in the next column. BGIIB is auctioned "
            "on Tuesdays and T-Bills on Sundays, so a same-day pair does not exist and "
            "nothing here is interpolated.",
            "BGIS Sukuk: outstanding as BB's own table states it. 'Sukuk next 12m' dates are "
            "SCHEDULED dates (cash settles on the next working day). Profit is derived only for "
            "issues BB lists as half-yearly (outstanding x rate / 2); issues paying profit on "
            "maturity show principal only, because that lump sum is not derivable.",
            "Every release is checked against BB's own Total line before being stored; a "
            "release whose rows do not add up is refused whole rather than misstating the "
            "size of the window.",
        ],
    )
    return Response(content=body, media_type=XLSX_MEDIA, headers={
        "Content-Disposition":
            f'attachment; filename="{filename("bd_bgiib_islamic_window", p["as_of"])}"'})
