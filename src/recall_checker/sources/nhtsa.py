"""NHTSA recalls from the published flat file.

NHTSA's JSON API is organised around vehicle make/model/year lookups, which can't enumerate
child seats or aftermarket equipment. The flat file (regenerated daily) has every campaign,
one row per affected make/model/part, with a type code per row:

  V vehicle · E equipment · C child restraint (car seats) · T tire

Only the post-2010 archive is used: the pre-2010 one has no part numbers, mostly repeats the
make as the model, and car seats that old are long past their expiry date.
Field layout: https://static.nhtsa.gov/odi/ffdd/rcl/RCL.txt
"""
from __future__ import annotations

import io
import logging
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import date

import httpx

from .base import Recall

log = logging.getLogger(__name__)

FLAT_FILE_URL = "https://static.nhtsa.gov/odi/ffdd/rcl/FLAT_RCL_POST_2010.zip"
RECALL_URL = "https://www.nhtsa.gov/recalls?nhtsaId={campno}"
KINDS = {"C": "child seat", "E": "equipment", "T": "tire", "V": "vehicle"}

# 0-based column indexes (RCL.txt numbers fields from 1)
CAMPNO, MAKE, MODEL, YEAR, MFGCAMPNO, COMPNAME, MFGNAME, BGMAN, ENDMAN, TYPE, POTAFF, ODATE = (
    1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12)
RCDATE, DATEA, DESC, CONSEQUENCE, CORRECTIVE, NOTES = 15, 16, 19, 20, 21, 22
MFR_COMP_NAME, MFR_COMP_DESC, MFR_COMP_PTNO = 24, 25, 26
N_FIELDS = 29


def _date(s: str) -> date | None:
    s = s.strip()
    if len(s) != 8 or not s.isdigit():
        return None
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:]))
    except ValueError:
        return None


def _add(seq: list[str], value: str) -> None:
    value = " ".join(value.split())
    if value and value.upper() not in {v.upper() for v in seq}:
        seq.append(value)


@dataclass
class _Campaign:
    campno: str
    kind: str
    mfgname: str = ""
    desc: str = ""
    consequence: str = ""
    corrective: str = ""
    notes: str = ""
    components: list[str] = field(default_factory=list)
    makes: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)       # "MAKE MODEL", display
    model_numbers: list[str] = field(default_factory=list)  # bare models + part numbers
    part_names: list[str] = field(default_factory=list)
    received: date | None = None
    created: date | None = None
    made_from: date | None = None
    made_to: date | None = None
    units: int = 0

    def add_row(self, r: list[str]) -> None:
        self.mfgname = self.mfgname or r[MFGNAME].strip()
        self.desc = self.desc or r[DESC].strip()
        self.consequence = self.consequence or r[CONSEQUENCE].strip()
        self.corrective = self.corrective or r[CORRECTIVE].strip()
        self.notes = self.notes or r[NOTES].strip()
        make, model = r[MAKE].strip(), r[MODEL].strip()
        _add(self.makes, make)
        if model and model.upper() != make.upper():
            _add(self.models, f"{make} {model}".strip())
            _add(self.model_numbers, model)
        _add(self.model_numbers, r[MFR_COMP_PTNO])
        _add(self.part_names, r[MFR_COMP_NAME])
        _add(self.components, r[COMPNAME])
        self.received = self.received or _date(r[RCDATE])
        created = _date(r[DATEA])
        if created and (not self.created or created > self.created):
            self.created = created
        start, end = _date(r[BGMAN]), _date(r[ENDMAN])
        if start and (not self.made_from or start < self.made_from):
            self.made_from = start
        if end and (not self.made_to or end > self.made_to):
            self.made_to = end
        try:
            self.units = max(self.units, int(r[POTAFF] or 0))
        except ValueError:
            pass

    def to_recall(self) -> Recall:
        kind = KINDS.get(self.kind, "equipment")
        names = [m.title() for m in self.models] or [m.title() for m in self.makes]
        shown = ", ".join(names[:4]) + (f" and {len(names) - 4} more" if len(names) > 4 else "")
        plural = "child seats" if kind == "child seat" else kind
        return Recall(
            source="nhtsa",
            source_id=self.campno,
            number=self.campno,
            title=f"{self.mfgname or self.makes[0].title()} recalls {shown} ({plural})",
            description=self.desc,
            url=RECALL_URL.format(campno=self.campno),
            recall_date=self.received,
            last_publish_date=self.created or self.received,
            product_names=self.models + self.part_names,
            product_types=[kind, *self.components],
            brands=[*self.makes, self.mfgname] if self.mfgname else list(self.makes),
            hazards=[self.consequence] if self.consequence else [],
            remedies=[self.corrective] if self.corrective else [],
            consumer_contact=self.notes,
            units=f"{self.units:,}" if self.units else "",
            models=self.model_numbers,
            manufactured_from=self.made_from,
            manufactured_to=self.made_to,
            kind=kind,
        )


def parse(lines, types: set[str]) -> list[Recall]:
    """Group flat-file rows into one Recall per campaign, keeping only `types`."""
    campaigns: dict[str, _Campaign] = {}
    skipped = 0
    for line in lines:
        # Cheap pre-check on the type column before splitting a 17 KB row in full.
        head = line.split("\t", TYPE + 1)
        if len(head) <= TYPE or head[TYPE].strip() not in types:
            continue
        r = line.rstrip("\r\n").split("\t")
        if len(r) != N_FIELDS:
            skipped += 1
            continue
        campno = r[CAMPNO].strip()
        if campno not in campaigns:
            campaigns[campno] = _Campaign(campno, r[TYPE].strip())
        campaigns[campno].add_row(r)
    if skipped:
        log.warning("Skipped %d malformed NHTSA rows", skipped)
    return [c.to_recall() for c in campaigns.values()]


class NHTSASource:
    name = "nhtsa"
    parser_version = 1

    def __init__(self, types: set[str], url: str = FLAT_FILE_URL,
                 client: httpx.Client | None = None):
        self.types = types
        self.url = url
        self.client = client or httpx.Client(timeout=httpx.Timeout(300.0, connect=15.0),
                                             follow_redirects=True)

    def fetch(self, since: date | None) -> list[Recall]:
        # No date filter exists for the flat file; the whole file (~15 MB zipped) is fetched
        # and unchanged campaigns are no-ops in the database.
        with tempfile.TemporaryFile() as tmp:
            with self.client.stream("GET", self.url) as resp:
                resp.raise_for_status()
                for chunk in resp.iter_bytes():
                    tmp.write(chunk)
            tmp.seek(0)
            with zipfile.ZipFile(tmp) as zf:
                name = next(n for n in zf.namelist() if n.lower().endswith(".txt"))
                with zf.open(name) as fh:
                    text = io.TextIOWrapper(fh, encoding="latin-1", newline="")
                    recalls = parse(text, self.types)
        log.info("NHTSA returned %d campaigns of types %s", len(recalls),
                 ",".join(sorted(self.types)))
        return recalls
