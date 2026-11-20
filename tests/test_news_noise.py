"""Liquidity Watch noise: every test here is named for the harm it prevents.

Live, on 19-Nov-2026, "injected this month" read 13,430 crore. The true act was one stake purchase of 715 crore:
12,000 was a running total ("around Tk 12,000 crore from its deposit protection fund"), and 715 was counted twice
(two papers, differently worded counterparties). Scheme sizes and one bank's enrolment in a scheme were announced as
if they were cash. And the opposite harm - a genuine act dropped by an over-eager rule - is guarded by
test_no_genuine_event_is_lost over a frozen snapshot of the real events.
"""
import datetime
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.append(os.path.join(_ROOT, "api", "routers"))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import db as dbmod
from db import DataChange, LiquidityEvent, LiquidityEventHistory
from engines import announce as A
from engines import bangla as BN
from engines import liquidity_news as L
from engines import news_rules as R
import news_logic as NL

D = datetime.date
NOW = datetime.datetime(2026, 11, 20, 12, 0, 0)
TODAY = D(2026, 11, 20)


def _mem():
    eng = create_engine("sqlite:///:memory:")
    dbmod.Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)()


def _read(quote, kind="BB_REFINANCE", amount=None, status="DISBURSED", url="https://x/1", day=18, cp=None,
          cumulative=False, confidence=0.9):
    """One reading of one article through the real validator: (event, article)."""
    pub = datetime.datetime(2026, 11, day, 8)
    art = {"url": url, "source": "outlet", "title": "t", "summary": "", "published_utc": pub,
           "body_text": quote + " Officials gave no further detail on the matter."}
    raw = {"kind": kind, "direction": L.KINDS[kind], "amount_crore": amount, "is_cumulative": cumulative,
           "event_date": None, "announced_date": None, "status": status, "channel": None, "counterparty": cp,
           "summary": quote[:200], "evidence_quote": quote, "confidence": confidence}
    ev, why = L.validate_event(raw, art)
    assert ev is not None, why
    return ev, art


def _store(s, quote, verified_by="llm", **kw):
    """What news_triage._store_events does for one event: merge_or_create, then link_story for a created row."""
    ev, art = _read(quote, **kw)
    row, how = L.merge_or_create(s, ev, art, NOW, verified_by=verified_by)
    if how == "created":
        L.link_story(s, row, now=NOW)
    s.commit()
    return row


STAKE_17 = ("State-owned institutions, including ICB, Sonali Bank, Janata Bank, Agrani Bank, and Rupali Bank, "
            "collectively acquired a 60 percent stake for Tk 715 crore.")
STAKE_21 = ("Five state-owned financial institutions -- ICB, Sonali Bank, Janata Bank, Agrani Bank and Rupali Bank -- "
            "jointly acquired a 60 percent stake for Tk 715 crore.")
SCHEME = "Bangladesh Bank has established a Tk 10,000 crore refinancing scheme for agriculture, agro-based industry and CMSMEs."
POOL = ("In addition, the Bangladesh Bank has provided around Tk12,000 crore from its deposit protection fund "
        "to facilitate the repayment of depositors of the merged banks.")
MID_OCT = "By mid-October, banks disbursed about Tk 12,000 crore, a fifth of the total, to help struggling businesses."
JOIN = ("ONE Bank PLC has signed a participation agreement with Bangladesh Bank under the central bank's "
        "Tk 2,000 crore Pre-Finance Fund for the Export-Oriented Frozen Food Sector.")


def _month_inject(s):
    return NL.news_block(s, today=TODAY)["totals"]["month_inject_crore"]


