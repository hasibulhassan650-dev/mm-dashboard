"""News and BB's FX-auction table must be read against each other, and the press must never become BB data.

Live case (Nov 2026): the press reported BB selling $100m on 16-Nov and $220m on 18-Nov while BB's FX auction page still
ended at 12-Nov; nothing said so, and the news-event 'already held' test (any sale within +-3 days) hid it."""
import datetime, importlib.util, pathlib
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
import db as dbmod

_P = pathlib.Path(__file__).resolve().parents[1] / "api" / "routers" / "reconcile_logic.py"
_sp = importlib.util.spec_from_file_location("reconcile_logic", _P)
R = importlib.util.module_from_spec(_sp)
_sp.loader.exec_module(R)
D = datetime.date
TODAY = D(2026, 11, 21)


def _world(bb, news, fx_ok_utc=datetime.datetime(2026, 11, 20, 20, 0)):
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    for i, (d, typ, amt) in enumerate(bb):
        s.add(dbmod.FxAuctionResult(auction_date=d, settlement_date=d, auction_type=typ, accepted_amount_usd_mill=amt,
                                    ingested_utc=datetime.datetime(2026, 11, 15))) if hasattr(dbmod, "FxAuctionResult") else None
    for i, (pub, title) in enumerate(news):
        s.add(dbmod.NewsArticle(url=f"https://n/{i}", source="x", published_utc=pub, title=title, fetched_utc=pub))
    if fx_ok_utc:
        s.add(dbmod.StepRun(run_utc=fx_ok_utc, machine="pc", step="fx", status="ok"))
    s.commit()
    return s


def _kinds(rows):
    return [(r["verdict"], r["date"]) for r in rows]


def test_a_sale_the_press_reports_but_bb_has_not_listed_is_waiting_and_never_stored():
    s = _world([(D(2026, 11, 12), "Sell", 200.0)],
               [(datetime.datetime(2026, 11, 18, 10), "Bangladesh Bank sells $220m from reserves at Tk123.90 per dollar"),
                (datetime.datetime(2026, 11, 18, 12), "BB sells another $220m to stabilise forex market")])
    before = s.execute(text("SELECT COUNT(*) FROM fx_auction_results")).scalar()
    rows = R.reconcile_fx_news(s, TODAY)
    assert _kinds(rows) == [("WAITING", "2026-11-18")] and len(rows[0]["urls"]) == 2      # two outlets, one sale
    assert s.execute(text("SELECT COUNT(*) FROM fx_auction_results")).scalar() == before   # news never becomes BB data


def test_the_same_sale_with_bbs_amount_is_a_match_and_a_lone_outlet_that_differs_is_noise():
    s = _world([(D(2026, 11, 12), "Sell", 200.0)],
               [(datetime.datetime(2026, 11, 12, 12), "BB sells $200m to stabilise forex market"),
                (datetime.datetime(2026, 11, 12, 11), "Bangladesh Bank sells $205m to banks, dollar rate falls below Tk125")])
    assert _kinds(R.reconcile_fx_news(s, TODAY)) == [("MATCH", "2026-11-12")]


def test_when_no_outlet_agrees_with_bbs_row_it_is_a_conflict_not_a_silent_match():
    s = _world([(D(2026, 11, 12), "Sell", 200.0)], [(datetime.datetime(2026, 11, 12, 12), "BB sells $250m to banks")])
    assert _kinds(R.reconcile_fx_news(s, TODAY)) == [("CONFLICT", "2026-11-12")]


def test_a_story_a_day_after_the_auction_still_matches():
    s = _world([(D(2026, 11, 12), "Sell", 200.0)], [(datetime.datetime(2026, 11, 14, 3), "BB sells $200m through auction")])
    assert _kinds(R.reconcile_fx_news(s, TODAY)) == [("MATCH", "2026-11-14")]


def test_our_own_stale_fetch_is_not_reported_as_bb_lagging():
    s = _world([], [(datetime.datetime(2026, 11, 18, 10), "BB sells $220m from reserves")],
               fx_ok_utc=datetime.datetime(2026, 11, 10))
    assert R.reconcile_fx_news(s, TODAY) == []


def test_a_sale_still_missing_after_bb_posts_later_auctions_or_a_long_wait_is_unconfirmed():
    s = _world([(D(2026, 11, 20), "Sell", 50.0)], [(datetime.datetime(2026, 11, 16, 10), "BB sells $100m from reserves")])
    assert _kinds(R.reconcile_fx_news(s, TODAY)) == [("UNCONFIRMED", "2026-11-16")]


def test_headlines_without_one_clear_dollar_amount_are_not_claims():
    for t in ("BB sells $100m and $200m", "Bangladesh Bank sells bonds", "Dollar sells at Tk125", "BB sells $1.2 billion of notes"):
        assert R.claim_from_title(t) is None, t
    assert R.claim_from_title("bb sells 100m dollar hits record tk") == ("sell", 100.0)


def test_the_press_never_turns_the_build_red(monkeypatch):
    import validate
    s = _world([(D(2026, 11, 12), "Sell", 200.0)], [(datetime.datetime(2026, 11, 18, 10), "BB sells $220m from reserves")])
    monkeypatch.setattr(validate, "get_session", lambda: s)
    v = validate.integrity_check()
    assert not [i for i in v["issues"] if i.startswith("fx-news")]
    assert any(w.startswith("fx-news") for w in v["waiting"])

