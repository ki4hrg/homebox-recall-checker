from recall_checker.config import Tier
from recall_checker.matching import is_model_like, model_tokens, norm_model, snippet

from .conftest import item


def best(matcher, it, recall_id):
    for m in matcher.match_item(it):
        if m.recall_key == f"cpsc:{recall_id}":
            return m
    return None


def test_model_normalisation():
    assert norm_model("TL-1302Z™") == "TL1302Z"
    assert is_model_like("TL1302Z")
    assert not is_model_like("2019")       # bare 4-digit numbers look like years
    assert not is_model_like("MALM")       # no digits
    assert "TL1302Z" in model_tokens("the TL 1302Z laminator")


def test_snippet_finds_original_text():
    assert "TL909-50" in snippet("This recall involves model TL909-50. Blah", "TL90950")


def test_brand_and_model_is_high(matcher):
    m = best(matcher, item(name="Laminator", manufacturer="3M", modelNumber="TL1302Z"), 11022)
    assert m and m.tier == Tier.high
    assert {e.signal for e in m.evidence} >= {"brand", "model"}


def test_model_without_brand_is_medium(matcher):
    m = best(matcher, item(name="Scotch TL1302Z laminator"), 11022)
    assert m and m.tier == Tier.medium


def test_model_variant_with_brand_is_medium(matcher):
    m = best(matcher, item(name="Laminator", manufacturer="Scotch", modelNumber="TL909"), 10451)
    assert m and m.tier == Tier.medium
    assert any(e.signal == "model-variant" for e in m.evidence)


def test_upc_is_high(matcher):
    it = item(name="Ceiling fan", fields=[{"name": "UPC", "value": "0840059614922"}])
    m = best(matcher, it, 10930)
    assert m and m.tier == Tier.high


def test_product_line_with_brand_is_medium(matcher):
    m = best(matcher, item(name="Dresser", manufacturer="IKEA", modelNumber="MALM"), 7779)
    assert m and m.tier == Tier.medium


def test_strong_name_overlap_is_medium(matcher):
    it = item(name="Rock n Play Sleeper", manufacturer="Fisher-Price")
    m = best(matcher, it, 8532)
    assert m and m.tier == Tier.medium


def test_name_overlap_capped_when_models_disagree(matcher):
    it = item(name="Thermal laminator", manufacturer="3M", modelNumber="XYZ123")
    m = best(matcher, it, 11022)
    assert m and m.tier == Tier.low


def test_brand_only_different_product_no_match(matcher):
    assert matcher.match_item(item(name="Post-it Notes", manufacturer="3M")) == []


def test_unrelated_item_no_match(matcher):
    assert matcher.match_item(item(name="Router", manufacturer="Netgear",
                                   modelNumber="R7000")) == []


def test_generic_manufacturer_ignored(matcher):
    assert matcher.match_item(item(name="Space heater", manufacturer="Generic")) == []


def test_purchase_after_recall_downgrades(matcher):
    it = item(name="Laminator", manufacturer="3M", modelNumber="TL1302Z",
              purchaseDate="2029-01-01")
    m = best(matcher, it, 11022)
    assert m and m.tier == Tier.medium
    assert any(e.signal == "purchase-date" for e in m.evidence)
