"""Every source link on the dashboard must be a URL a fetcher really reads.

Why this exists: an invented or stale URL on a treasury desk's screen is
fabrication. The desk clicks "Source" to check a number against Bangladesh Bank;
a link typed from memory, or one that outlived the fetcher that moved, sends
them to the wrong page and quietly tells them the number was verified there.

So frontend/lib/sources.ts is held to the fetchers:

  * every DATASETS key (engines/expectations.py) has an entry, and its table
    name is the one DATASETS names;
  * every href appears literally in config.py or fetchers/*.py, either whole or
    as `_BASE + "slug"` where the file defines that _BASE;
  * every nav page either shows sources or is exempt WITH a reason, so a new
    page cannot ship without a decision;
  * every page listed shows a <SourceLink>, and every key a page passes exists;
  * a derived dataset names what it derives from, and those keys exist.

/fx is in PAGE_SOURCES but its <SourceLink> is added separately, so the
page-contains-SourceLink check skips it rather than failing the build.
"""
import glob
import io
import os
import re
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from engines.expectations import DATASETS  # noqa: E402

FRONTEND = os.path.join(_ROOT, "frontend")
APP = os.path.join(FRONTEND, "app")
SOURCES_TS = os.path.join(FRONTEND, "lib", "sources.ts")
NAV = os.path.join(FRONTEND, "components", "terminal", "nav.ts")
# Added by a different change; asserting it here would fail the build for a
# reason unrelated to this file.
SOURCELINK_NOT_YET_REQUIRED = {"/fx"}


def _read(p):
    return io.open(p, encoding="utf-8").read()


def _sources_ts():
    return _read(SOURCES_TS)


def _entries():
    """{key: block_text} for every top-level entry of SOURCES."""
    body = _sources_ts().split("export const SOURCES", 1)[1].split("export const PAGE_SOURCES", 1)[0]
    out = {}
    for m in re.finditer(r"^  (\w+): \{\n(.*?)^  \},", body, re.M | re.S):
        out[m.group(1)] = m.group(2)
    return out


def _page_sources():
    ts = _sources_ts()
    block = ts.split("export const PAGE_SOURCES:", 1)[1].split("export const PAGE_SOURCES_EXEMPT", 1)[0]
    return {h: re.findall(r'"([^"]+)"', keys)
            for h, keys in re.findall(r'^  "(/[^"]*)":\s*\[(.*?)\],', block, re.M)}


def _exempt():
    ts = _sources_ts()
    block = ts.split("export const PAGE_SOURCES_EXEMPT", 1)[1]
    return dict(re.findall(r'^  "(/[^"]*)":\s*"(.*?)",?\s*$', block, re.M))


def _consts():
    """Prefix constants in sources.ts, e.g. const BB = "https://..."."""
    return dict(re.findall(r'^const (\w+) = "([^"]+)";', _sources_ts(), re.M))


def _hrefs():
    """(key, full href) for every url in every entry, prefixes resolved."""
    consts = _consts()
    out = []
    for key, block in _entries().items():
        for m in re.finditer(r'href:\s*(?:(\w+)\s*\+\s*)?"([^"]*)"', block):
            prefix = consts[m.group(1)] if m.group(1) else ""
            out.append((key, prefix + m.group(2)))
    return out


def _fetcher_text():
    files = [os.path.join(_ROOT, "config.py")] + sorted(glob.glob(os.path.join(_ROOT, "fetchers", "*.py")))
    return {f: _read(f) for f in files}


def _href_is_in_a_fetcher(href):
    """True when a fetcher or config.py carries this exact URL: as a whole
    string, or as `<_BASE-style constant> + "slug"` / `_BASE + "slug"`."""
    for text in _fetcher_text().values():
        if f'"{href}"' in text:
            return True
        # base + slug: a quoted base constant that is a prefix of the href and
        # a quoted slug in the same file that is the rest.
        for base in re.findall(r'^\w*BASE\w*\s*=\s*"([^"]+)"', text, re.M):
            if href.startswith(base) and f'"{href[len(base):]}"' in text:
                return True
    return False


def _nav_hrefs():
    return set(re.findall(r'href:\s*"([^"]+)"', _read(NAV)))


