import json
from pathlib import Path

import pytest

from recall_checker.matching import Matcher, RecallIndex
from recall_checker.sources.cpsc import normalize

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def raw_recalls() -> list[dict]:
    return json.loads((FIXTURES / "cpsc_sample.json").read_text())


@pytest.fixture(scope="session")
def recalls(raw_recalls):
    return [normalize(r) for r in raw_recalls]


@pytest.fixture(scope="session")
def matcher(recalls):
    return Matcher(RecallIndex(recalls))


def item(**kw) -> dict:
    base = {"id": kw.get("name", "x"), "name": "", "description": "", "manufacturer": "",
            "modelNumber": "", "serialNumber": "", "purchaseDate": None, "tags": [],
            "fields": [], "location": "", "updatedAt": "2026-01-01T00:00:00Z"}
    base.update(kw)
    return base
