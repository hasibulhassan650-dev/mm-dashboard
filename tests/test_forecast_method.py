"""The /research page may never describe a forecasting method the code no longer uses.

The page explains the method in plain words, and every constant in that
explanation (minimum prints, lookback windows, EWMA lambda, bootstrap draws,
thresholds, the anchor tenor, the model list) is read from
frontend/lib/forecastMethod.json, which forecast/method_spec.py generates from
the very modules that run the forecast. These tests exist so that:

  (a) changing a constant in the forecasting code without regenerating the JSON
      FAILS the build (fix: python -m forecast.method_spec --write);
  (b) the model list the front end knows (the ForecastModel union in lib/api.ts,
      the MODEL_LABEL / MODEL_ORDER tables on the research page) is exactly the
      model list the code runs, so a model can neither silently drop off the
      page nor appear on it without existing;
  (c) the new method component types no number about the method by hand: visible
      text carries no digit and no 'number + unit' literal, so a figure can only
      get onto the page through the JSON or the API payload.

A literal guard that cannot fail is worthless, so (c) also proves itself against
a known-bad snippet.
"""
import io
import json
import os
import re
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)



@pytest.fixture(autouse=True)
def _the_forecast_package_not_the_api_router():
    """Other tests put api/routers on sys.path, where forecast.py (the API router) shadows the root forecast/ package
    for the rest of the run. Import the package this file is about, then put the path back as found."""
    saved_path, saved_mods = list(sys.path), {k: v for k, v in sys.modules.items() if k == "forecast" or k.startswith("forecast.")}
    if "forecast" in sys.modules and not hasattr(sys.modules["forecast"], "__path__"):
        for k in saved_mods:
            del sys.modules[k]
    sys.path[:] = [_ROOT] + [p for p in sys.path if os.path.basename(os.path.normpath(p)) != "routers"]
    try:
        yield
    finally:
        sys.path[:] = saved_path
        for k in [k for k in sys.modules if k == "forecast" or k.startswith("forecast.")]:
            del sys.modules[k]
        sys.modules.update(saved_mods)


FRONTEND = os.path.join(_ROOT, "frontend")
SPEC_JSON = os.path.join(FRONTEND, "lib", "forecastMethod.json")
API_TS = os.path.join(FRONTEND, "lib", "api.ts")
PAGE = os.path.join(FRONTEND, "app", "research", "page.tsx")
COMPONENT = os.path.join(FRONTEND, "components", "ForecastMethod.tsx")


def _read(p):
    return io.open(p, encoding="utf-8").read()


# ── (a) the committed JSON is what the code says ──────────────────────────────

def test_committed_spec_equals_the_code():
    from forecast.method_spec import spec
    committed = json.loads(_read(SPEC_JSON))
    live = json.loads(json.dumps(spec()))      # normalise tuples -> lists
    assert committed == live, (
        "frontend/lib/forecastMethod.json is out of date with the forecasting code. "
        "The research page would describe a method the code no longer uses. "
        "Regenerate it:  python -m forecast.method_spec --write")


def test_spec_values_are_real_not_placeholders():
    """Every leaf must be a real value; a None would render as a blank on the page."""
    def leaves(x):
        if isinstance(x, dict):
            return [y for v in x.values() for y in leaves(v)]
        if isinstance(x, list):
            return [y for v in x for y in leaves(v)]
        return [x]
    for v in leaves(json.loads(_read(SPEC_JSON))):
        assert v is not None


def test_the_two_band_multipliers_agree():
    """The stored band uses band_half_width's own z; run_forecast's Z95 is the fallback.
    Two copies of one constant is how a band quietly changes width in one path only."""
    from forecast.method_spec import spec
    b = spec()["band"]
    assert b["z"] == b["fallback_z"]


# ── (b) the model list the front end knows is the one the code runs ──────────

def _union_members():
    src = _read(API_TS)
    m = re.search(r"export type ForecastModel\s*=\s*([^;]+);", src)
    assert m, "ForecastModel union not found in lib/api.ts"
    return re.findall(r'"([a-z_]+)"', m.group(1))


def _table_keys(name):
    src = _read(PAGE)
    m = re.search(name + r"[^=]*=\s*\{(.*?)\};", src, re.S)
    assert m, f"{name} not found in research/page.tsx"
    return re.findall(r"\b([a-z_]+)\s*:", m.group(1))