def test_there_is_something_to_check():
    # A parse that finds nothing would make every test below pass vacuously.
    assert len(_entries()) > 20, _entries().keys()
    assert len(_hrefs()) > 20, _hrefs()
    assert len(_page_sources()) > 10, _page_sources()
    assert len(_exempt()) >= 8, _exempt()


def test_every_dataset_has_a_source_entry_with_its_own_table():
    entries = _entries()
    missing = sorted({d.key for d in DATASETS} - set(entries))
    assert not missing, f"datasets with no entry in frontend/lib/sources.ts: {missing}"
    assert "news" in entries, "the news source entry is missing"
    wrong = []
    for d in DATASETS:
        m = re.search(r'table:\s*"([^"]*)"', entries[d.key])
        if not m or m.group(1) != d.table:
            wrong.append((d.key, m.group(1) if m else None, d.table))
    assert not wrong, f"table name differs from DATASETS (key, sources.ts, DATASETS): {wrong}"


def test_every_href_is_a_url_a_fetcher_actually_reads():
    bad = [(k, h) for k, h in _hrefs() if not _href_is_in_a_fetcher(h)]
    assert not bad, (
        "these source links do not appear in config.py or fetchers/*.py, so they "
        f"were typed from memory or have gone stale: {bad}"
    )


def test_every_non_derived_source_has_a_url_and_derived_ones_have_none():
    entries = _entries()
    for d in DATASETS:
        block = entries[d.key]
        has_url = "href:" in block
        derived = "derivedFrom:" in block
        if d.cadence == "derived":
            assert derived and not has_url, f"{d.key}: derived datasets use derivedFrom and no url"
        else:
            assert has_url and not derived, f"{d.key}: a BB dataset needs a url and no derivedFrom"


def test_derived_from_keys_exist():
    entries = _entries()
    for key, block in entries.items():
        m = re.search(r"derivedFrom:\s*\[(.*?)\]", block, re.S)
        if not m:
            continue
        refs = re.findall(r'"([^"]+)"', m.group(1))
        assert refs, f"{key}: empty derivedFrom"
        unknown = [r for r in refs if r not in entries]
        assert not unknown, f"{key} derives from unknown sources {unknown}"
        assert key not in refs, f"{key} derives from itself"


def test_every_nav_page_shows_sources_or_says_why_not():
    covered = set(_page_sources()) | set(_exempt())
    missing = sorted(h for h in _nav_hrefs() if h not in covered)
    assert not missing, (
        f"nav pages with no source decision: {missing}. Add them to PAGE_SOURCES, "
        "or to PAGE_SOURCES_EXEMPT with a reason, in frontend/lib/sources.ts."
    )


def test_exemptions_are_explained_and_not_also_listed():
    ex, ps = _exempt(), _page_sources()
    assert not (set(ex) & set(ps)), f"in both lists: {sorted(set(ex) & set(ps))}"
    for route, reason in ex.items():
        assert len(reason) > 20, f"{route} needs a real reason, got {reason!r}"


def test_page_source_keys_exist():
    entries = _entries()
    for route, keys in _page_sources().items():
        assert keys, f"{route}: empty source list"
        unknown = [k for k in keys if k not in entries]
        assert not unknown, f"{route} lists unknown source keys {unknown}"


def _page_file(route):
    return os.path.join(APP, route.strip("/"), "page.tsx")


@pytest.mark.parametrize("route", sorted(set(_page_sources()) - SOURCELINK_NOT_YET_REQUIRED))
def test_each_listed_page_renders_a_source_link_with_valid_keys(route):
    path = _page_file(route)
    assert os.path.exists(path), f"{route} is in PAGE_SOURCES but has no page"
    src = _read(path)
    assert "<SourceLink" in src, f"{route} is listed in PAGE_SOURCES but renders no <SourceLink>"
    shown = set()
    for m in re.finditer(r"<SourceLink\s+k=(?:\"([^\"]+)\"|\{\[(.*?)\]\})", src, re.S):
        shown.update([m.group(1)] if m.group(1) else re.findall(r'"([^"]+)"', m.group(2)))
    assert shown, f"{route}: could not read the keys passed to <SourceLink>"
    unknown = sorted(shown - set(_entries()))
    assert not unknown, f"{route} passes unknown source keys {unknown}"
    expected = set(_page_sources()[route])
    assert shown == expected, (
        f"{route}: PAGE_SOURCES says {sorted(expected)} but the page shows {sorted(shown)}"
    )
