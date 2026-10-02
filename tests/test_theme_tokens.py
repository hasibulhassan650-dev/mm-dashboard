"""Every colour a page uses must exist in BOTH themes.

Light/dark parity has broken on this dashboard before: a token defined only in
the dark block renders as nothing in light mode, which on a numbers page means
white-on-white figures -- data that is present but unreadable, the worst failure
mode for a desk tool because it looks like an empty cell rather than a bug.

So this scans every `var(--token)` referenced by the components and asserts each
one is declared in globals.css for the light theme AND redefined (or inherited
by an explicit rule) for dark. It is a static check -- it cannot tell you the
result looks good, only that nothing will resolve to nothing.
"""
import os
import re
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

FRONTEND = os.path.join(_ROOT, "frontend")
CSS = os.path.join(FRONTEND, "app", "globals.css")

# Tokens the browser supplies or that are declared inline on an element rather
# than in globals.css. `currentColor` is CSS, not a custom property.
_BUILTIN = {"--sans", "--mono"}

_VAR_USE = re.compile(r"var\(\s*(--[a-zA-Z0-9-]+)")
_VAR_DEF = re.compile(r"(--[a-zA-Z0-9-]+)\s*:")


def _css():
    with open(CSS, encoding="utf-8") as fh:
        return fh.read()


def _defined_tokens(text: str) -> set:
    return set(_VAR_DEF.findall(text))


def _block(text: str, selector_pattern: str) -> str:
    """The body of every brace-group whose selector matches."""
    out = []
    for m in re.finditer(selector_pattern + r"[^{}]*\{", text, re.IGNORECASE):
        start, depth, i = m.end(), 1, m.end()
        while i < len(text) and depth:
            depth += 1 if text[i] == "{" else -1 if text[i] == "}" else 0
            i += 1
        out.append(text[start:i])
    return "\n".join(out)


# Every page and component added or reworked for the schedule and liquidity
# work. Keep this list current: a file dropped from it is a file nobody checks.
_SCANNED = [
    "app/schedule/page.tsx",
    "app/forecast/page.tsx",
    "app/drilldown/page.tsx",
    "components/ScheduleChart.tsx",
    "components/HorizonControl.tsx",
    "components/LadderTable.tsx",
    "components/OmoMaturityLadder.tsx",
]


def _sources():
    out = {}
    for r in _SCANNED:
        p = os.path.join(FRONTEND, *r.split("/"))
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                out[r] = fh.read()
    return out


def test_every_scanned_file_exists():
    # A scan over nothing passes vacuously; fail loudly if a file was renamed
    # or removed without updating the list.
    missing = [r for r in _SCANNED if r not in _sources()]
    assert not missing, f"listed for scanning but not found: {missing}"


@pytest.mark.parametrize("rel", sorted(_sources()))
def test_every_token_used_is_defined_somewhere(rel):
    text = _sources()[rel]
    defined = _defined_tokens(_css()) | _BUILTIN
    used = set(_VAR_USE.findall(text))
    missing = sorted(used - defined)
    assert not missing, f"{rel} uses tokens that globals.css never defines: {missing}"


def test_no_token_is_defined_in_only_one_theme():
    """The failure this catches: a token declared ONLY inside the dark (or only
    the light) theme block resolves to nothing in the other theme.

    This app declares base values on :root and overrides them per theme in
    html[data-theme="dark"] and html[data-theme="light"]. A token on :root is
    therefore always resolvable, and one deliberately identical in both themes
    legitimately appears nowhere else -- so "must be redefined in dark" is the
    wrong rule. The real rule is: resolvable in BOTH themes, which means on
    :root, or in both theme blocks.
    """
    css = _css()
    base = _defined_tokens(_block(css, r":root"))
    dark = _defined_tokens(_block(css, r'html\[data-theme="dark"\]'))
    light = _defined_tokens(_block(css, r'html\[data-theme="light"\]'))
    assert dark and light, "theme blocks not found — the selectors in globals.css changed"

    used = set()
    for text in _sources().values():
        used |= set(_VAR_USE.findall(text))

    one_theme_only = sorted(
        t for t in used
        if t not in base and t not in _BUILTIN and ((t in dark) != (t in light))
    )
    assert not one_theme_only, (
        "these tokens exist in one theme only, so they resolve to nothing in the "
        f"other: {one_theme_only}"
    )