def _order_members():
    m = re.search(r"const MODEL_ORDER[^=]*=\s*\[(.*?)\]", _read(PAGE), re.S)
    assert m, "MODEL_ORDER not found in research/page.tsx"
    return re.findall(r'"([a-z_]+)"', m.group(1))


def test_front_end_model_list_equals_the_code():
    models = set(json.loads(_read(SPEC_JSON))["models"])
    assert set(_union_members()) == models, "ForecastModel union in lib/api.ts differs from MODELS"
    assert set(_table_keys("MODEL_LABEL")) == models, "MODEL_LABEL on the research page differs from MODELS"
    assert set(_order_members()) == models, "MODEL_ORDER on the research page differs from MODELS"


# ── (c) no hand-typed method numbers in the component's visible text ──────────

# 'number + unit' literals that describe the method or its measured results.
_NUM_UNIT = re.compile(
    r"\d[\d,.]*\s*(?:bps?|basis|%|pp|draws?|resamples?|auctions?|prints?|rows?|years?|yrs?|days?|x|×)\b"
    r"|\d[\d,.]*\s*%",
    re.I)


def _jsx_region(src: str) -> str:
    """The component's returned JSX: everything from its `return (` onward, with
    comments, style attributes and import lines removed."""
    i = src.index("export default function")
    j = src.index("return (", i)
    region = src[j:]
    region = re.sub(r"/\*.*?\*/", "", region, flags=re.S)
    region = re.sub(r"(?m)^\s*//.*$", "", region)
    region = re.sub(r"style=\{\{[^}]*\}\}", "", region)       # inline style objects
    region = re.sub(r"style=\{[A-Za-z_.]+\}", "", region)     # style={someConst}
    return region


def _visible_text_chunks(src: str):
    """Text a reader can see: JSX text between tags/expressions, plus title=/sub= strings."""
    region = _jsx_region(src)
    # text that follows a tag end or an expression end, up to the next tag or expression
    for m in re.finditer(r"(?:(?<!=)>|\})([^<>{}]+)(?=<|\{)", region):
        yield m.group(1)
    for m in re.finditer(r'\b(?:title|sub)="([^"]*)"', region):
        yield m.group(1)


def _string_literals(src: str):
    region = _jsx_region(src)
    for m in re.finditer(r'"([^"\n]*)"|`([^`]*)`', region):
        yield m.group(1) if m.group(1) is not None else m.group(2)


def _violations(src: str):
    bad = []
    for t in _visible_text_chunks(src):
        if re.search(r"\d", t):
            bad.append(f"digit in visible text: {t.strip()[:80]!r}")
    for t in _string_literals(src):
        # class names / css values carry digits legitimately (grid12, spans); a
        # number glued to a unit word is what describes the method.
        if _NUM_UNIT.search(t):
            bad.append(f"number+unit literal: {t.strip()[:80]!r}")
    return bad


def test_the_literal_guard_can_fail():
    """Rule 9: a guard that cannot fail is wrong. Prove it catches the real offences."""
    snippet = '''export default function X() {
  return (
    <div>
      <p>The band holds for 94.3% of prints and uses 2,000 draws.</p>
      <p>Window of {n} auctions, 20 bps typical.</p>
    </div>
  );
}'''
    found = _violations(snippet)
    assert any("94.3%" in v for v in found), found
    assert any("2,000" in v for v in found), found
    assert any("20 bps" in v for v in found), found


def test_method_component_exists_and_reads_the_json():
    src = _read(COMPONENT)
    assert "@/lib/forecastMethod.json" in src, "component must import the generated spec"
    assert 'id="method"' in src, "the /research#method anchor is linked from Explore"


def test_method_component_has_no_hand_typed_method_numbers():
    bad = _violations(_read(COMPONENT))
    assert not bad, (
        "ForecastMethod.tsx types a number into visible text. Constants must come from "
        "forecastMethod.json and measured figures from the /api/forecast payload:\n  "
        + "\n  ".join(bad))


def test_research_page_places_the_component_and_explore_links_to_it():
    assert "<ForecastMethod" in _read(PAGE)
    explore = _read(os.path.join(FRONTEND, "app", "explore", "page.tsx"))
    assert "/research#method" in explore
