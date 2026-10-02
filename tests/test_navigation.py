"""Every page must be reachable from the navigation.

Written because six finished pages were not in the nav at all -- /forecast,
/repo, /fxmarket, /guardian, /explore and /research -- including three built
days earlier at the desk's request. They were reachable only by typing the URL
or through the command palette, so from the user's side they did not exist. The
report was "I don't usually find it", and the cause was not a bad label.

A page nobody can navigate to is invisible work. This test is the only thing
that makes that a build failure rather than a discovery months later.

Two routes are deliberately excluded, and the reason is recorded here rather
than left to be guessed:

  /login      the authentication gate; linking to it from the nav is circular
  /drilldown  a single-date view that needs ?date= to mean anything; it is
              reached by clicking a date, and a bare link would land on today
              with no context
"""
import io
import os
import re
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

FRONTEND = os.path.join(_ROOT, "frontend")
APP = os.path.join(FRONTEND, "app")
NAV = os.path.join(FRONTEND, "components", "Nav.tsx")

# Route -> why it is not in the nav. Adding an entry here is a deliberate,
# reviewable decision; forgetting a page is not.
NOT_IN_NAV = {
    "/login": "the auth gate — linking to it from the nav is circular",
    "/drilldown": "needs ?date= to mean anything; reached by clicking a date",
}


def _routes():
    """Every route with a page.tsx, excluding route groups and API handlers."""
    out = []
    for root, _dirs, files in os.walk(APP):
        if "page.tsx" not in files:
            continue
        rel = os.path.relpath(root, APP).replace(os.sep, "/")
        route = "/" if rel == "." else "/" + rel
        # Next.js route groups (folder) and private folders (_folder) are not
        # part of the URL; nothing here uses them yet, but skip them if added.
        if "(" in route or "/_" in route:
            continue
        out.append(route)
    return sorted(set(out))


def _nav_hrefs():
    return set(re.findall(r'href:\s*"([^"]+)"', io.open(NAV, encoding="utf-8").read()))


def test_the_app_has_pages_and_a_nav_to_scan():
    # Guards against this whole file passing vacuously if either path moves.
    assert len(_routes()) > 5, _routes()
    assert len(_nav_hrefs()) > 5, _nav_hrefs()


def test_every_page_is_reachable_from_the_nav():
    missing = sorted(set(_routes()) - _nav_hrefs() - set(NOT_IN_NAV))
    assert not missing, (
        "these pages exist but are not in Nav.tsx, so nobody can navigate to "
        f"them: {missing}. Add them to the nav, or to NOT_IN_NAV with a reason."
    )


def test_the_nav_does_not_link_to_a_page_that_does_not_exist():
    # The other direction: a link to a deleted page is a dead end.
    routes = set(_routes())
    dead = sorted(h for h in _nav_hrefs()
                  if h.startswith("/") and not h.startswith("//") and h not in routes)
    assert not dead, f"Nav.tsx links to routes with no page.tsx: {dead}"


@pytest.mark.parametrize("route,reason", sorted(NOT_IN_NAV.items()))
def test_each_deliberate_exclusion_still_exists_and_is_explained(route, reason):
    # If an excluded page is deleted, the exclusion should go too rather than
    # sit here forever hiding the fact that the entry is stale.
    assert route in _routes(), f"{route} is in NOT_IN_NAV but has no page"
    assert len(reason) > 20, f"{route} needs a real reason, got {reason!r}"


def test_the_nav_follows_the_theme_rather_than_hardcoding_dark():
    """The nav was written with Tailwind greys (bg-gray-950, text-teal-400), so
    it stayed dark in light mode while every other surface switched -- the most
    visible component on the page ignoring the theme."""
    src = io.open(NAV, encoding="utf-8").read()
    hardcoded = sorted(set(re.findall(
        r"\b(?:bg|text|border|from|to|via)-(?:gray|slate|zinc|neutral|stone|teal|"
        r"blue|red|green|amber|orange)-\d{2,3}\b", src)))
    assert not hardcoded, (
        "Nav.tsx uses fixed colour utilities instead of theme tokens, so it "
        f"will not follow light/dark: {hardcoded}"
    )
    assert "var(--" in src, "Nav.tsx should use theme tokens"