class TestARunningTotalIsNeverAPayment:
    def test_disbursed_by_mid_october_from_any_reader_is_never_summed(self):
        """A model said is_cumulative=True for this sentence by luck; no rule guaranteed it. A model (or the rule reader)
        that says False must be overruled by the code, or 12,000 crore of 'disbursed so far' joins the month's total."""
        ev, _ = _read(MID_OCT, amount=12000, cumulative=False)
        assert ev["is_cumulative"] is True
        s = _mem()
        _store(s, MID_OCT, amount=12000, cumulative=False)
        assert _month_inject(s) == 0.0

    def test_period_words_that_were_missing(self):
        for q in ("Banks disbursed Tk 5,000 crore by the end of October.", "Banks disbursed Tk 5,000 crore by early November.",
                  "Banks have disbursed Tk 5,000 crore thus far.", "Disbursed Tk 5,000 crore since the launch of the scheme."):
            assert R._PERIOD.search(q), q
        assert not R._PERIOD.search("The ministry released Tk 5,000 crore by the end of the day.")

    def test_an_approximate_amount_from_a_fund_with_no_date_is_a_running_total(self):
        """'around Tk 12,000 crore from its deposit protection fund' is the fund's total to date. A release that names
        its day, or states an exact figure, is a payment and stays one."""
        ev, _ = _read(POOL, kind="DEPOSIT_PAYOUT", amount=12000)
        assert ev["is_cumulative"] is True
        dated = "The Bangladesh Bank provided around Tk 2,000 crore from its deposit protection fund on Tuesday."
        assert R.stock_by_pool(dated) is False
        assert R.stock_by_pool("The Bangladesh Bank provided Tk 2,000 crore from its deposit protection fund.") is False

    def test_a_dated_single_release_is_still_summed(self):
        """The guard must not eat real news: a one-day release is an ACT, not cumulative, and counted."""
        q = "The Finance Division released Tk 4,000 crore as LNG subsidy to Petrobangla on Tuesday."
        ev, _ = _read(q, kind="SUBSIDY_RELEASE", amount=4000, cp="Petrobangla")
        assert ev["nature"] == "ACT" and ev["is_cumulative"] is False
        s = _mem()
        _store(s, q, kind="SUBSIDY_RELEASE", amount=4000, cp="Petrobangla")
        assert _month_inject(s) == 4000.0


class TestAScemeIsNotCash:
    def test_a_scheme_size_is_capacity_not_cash(self):
        """'a Tk 10,000 crore refinancing scheme' announced as DISBURSED must not become 10,000 crore injected."""
        ev, _ = _read(SCHEME, amount=10000, status="DISBURSED")
        assert ev["nature"] == "FACILITY"
        s = _mem()
        row = _store(s, SCHEME, amount=10000, status="DISBURSED")
        assert row.matters and _month_inject(s) == 0.0
        assert [e["id"] for e in NL.news_block(s, today=TODAY)["events"]] == [row.id]      # still LISTED

    def test_a_payment_out_of_a_scheme_is_still_cash(self):
        """The amount followed by 'from' / 'under' is what MOVED: the scheme's size elsewhere in the sentence is not it."""
        q1 = "The ministry released Tk 2,000 crore from the Tk 10,000 crore fund on Monday."
        q2 = "Forty farmers received loans totalling Tk 4.50 crore under Bangladesh Bank's Tk 10,000 crore refinancing scheme."
        assert R.nature_of(q1, "BB_REFINANCE", 2000) == "ACT"
        assert R.nature_of(q2, "BB_REFINANCE", 4.5) == "ACT"
        assert R.nature_of(q2, "BB_REFINANCE", None) == "ACT"      # no stored amount: the 4.50 is still a payment

    def test_bangla_scheme_size(self):
        q = "বাংলাদেশ ব্যাংকের ১০ হাজার কোটি টাকার পুনঃঅর্থায়ন স্কিম বাস্তবায়ন এবং সচেতনতা বৃদ্ধির লক্ষ্যে সভা হয়েছে।"
        assert BN.is_facility(q) and R.nature_of(q, "BB_REFINANCE", 10000) == "FACILITY"
        assert not BN.is_facility("৪ কোটি ৫০ লাখ টাকার ঋণ বিতরণ করা হয়।")

    def test_a_has_done_lede_is_news_not_background(self):
        """Present perfect is the lede of today's news. Only past perfect / 'launched last year' is background."""
        assert R.nature_of("Bangladesh Bank has cut the CRR by 50 basis points.", "RESERVE_REQ_CUT") == "ACT"
        assert R.nature_of("The government has released Tk 3,000 crore for fertiliser subsidy.", "SUBSIDY_RELEASE", 3000) == "ACT"
        assert R.nature_of("The scheme, which had been launched in 2024, now has 40 banks.", "BB_REFINANCE") == "BACKGROUND"
        assert R.nature_of("The ministry released Tk 3,000 crore last month.", "SUBSIDY_RELEASE", 3000) == "BACKGROUND"
        assert R.nature_of("The ministry released Tk 3,000 crore last month, on 14 October.", "SUBSIDY_RELEASE", 3000) == "ACT"
        assert R.nature_of("Releases rose to Tk 3,000 crore, up from Tk 2,000 crore last month.", "SUBSIDY_RELEASE", 3000) == "ACT"

    def test_background_is_stored_with_its_quote_but_does_not_matter(self):
        s = _mem()
        row = _store(s, "The scheme, which had been launched in 2024, was topped up with Tk 5,000 crore.", amount=5000)
        assert row.nature == "BACKGROUND" and row.matters is False and row.evidence_quote


