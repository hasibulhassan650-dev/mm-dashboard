"""When the F5 challenge stops clearing, the refresh must fail fast.

From 23-Oct-2026 seven consecutive hourly refreshes were killed by their
30-minute timeout. Chrome launched fine; the challenge never cleared; and each
page spent three attempts x ~95s waiting for it. Four pages burned about
twenty minutes of the budget, the job was cancelled, and with it went the
commit, the integrity gate and every step that needed no browser at all. The
retries were also traffic to a host that was already refusing us.

The fix is a per-process breaker: one page that exhausts its attempts means the
challenge is not clearing, so the next page gets ONE short probe, and a second
exhaustion makes everything after it fail instantly. These tests count real
launches, because "it bails out early" is a claim, and a claim needs a number.
"""
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fetchers.bb_session as bbs


@pytest.fixture
def browser(monkeypatch):
    """A fake browser stack whose challenge never clears. Returns the launch log."""
    log = {"launches": 0, "waits": []}

    class _Opts:
        def add_argument(self, *_):
            pass

    class _Chrome:
        def __init__(self, *a, **k):
            log["launches"] += 1

        def get(self, *_):
            pass

        def quit(self):
            pass

    class _Wait:
        def __init__(self, driver, timeout):
            log["waits"].append(timeout)

        def until(self, *_):
            raise TimeoutError("")        # selenium's TimeoutException has an empty message

    uc = types.ModuleType("undetected_chromedriver")
    uc.ChromeOptions, uc.Chrome = _Opts, _Chrome
    sel = types.ModuleType("selenium")
    wd = types.ModuleType("selenium.webdriver")
    common = types.ModuleType("selenium.webdriver.common")
    by = types.ModuleType("selenium.webdriver.common.by")
    by.By = types.SimpleNamespace(CSS_SELECTOR="css")
    support = types.ModuleType("selenium.webdriver.support")
    ui = types.ModuleType("selenium.webdriver.support.ui")
    ui.WebDriverWait = _Wait
    ec = types.ModuleType("selenium.webdriver.support.expected_conditions")
    ec.presence_of_element_located = lambda *_: None
    for name, mod in (("undetected_chromedriver", uc), ("selenium", sel),
                      ("selenium.webdriver", wd), ("selenium.webdriver.common", common),
                      ("selenium.webdriver.common.by", by),
                      ("selenium.webdriver.support", support),
                      ("selenium.webdriver.support.ui", ui),
                      ("selenium.webdriver.support.expected_conditions", ec)):
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setattr(bbs, "get_chrome_version", lambda: 155)
    monkeypatch.setattr(bbs, "_cleanup_chrome", lambda: None)
    monkeypatch.setattr("time.sleep", lambda *_: None)
    bbs.reset_f5_breaker()
    yield log
    bbs.reset_f5_breaker()


def _try(url):
    try:
        bbs.get_f5_cookies(url)
    except Exception as exc:
        return exc
    return None


def test_the_first_page_gets_its_full_three_attempts(browser):
    exc = _try("https://www.bb.org.bd/a")
    assert browser["launches"] == 3, browser
    assert browser["waits"] == [90, 90, 90]
    assert not isinstance(exc, bbs.F5Unavailable), \
        "the first failure is a real failure, not the breaker"


def test_the_second_page_gets_one_short_probe_not_three_long_ones(browser):
    _try("https://www.bb.org.bd/a")
    before = browser["launches"]
    _try("https://www.bb.org.bd/b")
    assert browser["launches"] - before == 1, "the second page launched Chrome repeatedly"
    assert browser["waits"][-1] == 35, "the probe waited as long as a full attempt"


def test_after_two_exhaustions_nothing_launches_at_all(browser):
    _try("https://www.bb.org.bd/a")
    _try("https://www.bb.org.bd/b")
    launched = browser["launches"]
    exc = _try("https://www.bb.org.bd/c")
    assert isinstance(exc, bbs.F5Unavailable)
    assert browser["launches"] == launched, "Chrome was launched after the breaker tripped"


def test_the_whole_run_is_bounded(browser):
    """The number that matters. Four pages used to cost 12 launches and ~20
    minutes; they must now cost 4 and at most 3x90 + 2x35 seconds of waiting."""
    for page in "abcd":
        _try(f"https://www.bb.org.bd/{page}")
    assert browser["launches"] == 4, browser["launches"]       # 3 + 1 + 0 + 0
    assert sum(browser["waits"]) == 3 * 90 + 35, sum(browser["waits"])


def test_the_message_says_why_nothing_was_tried(browser):
    _try("https://www.bb.org.bd/a")
    _try("https://www.bb.org.bd/b")
    exc = _try("https://www.bb.org.bd/c")
    assert "did not clear" in str(exc) and "refusing" in str(exc)


def test_a_success_never_trips_it(monkeypatch, browser):
    """The breaker must only count FAILURES, or a healthy run that happens to
    need many pages would lock itself out."""
    import types as t

    class _GoodWait:
        def __init__(self, *a, **k):
            pass

        def until(self, *_):
            return True

    sys.modules["selenium.webdriver.support.ui"].WebDriverWait = _GoodWait

    class _GoodChrome:
        def __init__(self, *a, **k):
            browser["launches"] += 1

        def get(self, *_):
            pass

        def get_cookies(self):
            return [{"name": "TS", "value": "x"}]

        def execute_script(self, *_):
            return "UA"

        def quit(self):
            pass

    sys.modules["undetected_chromedriver"].Chrome = _GoodChrome
    for page in "abcdef":
        cookies, ua = bbs.get_f5_cookies(f"https://www.bb.org.bd/{page}")
        assert cookies == {"TS": "x"} and ua == "UA"
    assert browser["launches"] == 6
