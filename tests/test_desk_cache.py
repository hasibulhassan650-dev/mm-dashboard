"""A desk entry exists to be seen NOW: the desk reads the cut-off at the auction and BB prints it a day later.
The site caches API data for 5 minutes and pages for 5 more, and a saved mark used to wait that out ("I put
the entry and it is not on the yield curve"). Every write route must expire the cache, and every API read must
carry the tag it expires."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def test_every_desk_write_route_expires_the_cache_on_success():
    for rel in ("frontend/app/api/desk/mark/route.ts", "frontend/app/api/desk/buyback/route.ts"):
        src = _read(rel)
        assert 'revalidateTag("api", { expire: 0 })' in src, f"{rel} must expire the cache after a stored entry"
        assert src.index("if (r.ok)") < src.index("revalidateTag"), f"{rel}: only a stored entry may expire it"


def test_api_reads_carry_the_tag_the_write_routes_expire():
    assert 'tags: ["api"]' in _read("frontend/lib/api.ts")