class TestAnEnrolmentIsNotAnEvent:
    def test_a_participation_agreement_is_listed_under_its_scheme_and_never_announced(self):
        s = _mem()
        row = _store(s, JOIN, amount=2000, status="APPROVED", cp="ONE Bank PLC")
        assert row.nature == "ENROLMENT" and row.matters is False
        b = NL.news_block(s, today=TODAY)
        assert b["events"] == [] and b["hidden"]["minor"] == 0
        assert [(g["instrument"], [x["bank"] for x in g["banks"]]) for g in b["enrolments"]] == \
            [("Tk 2,000 crore scheme", ["ONE Bank PLC"])]
        assert A.pending(s, NOW) == []
        lines, _ = A.news_digest_lines(s, NOW)
        assert any("1 bank(s) signed participation agreements under the 2,000 cr scheme (allocations, not cash)" in x
                   for x in lines)

    def test_a_support_agreement_with_a_weak_bank_is_still_news(self):
        """Lending to a failing bank is the news. The enrolment rule is for refinance schemes only."""
        q = "National Bank has signed an agreement with Bangladesh Bank for Tk 7,000 crore in liquidity support."
        s = _mem()
        row = _store(s, q, kind="BB_BANK_SUPPORT", amount=7000, cp="National Bank")
        assert row.nature == "ACT" and row.matters is True
        assert len(A.pending(s, NOW)) == 1


