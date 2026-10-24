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




# --- regressions from real Homebox data (all were false positives, all dismissed) ---

KOBALT_ITEMS = [
    item(name="Kobalt 4.0Ah 40v Battery", manufacturer="Kobalt", modelNumber="KB 440-03"),
    item(name="Kobalt 40v 6.0Ah Battery", manufacturer="Kobalt", modelNumber="KB 640-03"),
    item(name="Kobalt 40v Hedge Trimmer", manufacturer="Kobalt", modelNumber="KHT 1040B-03",
         purchaseDate="2024-10-28"),
    item(name="Kobalt 40v Lawn Mower", manufacturer="Kobalt", modelNumber="KPM 1040B-03",
         purchaseDate="2025-04-08"),
    item(name="Kobalt 40v String Trimmer", manufacturer="Kobalt", modelNumber="KST 1040B-03",
         purchaseDate="2025-08-03"),
]


def test_specs_are_not_model_numbers():
    assert model_tokens("Kobalt 4.0Ah 40v Battery") == set()
    assert model_tokens("40 AH") == set()
    assert "KB44003" in model_tokens("Battery: KB 440-03")


def test_kobalt_tools_never_notify(matcher):
    for it in KOBALT_ITEMS:
        for m in matcher.match_item(it):
            assert m.tier == Tier.low, (it["name"], m.recall_key, m.evidence)
            assert not any(e.signal == "model" for e in m.evidence)


def test_real_kobalt_model_still_matches(matcher):
    # Greenworks' 24V recall lists KB 324-06; owning that battery must still be high.
    it = item(name="Kobalt 24V 3.0Ah Battery", manufacturer="Kobalt", modelNumber="KB 324-06")
    m = best(matcher, it, 10859)
    assert m and m.tier == Tier.high


def test_retailer_is_not_a_brand(matcher):
    it = item(name="Living Room Fire TV Stick 4K Plus", manufacturer="Amazon",
              purchaseDate="2026-10-14")
    assert best(matcher, it, 10128) is None


def test_post_recall_purchase_caps_at_low(matcher):
    it = item(name="Laminator", manufacturer="3M", modelNumber="TL1302Z",
              purchaseDate="2029-01-01")
    m = best(matcher, it, 11022)
    assert m and m.tier == Tier.low


def test_known_model_not_in_recall_caps_name_match(matcher):
    # The 2004 Lasko recall lists 4-digit models; owning model 754200 isn't evidence.
    it = item(name="Space Heater", manufacturer="Lasko", modelNumber="754200")
    for m in matcher.match_item(it):
        assert m.tier == Tier.low
