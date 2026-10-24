from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Protocol


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

    @property
    def key(self) -> str:
        return f"{self.source}:{self.source_id}"

    @property
    def label(self) -> str:
        return f"{self.source.upper()} Recall #{self.number}"

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("recall_date", "last_publish_date"):
            d[k] = d[k].isoformat() if d[k] else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Recall:
        d = dict(d)
        for k in ("recall_date", "last_publish_date"):
            d[k] = date.fromisoformat(d[k]) if d.get(k) else None
        return cls(**d)


class RecallSource(Protocol):
    name: str

    def fetch(self, since: date | None) -> list[Recall]:
        """Return recalls published/updated on or after `since` (all recalls when None)."""
        ...