class TestTheSameStoryIsOneEvent:
    def test_one_scheme_from_two_outlets_weeks_apart_files_one_issue(self):
        s = _mem()
        first = _store(s, SCHEME, amount=10000, url="https://a/1", day=3)
        second = _store(s, "BB's new Tk 10,000 crore refinance scheme will lend to farmers, the central bank said.",
                        amount=10000, url="https://b/2", day=19)
        assert second.nature == "FACILITY" and second.redundant_with == f"liquidity_events:{first.id}"
        assert [e["serial"] for e in A.pending(s, NOW, max_age_days=60)] == [f"ev-{first.id}"]
        assert {x["url"] for x in json.loads(first.sources_json)} == {"https://a/1", "https://b/2"}

    def test_the_same_stake_purchase_from_two_outlets_is_counted_once(self):
        """Live: ev-17 (cp 'Padma Bank ...') and ev-21 (cp 'ICB, Sonali Bank ...') were the same 715-crore purchase."""
        s = _mem()
        a = _store(s, STAKE_17, kind="BANK_RECAP", amount=715, status="COMPLETED", url="https://a/1",
                   cp="Padma Bank (formerly Farmers Bank)")
        b = _store(s, STAKE_21, kind="BANK_RECAP", amount=715, status="COMPLETED", url="https://b/2",
                   cp="ICB, Sonali Bank, Janata Bank, Agrani Bank and Rupali Bank")
        assert b.redundant_with == f"liquidity_events:{a.id}" and a.redundant_with is None
        assert _month_inject(s) == 715.0

    def test_two_equal_payments_eleven_days_apart_stay_two_events(self):
        """The narrow ACT rule must not swallow a second, equal payment to the same party."""
        s = _mem()
        q = "The Finance Division released Tk 1,000 crore as LNG subsidy to Petrobangla."
        a = _store(s, q, kind="SUBSIDY_RELEASE", amount=1000, cp="Petrobangla", url="https://a/1", day=2)
        b = _store(s, q.replace("The Finance Division", "Finance Division officials said they"),
                   kind="SUBSIDY_RELEASE", amount=1000, cp="Petrobangla", url="https://b/2", day=13)
        assert a.id != b.id and a.redundant_with is None and b.redundant_with is None
        assert s.query(LiquidityEvent).count() == 2

    def test_one_article_with_two_equal_facts_is_never_one(self):
        """Two facts read from ONE article stay two (the scheme's size and a second 10,000-crore mention): the story link
        looks only across different articles."""
        s = _mem()
        ev, art = _read(SCHEME, amount=10000)
        r1, _ = L.merge_or_create(s, ev, art, NOW)
        r2 = LiquidityEvent(dedupe_key="other", kind=r1.kind, direction=r1.direction, amount_crore=10000.0, nature="FACILITY",
                            status="ANNOUNCED", evidence_quote="q", sources_json=r1.sources_json, matters=True,
                            announced_date=r1.announced_date, first_seen_utc=NOW, verified_by="llm")
        s.add(r2)
        s.commit()
        assert L.link_story(s, r2, now=NOW) is None and r2.redundant_with is None

    def test_a_confirmed_reading_becomes_the_story_primary(self):
        """A rule-read lead came first, then Claude read the same story elsewhere: the confirmed reading is the primary
        (it is the one announced and counted); the lead follows it, and an announcement already sent is not repeated."""
        s = _mem()
        lead = _store(s, SCHEME, amount=10000, verified_by="rules", url="https://a/1", day=3, confidence=None)
        lead.notified_utc = NOW
        s.commit()
        better = _store(s, "BB has set up a Tk 10,000 crore refinance scheme for farm lending.", amount=10000,
                        verified_by="llm", url="https://b/2", day=19)
        s.refresh(lead)
        assert lead.redundant_with == f"liquidity_events:{better.id}" and better.redundant_with is None
        assert better.notified_utc == NOW


class TestTheRepair:
    def _old(self, s):
        """Rows as the old reader stored them: no nature, everything ACT, the scheme and the double both counted."""
        rows = [(STAKE_17, "BANK_RECAP", 715, "COMPLETED", "Padma Bank (formerly Farmers Bank)", 1, "https://a/1"),
                (STAKE_21, "BANK_RECAP", 715, "COMPLETED", "ICB, Sonali Bank, Janata Bank, Agrani Bank and Rupali Bank", 2, "https://b/2"),
                (POOL, "DEPOSIT_PAYOUT", 12000, "DISBURSED", "Sammilito Islami Bank", 3, "https://c/3"),
                (JOIN, "BB_REFINANCE", 2000, "APPROVED", "ONE Bank PLC", 4, "https://d/4")]
        for q, kind, amt, st, cp, i, url in rows:
            s.add(LiquidityEvent(dedupe_key=f"k{i}", kind=kind, direction="INJECT", amount_crore=amt, status=st,
                                 counterparty=cp, summary=q[:100], evidence_quote=q, matters=True, nature=None,
                                 is_cumulative=False, announced_date=D(2026, 11, 18), verified_by="llm",
                                 first_seen_utc=datetime.datetime(2026, 11, 18, i),
                                 sources_json=json.dumps([{"url": url, "source": "x"}])))
        s.commit()

    def test_repair_is_idempotent_and_logged_in_data_changes(self):
        s = _mem()
        self._old(s)
        first = L.reclassify_stored(s, NOW, apply=True, today=TODAY)
        assert first["before"]["month_inject"] == 13430.0 and first["after"]["month_inject"] == 715.0
        assert {d["id"] for d in first["differences"]} == {2, 3}          # the double and the running total, by id
        classes = {c.change_class for c in s.query(DataChange).all()}
        assert classes == {"news_nature", "news_story_dup"}
        assert any("ev-3 is_cumulative False->True" in c.detail for c in s.query(DataChange).all())
        assert s.query(LiquidityEventHistory).filter_by(event_id=2, field="redundant_with").count() == 1
        n_changes, n_hist = s.query(DataChange).count(), s.query(LiquidityEventHistory).count()
        second = L.reclassify_stored(s, NOW, apply=True, today=TODAY)
        assert second["changes"] == [] and second["before"] == second["after"]
        assert s.query(DataChange).count() == n_changes and s.query(LiquidityEventHistory).count() == n_hist

    def test_a_dry_run_writes_nothing(self):
        s = _mem()
        self._old(s)
        rep = L.reclassify_stored(s, NOW, apply=False, today=TODAY)
        assert rep["changes"] and s.query(DataChange).count() == 0
        for r in s.query(LiquidityEvent).all():
            assert (r.nature, r.is_cumulative, r.matters, r.redundant_with) == ("ACT", False, True, None)


