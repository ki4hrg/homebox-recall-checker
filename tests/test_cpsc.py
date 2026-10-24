from recall_checker.sources.cpsc import _clean_company, extract_brands


def test_normalize_fields(recalls):
    r = next(r for r in recalls if r.source_id == "11022")
    assert r.key == "cpsc:11022"
    assert r.recall_date.isoformat() == "2026-10-15"
    assert "TL1302Z" in r.description
    assert "cpsc.gov/Recalls/" in r.url
    assert r.remedies


def test_upcs_extracted(recalls):
    fan = next(r for r in recalls if r.source_id == "10930")
    assert "840059614922" in fan.upcs


def test_clean_company():
    assert _clean_company("3M Company, of St. Paul, Minnesota") == ["3M Company"]
    assert _clean_company("Walmart, Inc. of Bentonville, Arkansas") == ["Walmart, Inc."]
    assert _clean_company("Opal Vista Ltd., dba usOpal, of China") == ["Opal Vista Ltd.", "usOpal"]
    assert _clean_company("Sold At:\nAmazon.com from March 2025") == []


def test_brand_from_title_variants():
    assert "Fisher-Price" in extract_brands({"Title": "Fisher-Price Reannounces Recall of X"})
    assert "Lasko Products Inc." in extract_brands(
        {"Title": "CPSC, Lasko Products Inc. Announce Recall of Space Heaters"})
    assert "3M" in extract_brands({"Title": "Scotch Thermal Laminators Recalled by 3M Due to Burn"})
