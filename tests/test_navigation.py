"""Every page must be reachable from the navigation that is actually rendered.

Two bugs are pinned here, and the second one is why the first went unnoticed.

1. /schedule and /auctions existed but were not in the sidebar, so the desk
   could not find them and reported "I don't usually find it" about finished,
   deployed work.

2. There were TWO nav definitions: components/terminal/nav.ts, which AppShell
   renders, and components/Nav.tsx, which was imported NOWHERE. A nav rework
   went into the dead one. It typechecked, it built, its test passed, and the
   screen did not change. A test that validates dead code is worse than no
   test, because it reports success.

So this file checks the nav that the layout actually renders, and refuses to
let a second nav definition exist.

Two routes are deliberately excluded, with the reason recorded rather than
left to be guessed:

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
# The nav AppShell renders. If this moves, test_the_nav_is_actually_rendered
# fails rather than this file silently checking nothing.
NAV = os.path.join(FRONTEND, "components", "terminal", "nav.ts")
LAYOUT = os.path.join(APP, "layout.tsx")

NOT_IN_NAV = {
    "/login": "the auth gate — linking to it from the nav is circular",
    "/drilldown": "needs ?date= to mean anything; reached by clicking a date",
}


def _read(p):
    return io.open(p, encoding="utf-8").read()


def _routes():
    """Every route with a page.tsx, excluding route groups and private folders."""
    out = []
    for root, _dirs, files in os.walk(APP):
        if "page.tsx" not in files:
            continue
        rel = os.path.relpath(root, APP).replace(os.sep, "/")
        route = "/" if rel == "." else "/" + rel
        if "(" in route or "/_" in route:
            continue
        out.append(route)
    return sorted(set(out))


def _nav_hrefs():
    return set(re.findall(r'href:\s*"([^"]+)"', _read(NAV)))


def test_the_app_has_pages_and_a_nav_to_scan():
    # Guards against this whole file passing vacuously if either path moves.
    assert len(_routes()) > 5, _routes()
    assert len(_nav_hrefs()) > 5, _nav_hrefs()


def test_the_nav_is_actually_rendered():
    """The check that was missing. A nav file nobody imports changes nothing,
    and a test against it reports a success that is not real."""
    chain = _read(LAYOUT)
    assert "AppShell" in chain, "layout.tsx no longer renders AppShell — update this test"
    shell = _read(os.path.join(FRONTEND, "components", "terminal", "AppShell.tsx"))
    assert re.search(r'from "\./nav"', shell), (
        "AppShell no longer imports ./nav, so the file this test checks is not "
        "the nav being rendered"
    )


def test_no_file_full_of_route_links_is_dead_code():
    """The failure this exists for: components/Nav.tsx held a full nav, was
    imported NOWHERE, and a rework landed in it — typechecked, built, test
    green, screen unchanged.

    A destination list that nothing imports cannot affect the UI, so anyone
    editing it is working on a decoy. CommandPalette also carries route links
    and legitimately passes: it IS imported."""
    sources = {}
    for root, _dirs, files in os.walk(FRONTEND):
        if "node_modules" in root or ".next" in root:
            continue
        for f in files:
            if f.endswith((".ts", ".tsx")):
                sources[os.path.join(root, f)] = _read(os.path.join(root, f))

    # Only COMPONENTS and LIB are candidates. Next.js wires app/**/page.tsx by
    # the filesystem rather than by an import, so route files always look
    # unimported; the thing that actually went dead was a component.
    candidates = {p: s for p, s in sources.items()
                  if (os.sep + "components" + os.sep) in p or (os.sep + "lib" + os.sep) in p}

    dead = []
    for path, src in candidates.items():
        if len(re.findall(r'href:\s*"/', src)) < 5 or "label:" not in src:
            continue
        stem = os.path.splitext(os.path.basename(path))[0]
        # does anything else import this module by name?
        q = chr(39) + chr(34)          # quote chars, kept out of the literal
        imported = re.compile(r"(?:from|import)\s+[" + q + r"][^" + q + r"]*"
                              + re.escape(stem) + r"[" + q + r"]")
        if any(imported.search(other)
               for p2, other in sources.items() if p2 != path):
            continue
        dead.append(os.path.relpath(path, FRONTEND))
    assert not dead, (
        "these files define route links but nothing imports them, so edits to "
        f"them change nothing on screen: {dead}"
    )


def test_every_page_is_reachable_from_the_nav():
    missing = sorted(set(_routes()) - _nav_hrefs() - set(NOT_IN_NAV))
    assert not missing, (
        "these pages exist but are not in the rendered nav, so nobody can "
        f"navigate to them: {missing}. Add them to components/terminal/nav.ts, "
        "or to NOT_IN_NAV with a reason."
    )


def test_the_nav_does_not_link_to_a_page_that_does_not_exist():
    routes = set(_routes())
    dead = sorted(h for h in _nav_hrefs()
                  if h.startswith("/") and not h.startswith("//") and h not in routes)
    assert not dead, f"the nav links to routes with no page.tsx: {dead}"


def test_no_nav_label_claims_to_be_auctions_except_the_auctions_page():
    """/fx was labelled "FX Auctions" — BB's USD/BDT intervention — and was the
    only entry containing the word, so anyone scanning for treasury auctions
    landed there and concluded there was no auctions page."""
    pairs = re.findall(r'href:\s*"([^"]+)",\s*label:\s*"([^"]+)"', _read(NAV))
    assert pairs, "could not parse href/label pairs from the nav"
    confusing = [(h, l) for h, l in pairs if "auction" in l.lower() and h != "/auctions"]
    assert not confusing, (
        f"these nav labels say 'auction' but do not point at /auctions: {confusing}"
    )


@pytest.mark.parametrize("route,reason", sorted(NOT_IN_NAV.items()))
def test_each_deliberate_exclusion_still_exists_and_is_explained(route, reason):
    assert route in _routes(), f"{route} is in NOT_IN_NAV but has no page"
    assert len(reason) > 20, f"{route} needs a real reason, got {reason!r}"
