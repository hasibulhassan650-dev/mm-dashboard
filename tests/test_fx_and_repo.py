"""
Interbank FX, published exchange rates and interbank repo — parser invariants.

Fixtures are BB's own HTML, trimmed but never cleaned: they still contain the
quirks that broke the first parse (a stray space inside "8. 50-8.95", a
high/low pair printed the wrong way round, and an all-zeros no-trade row).
"""
import datetime, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import calendar_utils
from fetchers.interbank_fx import parse_interbank_fx_html
from fetchers.interbank_repo import parse_interbank_repo_html, _parse_range
from fetchers.fxrates import parse_fx_rates_html

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
D = datetime.date


def _fx(name):
    return open(os.path.join(FIX, name), encoding="utf-8").read()


class TestInterbankFx:
    def test_all_three_segments_parse(self):
        rows = parse_interbank_fx_html(_fx("bb_interbank_fx.html"))
        segs = {r["segment"] for r in rows}
        assert segs == {"SPOT", "FORWARD", "SWAP"}, segs
        assert len(rows) == 15

    def test_only_spot_carries_rates(self):
        # BB publishes no rates for forward/swap; inheriting spot's would be
        # inventing a price for a market that never printed one.
        rows = parse_interbank_fx_html(_fx("bb_interbank_fx.html"))
        assert all(r["war_rate"] is None for r in rows if r["segment"] != "SPOT")
        assert all(r["war_rate"] is not None for r in rows if r["segment"] == "SPOT")

    def test_war_lies_between_the_extremes(self):
        for r in parse_interbank_fx_html(_fx("bb_interbank_fx.html")):
            if r["segment"] == "SPOT":
                assert r["low_rate"] <= r["war_rate"] <= r["high_rate"], r

    def test_inverted_high_low_is_relabelled_not_dropped(self):
        # 09-Aug-2026: BB printed "highest" 123.75 BELOW "lowest" 123.79. The
        # WAR between them proves both numbers are right and only the labels
        # were swapped, so relabel by size — never discard, never invent.
        html = ("<html><body><h4>FX Spot</h4><table>"
                "<tr><td>09/08/2026</td><td>2</td><td>1.30</td>"
                "<td>123.7500</td><td>123.7900</td><td>123.7592</td></tr>"
                "</table></body></html>")
        r = parse_interbank_fx_html(html)[0]
        assert (r["low_rate"], r["high_rate"]) == (123.75, 123.79)
        assert r["low_rate"] <= r["war_rate"] <= r["high_rate"]


class TestInterbankRepo:
    def test_bb_stray_space_inside_a_number(self):
        assert _parse_range("8. 50-8.95") == (8.5, 8.95)
        assert _parse_range("1-7") == (1.0, 7.0)
        assert _parse_range("8.8") == (8.8, 8.8)
        assert _parse_range("") == (None, None)

    def test_rows_parse_and_war_sits_inside_its_range(self):
        rows = parse_interbank_repo_html(_fx("bb_interbank_repo.html"))
        assert len(rows) == 18
        for r in rows:
            if r["war_pct"] is not None:
                assert r["rate_min_pct"] <= r["war_pct"] <= r["rate_max_pct"], r

    def test_a_no_trade_day_keeps_the_fact_and_drops_the_fiction(self):
        # 15-Sep-2026: BB prints "0 0 0 0 0". Zero deals is real; a 0% repo rate
        # is not — storing it would drag every average down.
        rows = {r["trade_date"]: r for r in parse_interbank_repo_html(_fx("bb_interbank_repo.html"))}
        z = rows[D(2026, 9, 15)]
        assert z["num_deals"] == 0 and z["amount_crore"] == 0.0
        assert z["war_pct"] is None and z["rate_min_pct"] is None and z["tenor_min_days"] is None


class TestExchangeRates:
    def setup_method(self):
        # 29-Sep-2026 is a Tuesday; the previous working day is Monday the 28th.
        calendar_utils.load_holidays(set(), set())

    def teardown_method(self):
        calendar_utils.load_holidays(set(), set())

    def test_every_currency_parses_with_bid_ask(self):
        rows = parse_fx_rates_html(_fx("bb_exchangerate.html"))
        ccys = {r["currency"] for r in rows}
        assert {"USD", "EUR", "GBP", "JPY", "INR"} <= ccys, ccys
        assert all(r["bid_rate"] <= r["ask_rate"] for r in rows)

    def test_rate_date_is_the_previous_business_day_not_the_headline(self):
        # THE date trap: BB heads the page "(Sep 29, 2026)" but its own note says
        # the rates are Dhaka close on the PREVIOUS business day. Storing the
        # headline date would put every FX rate a day late — the same mistake
        # the treasury page's issue-vs-auction date caused.
        rows = parse_fx_rates_html(_fx("bb_exchangerate.html"))
        assert rows[0]["published_date"] == D(2026, 9, 29)
        assert rows[0]["rate_date"] == D(2026, 9, 28)

    def test_only_usd_has_a_weighted_average(self):
        rows = {r["currency"]: r for r in parse_fx_rates_html(_fx("bb_exchangerate.html"))}
        assert rows["USD"]["war_rate"] is not None
        assert rows["EUR"]["war_rate"] is None

    def test_refuses_to_guess_a_date(self):
        # No heading date means we cannot know which day the rates describe.
        # Returning nothing is correct; assuming "today" would be a silent lie.
        assert parse_fx_rates_html("<html><body><table><tr><td>USD</td>"
                                   "<td>1</td><td>2</td></tr></table></body></html>") == []