class TestApiAndEngineAgree:
    def test_api_and_engine_agree_on_what_is_summed(self):
        """api/ cannot import engines/: SUMMABLE_SQL is copied. A drift would let the page add what the engine refuses."""
        assert NL.SUMMABLE_SQL == L.SUMMABLE_SQL
        s = _mem()
        grid = [("ACT", False), ("ACT", True), ("FACILITY", False), ("ENROLMENT", False), ("BACKGROUND", False), (None, False),
                (None, True)]
        for i, (nat, cum) in enumerate(grid):
            s.add(LiquidityEvent(dedupe_key=f"g{i}", kind="SUBSIDY_RELEASE", direction="INJECT", amount_crore=100.0 * (i + 1),
                                 status="DISBURSED", counterparty="P", summary="s", evidence_quote="q", matters=True,
                                 nature=nat, is_cumulative=cum, event_date=D(2026, 11, 10),
                                 first_seen_utc=datetime.datetime(2026, 11, 10), verified_by="llm"))
        s.commit()
        engine_sum = s.execute(text("SELECT SUM(amount_crore) FROM liquidity_events WHERE " + L.SUMMABLE_SQL)).scalar()
        assert NL.news_block(s, today=TODAY)["totals"]["month_inject_crore"] == engine_sum == 100.0 + 600.0

    def test_an_unclassified_old_row_is_an_act_and_never_vanishes(self):
        s = _mem()
        s.add(LiquidityEvent(dedupe_key="old", kind="SUBSIDY_RELEASE", direction="INJECT", amount_crore=900.0, status="DISBURSED",
                             counterparty="P", summary="s", evidence_quote="q", matters=True, nature=None,
                             event_date=D(2026, 11, 10), first_seen_utc=datetime.datetime(2026, 11, 10), verified_by="llm"))
        s.commit()
        b = NL.news_block(s, today=TODAY)
        assert b["totals"]["month_inject_crore"] == 900.0 and b["events"][0]["nature"] == "ACT"

    def test_an_api_deployed_before_the_column_exists_still_answers(self):
        s = _mem()
        s.add(LiquidityEvent(dedupe_key="old", kind="SUBSIDY_RELEASE", direction="INJECT", amount_crore=900.0, status="DISBURSED",
                             counterparty="P", summary="s", evidence_quote="q", matters=True,
                             event_date=D(2026, 11, 10), first_seen_utc=datetime.datetime(2026, 11, 10), verified_by="llm"))
        s.commit()
        s.execute(text("ALTER TABLE liquidity_events DROP COLUMN nature"))
        s.commit()
        assert NL.news_block(s, today=TODAY)["totals"]["month_inject_crore"] == 900.0


