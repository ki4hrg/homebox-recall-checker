import io
import zipfile
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from recall_checker.config import Tier
from recall_checker.matching import Matcher, RecallIndex
from recall_checker.sources.nhtsa import NHTSASource, parse

from .conftest import item

SAMPLE = Path(__file__).parent / "fixtures" / "nhtsa_sample.txt"
URL = "https://nhtsa.test/FLAT_RCL_POST_2010.zip"


def lines():
    return SAMPLE.read_text(encoding="latin-1").splitlines(keepends=True)


@pytest.fixture(scope="module")
def recalls():
    return {r.source_id: r for r in parse(lines(), {"C", "E"})}


def test_filters_types_and_groups_campaigns(recalls):
    assert set(recalls) == {"26C006000", "26C003000", "26C002000", "10E049000"}
    assert {r.kind for r in recalls.values()} == {"child seat", "equipment"}
    assert parse(lines(), {"V"}) and parse(lines(), {"T"})


def test_britax_campaign_fields(recalls):
    r = recalls["26C006000"]
    assert r.key == "nhtsa:26C006000" and r.label == "NHTSA Recall #26C006000"
    assert r.recall_date == date(2026, 8, 20)
    assert r.title.startswith("Britax Child Safety, Inc. recalls ")
    assert "(child seats)" in r.title
    assert "B-SAFE 35" in r.models and "M058600" in r.models    # model + part number
    assert "BRITAX" in r.brands
    assert r.hazards and r.remedies
    assert r.url.endswith("nhtsaId=26C006000")


def test_car_seat_matching(recalls):
    m = Matcher(RecallIndex(list(recalls.values())))

    def tier(it, campno):
        hits = [x for x in m.match_item(it) if x.recall_key == f"nhtsa:{campno}"]
        return hits[0].tier if hits else None

    # model number exactly as listed, with brand
    assert tier(item(name="Infant car seat", manufacturer="Britax",
                     modelNumber="B-Safe 35"), "26C006000") == Tier.high
    # digit-free model name inside the item name: a product line, so medium with the brand
    assert tier(item(name="Graco SnugRide Turn & Slide"), "26C003000") == Tier.medium
    assert tier(item(name="SnugRide Turn & Slide"), "26C003000") is None
    # model with digits inside the name, brand in the name too
    assert tier(item(name="Britax B-Safe 35 infant seat"), "26C006000") == Tier.high
    # part number from the seat's label
    assert tier(item(name="Car seat", manufacturer="Britax",
                     modelNumber="M058600"), "26C006000") == Tier.high
    # same brand, different seat
    assert tier(item(name="Convertible car seat", manufacturer="Britax",
                     modelNumber="One4Life"), "26C006000") in (None, Tier.low)
    # equipment: bare-number model needs the brand
    assert tier(item(name="RV fridge", manufacturer="Norcold", modelNumber="1210"),
                "10E049000") == Tier.high
    assert tier(item(name="Mini fridge", manufacturer="Danby", modelNumber="1210"),
                "10E049000") is None


def test_bought_before_manufacture_caps_low(recalls):
    r = recalls["26C006000"]
    m = Matcher(RecallIndex([r]))
    early = r.manufactured_from.replace(year=r.manufactured_from.year - 2)
    hit = m.match_item(item(name="Infant seat", manufacturer="Britax",
                            modelNumber="B-Safe 35", purchaseDate=early.isoformat()))
    assert hit and hit[0].tier == Tier.low


def test_source_downloads_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("FLAT_RCL_POST_2010.txt", SAMPLE.read_bytes())
    with respx.mock() as router:
        router.get(URL).mock(return_value=httpx.Response(200, content=buf.getvalue()))
        got = NHTSASource({"C"}, URL).fetch(None)
    assert {r.source_id for r in got} == {"26C006000", "26C003000", "26C002000"}
