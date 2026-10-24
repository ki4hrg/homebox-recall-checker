from __future__ import annotations

import logging
import re
from datetime import date, datetime

import httpx

from .base import Recall

log = logging.getLogger(__name__)

# "Acme Corp. Recalls ...", "CPSC, Acme Announce Recall ...", "Acme Widgets Recalled Due to ..."
_TITLE_BRAND = re.compile(
    r"^(?:CPSC,?\s+(?:and\s+)?)?(.*?)\s+"
    r"(?:Recalls|Recalled|Recall|Reannounces|Announces|Announce|Expands|Issues)\b",
    re.I,
)
# "Scotch Thermal Laminators Recalled by 3M Due to ..."
_RECALLED_BY = re.compile(r"\bRecalled\s+by\s+(.+?)(?:\s+(?:Due|for|Because)\b|[;(]|$)", re.I)
# ", of Atlanta, Georgia" / ", of China" / " dba Foo"
_COMPANY_LOCATION = re.compile(r",?\s+of\s+[A-Z][\w .,'-]*$")
_SOLD_AT = re.compile(r"^\s*sold\s+(at|exclusively|online|by)\b", re.I)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def _names(entries: list[dict] | None, key: str = "Name") -> list[str]:
    return [e[key].strip() for e in entries or [] if e.get(key) and e[key].strip()]


def _clean_company(name: str) -> list[str]:
    """'Foo Co., Ltd., dba Bamamon, of China' -> ['Foo Co., Ltd.', 'Bamamon']."""
    name = name.split("\n")[0].strip()
    if not name or _SOLD_AT.match(name):
        return []
    name = _COMPANY_LOCATION.sub("", name).strip(" ,")
    parts = re.split(r",?\s+(?:dba|d/b/a|doing business as)\s+", name, flags=re.I)
    return [p.strip(" ,") for p in parts if p.strip(" ,")]


def extract_brands(raw: dict) -> list[str]:
    brands: list[str] = []
    title = raw.get("Title") or ""
    by = _RECALLED_BY.search(title)
    if by:
        brands.extend(_clean_company(by.group(1)))
    m = _TITLE_BRAND.match(title)
    if m and m.group(1).strip():
        brands.append(m.group(1).strip(" ,"))
    for key in ("Manufacturers", "Importers", "Distributors", "Retailers"):
        for n in _names(raw.get(key)):
            brands.extend(_clean_company(n))
    seen: set[str] = set()
    out = []
    for b in brands:
        if b.lower() not in seen:
            seen.add(b.lower())
            out.append(b)
    return out


def normalize(raw: dict) -> Recall:
    products = raw.get("Products") or []
    return Recall(
        source="cpsc",
        source_id=str(raw["RecallID"]),
        number=str(raw.get("RecallNumber") or raw["RecallID"]),
        title=(raw.get("Title") or "").strip(),
        description=(raw.get("Description") or "").strip(),
        url=raw.get("URL") or "",
        recall_date=_parse_date(raw.get("RecallDate")),
        last_publish_date=_parse_date(raw.get("LastPublishDate")),
        product_names=_names(products),
        product_types=_names(products, "Type"),
        brands=extract_brands(raw),
        upcs=[u["UPC"].strip() for u in raw.get("ProductUPCs") or [] if u.get("UPC")],
        hazards=[h for h in _names(raw.get("Hazards")) if h.lower() != "none reported."],
        remedies=_names(raw.get("Remedies")),
        images=[i["URL"] for i in raw.get("Images") or [] if i.get("URL")],
        consumer_contact=(raw.get("ConsumerContact") or "").strip(),
        units=", ".join(p["NumberOfUnits"] for p in products if p.get("NumberOfUnits")),
    )


class CPSCSource:
    name = "cpsc"

    def __init__(self, base_url: str, client: httpx.Client | None = None):
        self.base_url = base_url
        self.client = client or httpx.Client(timeout=httpx.Timeout(120.0, connect=15.0))

    def fetch(self, since: date | None) -> list[Recall]:
        params = {"format": "json"}
        if since:
            params["LastPublishDateStart"] = since.isoformat()
        else:
            params["RecallDateStart"] = "1900-01-01"
        resp = self.client.get(self.base_url, params=params)
        resp.raise_for_status()
        data = resp.json()
        recalls = []
        for raw in data:
            try:
                recalls.append(normalize(raw))
            except Exception:  # one malformed record shouldn't sink the sync
                log.exception("Skipping malformed CPSC record %s", raw.get("RecallID"))
        log.info("CPSC returned %d recalls (since=%s)", len(recalls), since)
        return recalls