class TestNoGenuineEventIsLost:
    """Recall guard over the 28 real events of 19-Nov-2026 (tests/fixtures/liquidity_events_snapshot.json). Every rule above
    is a way to hide something; this fails if one hides a genuine new act."""

    def _loaded(self):
        snap = json.load(open(os.path.join(_ROOT, "tests", "fixtures", "liquidity_events_snapshot.json"), encoding="utf-8"))
        s = _mem()
        for e in snap:
            s.add(LiquidityEvent(
                id=e["id"], dedupe_key=f"snap{e['id']}", kind=e["kind"], direction=e["direction"], amount_crore=e["amount_crore"],
                is_cumulative=bool(e["is_cumulative"]), nature=None, status=e["status"], counterparty=e["counterparty"],
                summary=e["summary"], evidence_quote=e["evidence_quote"], matters=bool(e["matters"]),
                verified_by=e["verified_by"], event_date=D.fromisoformat(e["event_date"]) if e["event_date"] else None,
                announced_date=D.fromisoformat(e["announced_date"]) if e["announced_date"] else None,
                first_seen_utc=datetime.datetime.fromisoformat(e["first_seen_utc"]),
                sources_json=json.dumps([{"url": f"https://snap/{e['id']}", "source": "snap"}])))
        s.commit()
        return snap, s

    def test_no_genuine_event_is_lost(self):
        snap, s = self._loaded()
        L.reclassify_stored(s, NOW, apply=True, today=TODAY)
        rows = {r.id: r for r in s.query(LiquidityEvent).all()}
        for e in snap:
            r, label = rows[e["id"]], e["label"]
            if label.startswith("genuine"):
                assert r.nature in ("ACT", "FACILITY") and not r.redundant_with and r.matters, (e["id"], label, r.nature)
            if label.startswith("enrolment"):
                assert r.nature == "ENROLMENT", (e["id"], label)
            if label.startswith("scheme size"):
                assert r.nature == "FACILITY", (e["id"], label)
            if label.startswith("running total"):
                assert r.is_cumulative, (e["id"], label)
        # one of each duplicated pair survives as the primary
        assert rows[21].redundant_with == "liquidity_events:17" and rows[17].redundant_with is None
        assert rows[22].redundant_with == "liquidity_events:19" and rows[19].redundant_with is None
        # what the desk now sees: the genuine acts are in the listing the page builds
        shown = {e["id"] for e in NL.news_block(s, today=TODAY, days=400)["events"]}
        assert {2, 4, 6, 8, 17} <= shown
        # and the month's figure is the one real act, 715 crore
        assert _month_inject(s) == 715.0


class TestNatureIsAudited:
    """An event with no nature is read as cash, and a story link to nothing hides a real event: both must be loud."""

    def _check(self, monkeypatch, rows):
        import datetime
        import validate
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        import db as dbmod
        eng = create_engine("sqlite:///:memory:")
        dbmod.Base.metadata.create_all(eng)
        s = sessionmaker(bind=eng)()
        for r in rows:
            s.add(dbmod.LiquidityEvent(dedupe_key=f"k{r.get('id', len(rows))}", direction="INJECT", evidence_quote="q", summary="s",
                                    first_seen_utc=datetime.datetime(2026, 11, 10), **r))
        s.commit()
        for r in rows:
            if r.get("nature") is None:
                s.execute(validate.text("UPDATE liquidity_events SET nature = NULL WHERE id = :i"), {"i": r["id"]})
        s.commit()                                    # the column default would otherwise turn None into 'ACT'
        monkeypatch.setattr(validate, "get_session", lambda: sessionmaker(bind=eng)())
        v = validate.integrity_check()
        return [i for i in v["issues"] + v["notices"] if "liquidity event" in i], v

    def test_a_dangling_story_link_is_a_defect_and_a_clean_table_is_silent(self, monkeypatch):
        import datetime
        base = dict(event_date=datetime.date(2026, 11, 10), kind="BB_REFINANCE", nature="ACT")
        issues, _ = self._check(monkeypatch, [dict(base, id=1), dict(base, id=2, redundant_with="liquidity_events:1")])
        assert issues == []
        issues, v = self._check(monkeypatch, [dict(base, id=1, redundant_with="liquidity_events:99"),
                                              dict(base, id=2, redundant_with="liquidity_events:2")])
        assert len(issues) == 1 and "2 liquidity event" in issues[0] and issues[0] in v["issues"]

    def test_an_event_without_nature_is_noticed(self, monkeypatch):
        import datetime
        issues, v = self._check(monkeypatch, [dict(event_date=datetime.date(2026, 11, 10), kind="BB_REFINANCE", nature=None, id=1)])
        assert len(issues) == 1 and issues[0] in v["notices"]
