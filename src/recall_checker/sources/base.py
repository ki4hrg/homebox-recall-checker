from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Protocol
from urllib.parse import urlsplit

import httpx

_DATE_FIELDS = ("recall_date", "last_publish_date", "manufactured_from", "manufactured_to")


def http_url(value: str) -> str:
    """``value`` if it is an absolute http(s) URL with a host, else "".

    Recall links come from the feeds and become ``href``/``src`` in the UI and links in
    notifications and Homebox. Anything else (``javascript:``, ``data:``, a relative path) is
    dropped, so a feed can't put script behind a link.
    """
    value = (value or "").strip()
    try:
        parts = urlsplit(value)
    except ValueError:
        return ""
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return ""
    return value


class FeedTooLarge(RuntimeError):
    """A feed answered with more than the source's size cap."""


def read_limited(resp: httpx.Response, limit: int, write) -> int:
    """Stream ``resp``'s body into ``write`` and return its size; refuse more than ``limit``.

    Checks ``Content-Length`` first, then counts what arrives, since the header can be absent
    or wrong. Keeps a huge or hostile answer from filling memory or disk on a small host.
    """
    declared = resp.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise FeedTooLarge(f"{resp.url.host} answered {declared} bytes, over the {limit} cap")
    size = 0
    for chunk in resp.iter_bytes():
        size += len(chunk)
        if size > limit:
            raise FeedTooLarge(f"{resp.url.host} answered over the {limit}-byte cap")
        write(chunk)
    return size


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

    @property
    def link(self) -> str:
        """The recall's page, or "" if the feed gave something other than an http(s) URL."""
        return http_url(self.url)

    @property
    def image_links(self) -> list[str]:
        return [u for u in map(http_url, self.images) if u]

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
