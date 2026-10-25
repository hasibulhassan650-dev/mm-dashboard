"""When BB's own website says it is down, the system must know it is BB's.

From 22-Oct-2026 BB served a maintenance page in place of its pages. Every
BB-fed series stopped and this system spent days blaming itself: Chrome waited
ninety seconds per attempt for a table that was never coming, each failed fetch
was filed as "OUR fetch is not current", the freshness check escalated it as a
fault in our own code — and the 403 body, when it turned up in diagnosis, was
mistaken for BB throttling the developer's machine. It was BB's maintenance
page, served to everyone.

The fixture is that page, byte for byte (759 bytes, saved from BB on 25-Oct).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fetchers import bb_health as H

_FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "bb_maintenance.html")


@pytest.fixture(scope="module")
def maintenance_page():
    with open(_FIX, encoding="utf-8") as fh:
        return fh.read()


class TestRecognisingBBsOwnMaintenancePage:
    def test_the_real_page_is_recognised(self, maintenance_page):
        assert len(maintenance_page) < 1000, "the fixture is not the 759-byte page"
        assert H.looks_like_maintenance(maintenance_page)
        assert H.classify(403, maintenance_page) == "maintenance"

    def test_the_status_code_alone_is_not_the_signal(self, maintenance_page):
        """BB served it with a 403. F5 serves a 403 to a blocked client too, and
        calling that maintenance would let our own block be blamed on BB."""
        assert H.classify(403, "<html><body>Request rejected. Support ID: 123</body></html>") \
            == "blocked"
        assert H.classify(503, "<html>upstream timeout</html>") == "blocked"

    def test_a_503_from_anything_else_is_not_maintenance(self):
        """"Service Temporarily Unavailable" is only the <title>; any proxy says
        it. BB's own two phrases are the evidence."""
        page = "<html><head><title>Service Temporarily Unavailable</title></head></html>"
        assert H.classify(503, page) == "blocked"

    def test_a_real_page_that_mentions_maintenance_is_not_maintenance(self):
        """A press release about scheduled maintenance of a payment system is a
        genuine page, and it is tens of kilobytes. The size guard is what
        stops the detector from declaring BB down while it is serving data."""
        real = ("<html><body>" + "<tr><td>Open Market Operations</td></tr>" * 400 +
                "<p>The RTGS will undergo scheduled maintenance on Friday.</p></body></html>")
        assert len(real) > H._MAX_BODY
        assert H.classify(200, real) == "ok"

    def test_a_normal_page_is_ok(self):
        assert H.classify(200, "<html><table><tr><td>x</td></tr></table></html>") == "ok"

    def test_no_response_is_unreachable_not_maintenance(self):
        """Our own network being down must not be reported as BB's outage."""
        assert H.classify(None, None) == "unreachable"
        assert not H.looks_like_maintenance(None)
        assert not H.looks_like_maintenance("")

    def test_case_does_not_matter(self):
        assert H.looks_like_maintenance("<h1>The Service is UNDER MAINTENANCE</h1>")


class TestTheVerdictIsASpectrum:
    """On 24-Oct a GitHub runner read real (stale) pages over plain HTTP while
    the F5-protected ones failed; from a residential address every URL showed
    the maintenance page. One global flag cannot describe that."""

    @staticmethod
    def _r(*kinds):
        return [(f"https://x/{i}", 403 if k == "maintenance" else 200, k)
                for i, k in enumerate(kinds)]

    def test_all_maintenance(self):
        assert H.summarise(self._r("maintenance", "maintenance"))["state"] == "maintenance"

    def test_mixed_is_partial_not_up_and_not_down(self):
        assert H.summarise(self._r("maintenance", "ok", "ok"))["state"] == "partial"

    def test_all_ok(self):
        assert H.summarise(self._r("ok", "ok"))["state"] == "up"

    def test_blocked_and_unreachable_only_is_unknown(self):
        """"Could not tell" is an answer. Calling it up would hide an outage;
        calling it down would blame BB for our network."""
        r = [("u", 403, "blocked"), ("v", None, "unreachable")]
        assert H.summarise(r)["state"] == "unknown"

    def test_the_summary_carries_what_was_seen(self):
        s = H.summarise(self._r("maintenance", "ok"))
        assert [d["kind"] for d in s["detail"]] == ["maintenance", "ok"]
        assert s["probed_utc"]


