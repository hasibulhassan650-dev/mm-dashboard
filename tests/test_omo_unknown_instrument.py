"""An OMO instrument label we have never seen must keep its own name and take its direction from the amount's SIGN
(BB prints absorption negative): a new facility BB introduces must not silently flip the liquidity sign."""
from fetchers.omo import _resolve_instrument


def test_an_unknown_label_with_a_negative_amount_absorbs_and_a_positive_one_injects():
    name, direction, known = _resolve_instrument("Brand New Facility", accepted_negative=True)
    assert (name, direction, known) == ("BRAND_NEW_FACILITY", "ABSORPTION", False)
    assert _resolve_instrument("Brand New Facility", accepted_negative=False)[1] == "INJECTION"


def test_an_unknown_label_never_borrows_a_known_instruments_name():
    assert _resolve_instrument("Another Thing", accepted_negative=False)[0] not in {"SDF", "AR", "CB_REPO", "IBLF", "SLF"}
    assert _resolve_instrument("", accepted_negative=False) is None
