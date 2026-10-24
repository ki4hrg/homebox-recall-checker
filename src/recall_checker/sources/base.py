from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Protocol

_DATE_FIELDS = ("recall_date", "last_publish_date", "manufactured_from", "manufactured_to")


@dataclass
class Recall:
    source: str
    source_id: str
    number: str
    title: str
    description: str
    url: str
    recall_date: date | None
    last_publish_date: date | None
    product_names: list[str] = field(default_factory=list)
    product_types: list[str] = field(default_factory=list)
    brands: list[str] = field(default_factory=list)
    upcs: list[str] = field(default_factory=list)
    hazards: list[str] = field(default_factory=list)
    remedies: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    consumer_contact: str = ""
    units: str = ""
    # Structured model / part numbers when the source provides them (NHTSA does, CPSC doesn't).
    models: list[str] = field(default_factory=list)
    # Window in which affected units were made, if known.
    manufactured_from: date | None = None
    manufactured_to: date | None = None
    kind: str = ""  # e.g. "child seat", "equipment"

    @property
    def key(self) -> str:
        return f"{self.source}:{self.source_id}"

    @property
    def label(self) -> str:
        return f"{self.source.upper()} Recall #{self.number}"

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in _DATE_FIELDS:
            d[k] = d[k].isoformat() if d[k] else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Recall:
        d = dict(d)
        for k in _DATE_FIELDS:
            d[k] = date.fromisoformat(d[k]) if d.get(k) else None
        return cls(**d)


class RecallSource(Protocol):
    name: str
    # Bump when normalization changes, so stored recalls get re-fetched and re-parsed.
    parser_version: int

    def fetch(self, since: date | None) -> list[Recall]:
        """Return recalls published/updated on or after `since` (all recalls when None).
        Sources without a date filter may return everything; unchanged recalls are no-ops."""
        ...