class TestProbeCostsAFewSeconds:
    def test_a_network_error_per_url_is_isolated(self, monkeypatch):
        import curl_cffi.requests as cr

        calls = []

        def _get(url, **kw):
            calls.append(url)
            if len(calls) == 1:
                raise ConnectionError("no route")

            class _R:
                status_code, text = 200, "<table></table>"
            return _R()

        monkeypatch.setattr(cr, "get", _get)
        out = H.probe()
        assert [d["kind"] for d in out["detail"]] == ["unreachable", "ok", "ok"]
        assert out["state"] == "up"
        assert len(calls) == len(H.PROBE_URLS)


class TestCheckResponseNamesTheOutage:
    """A bare "403 Client Error" in a step's error is how BB's maintenance page
    got filed as a fault in our own fetch. check_response keeps every ordinary
    failure exactly as raise_for_status() had it, and changes only the one
    case that is BB telling us it is down."""

    class _Resp:
        def __init__(self, status, text, url="https://www.bb.org.bd/x"):
            self.status_code, self.text, self.url = status, text, url

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"{self.status_code} Client Error")

    def test_the_maintenance_page_raises_bbmaintenance_not_a_bare_403(self, maintenance_page):
        with pytest.raises(H.BBMaintenance) as exc:
            H.check_response(self._Resp(403, maintenance_page))
        assert "maintenance page" in str(exc.value) and "403" in str(exc.value)

    def test_an_ordinary_403_is_still_an_ordinary_error(self):
        """Not maintenance, so it must not be reported as BB being down."""
        with pytest.raises(RuntimeError) as exc:
            H.check_response(self._Resp(403, "<html>Request rejected. Support ID: 99</html>"))
        assert not isinstance(exc.value, H.BBMaintenance)

    def test_a_good_page_passes_silently(self):
        assert H.check_response(self._Resp(200, "<table><tr><td>x</td></tr></table>")) is None

    def test_bbmaintenance_is_a_runtimeerror_so_existing_handlers_still_catch_it(self):
        assert issubclass(H.BBMaintenance, RuntimeError)

    def test_bb_get_and_bb_post_raise_it_too(self, maintenance_page, monkeypatch):
        import curl_cffi.requests as cf

        class _Sess:
            def __init__(self, *a, **k):
                self.headers, self.cookies = self, self

            def update(self, *_):
                pass

            def set(self, *a, **k):
                pass

            def get(self, *a, **k):
                return TestCheckResponseNamesTheOutage._Resp(403, maintenance_page)

            post = get

        monkeypatch.setattr(cf, "Session", _Sess)
        import fetchers.bb_session as bbs
        with pytest.raises(H.BBMaintenance):
            bbs.bb_get("https://www.bb.org.bd/x", {}, "UA")
        with pytest.raises(H.BBMaintenance):
            bbs.bb_post("https://www.bb.org.bd/x", {}, {}, "UA")


class TestRetryingASiteThatSaidItIsDown:
    """The reference-rate step retried three times against BB's maintenance
    page in CI, each retry re-running the whole cookie-capture cycle. An
    ordinary failure deserves its retries; BB announcing it is down does not."""

    def test_maintenance_is_not_retried(self):
        import fetchers.bb_session as bbs
        calls = []

        def once():
            calls.append(1)
            raise H.BBMaintenance("down")

        assert bbs.fetch_with_retry(once, attempts=3, delay=0, label="t") == []
        assert len(calls) == 1, f"retried {len(calls)} times against a site that said it was down"

    def test_an_ordinary_failure_still_gets_every_attempt(self):
        import fetchers.bb_session as bbs
        calls = []

        def once():
            calls.append(1)
            raise ConnectionError("flaky")

        assert bbs.fetch_with_retry(once, attempts=3, delay=0, label="t") == []
        assert len(calls) == 3
