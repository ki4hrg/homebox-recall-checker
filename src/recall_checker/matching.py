"""Match Homebox items against recalls.

CPSC records almost never populate structured model fields, so model numbers are
pulled from free text. Signals, strongest first:

  high    UPC/EAN match; or item model number appears in the recall text AND brand matches
  medium  model number appears in the recall text but brand doesn't match (or is unknown);
          or a model prefix/variant match with a brand match
  low     brand match plus overlapping product-name words

An item purchased well after a recall was published is downgraded one tier.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from .config import Tier
from .sources.base import Recall

_SYMBOLS = re.compile(r"[™®©℠]")
_NON_ALNUM = re.compile(r"[^A-Z0-9]")
_RAW_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-./]*[A-Za-z0-9]|[A-Za-z0-9]")
_WORD = re.compile(r"[a-z0-9]+")
_DIGITS = re.compile(r"\d{8,14}")

CORP_WORDS = {
    "inc", "incorporated", "llc", "l", "c", "co", "corp", "corporation", "company", "ltd",
    "limited", "plc", "gmbh", "ag", "sa", "srl", "bv", "pty", "lp", "llp", "the", "and",
    "dba", "group", "holdings", "international", "intl", "usa", "us", "america", "north",
    "brands", "brand", "products", "industries", "enterprises", "trading", "technology",
    "technologies", "tech", "ecommerce", "e", "commerce",
}
GENERIC_BRANDS = {"generic", "unknown", "none", "na", "n a", "other", "various", "unbranded",
                  "no brand", "homemade", "diy", "custom"}
NAME_STOPWORDS = {
    "the", "and", "with", "for", "of", "a", "an", "in", "on", "to", "by", "from", "set", "kit",
    "new", "pack", "piece", "pcs", "inch", "ft", "black", "white", "gray", "grey", "red",
    "blue", "green", "silver", "small", "large", "medium", "mini", "pro", "plus", "series",
    "model", "recall", "recalls", "recalled", "due", "risk", "hazard", "injury", "serious",
    "death", "violate", "mandatory", "standard", "sold", "exclusively",
}
UPC_FIELD_NAMES = re.compile(r"upc|ean|gtin|barcode|bar code", re.I)


def norm_model(s: str) -> str:
    return _NON_ALNUM.sub("", _SYMBOLS.sub("", s).upper())


def is_model_like(norm: str) -> bool:
    """A normalized token specific enough to count as a model number."""
    if len(norm) < 4 or not any(c.isdigit() for c in norm):
        return False
    has_alpha = any(c.isalpha() for c in norm)
    # Bare numbers collide with years, unit counts, phone numbers, etc.
    return has_alpha or len(norm) >= 5


def norm_upc(s: str) -> str:
    d = re.sub(r"\D", "", s)
    return d.lstrip("0")  # UPC-A vs EAN-13 differ only by a leading zero


def brand_tokens(name: str) -> tuple[str, ...]:
    words = _WORD.findall(_SYMBOLS.sub("", name).lower().replace("&", " and "))
    return tuple(w for w in words if w not in CORP_WORDS)


def _stem(w: str) -> str:
    if len(w) > 4 and w.endswith("es") and not w.endswith("ses"):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def name_tokens(text: str, exclude: set[str] = frozenset()) -> set[str]:
    words = _WORD.findall(_SYMBOLS.sub("", text).lower())
    return {
        _stem(w) for w in words
        if len(w) >= 3 and w not in NAME_STOPWORDS and w not in exclude and not w.isdigit()
    }


def model_tokens(text: str) -> set[str]:
    """Model-like tokens in free text, including adjacent pairs ('TL 1302Z' -> 'TL1302Z')."""
    raw = [norm_model(t) for t in _RAW_TOKEN.findall(_SYMBOLS.sub("", text))]
    raw = [t for t in raw if t]
    out = {t for t in raw if is_model_like(t)}
    for a, b in zip(raw, raw[1:], strict=False):
        joined = a + b
        if len(joined) <= 20 and is_model_like(joined) and (
            any(c.isdigit() for c in a) or any(c.isdigit() for c in b)
        ):
            out.add(joined)
    return out


def snippet(text: str, model_norm: str, width: int = 80) -> str:
    """Find the original occurrence of a normalized model in text for display."""
    pattern = r"[\s\-./]?".join(re.escape(c) for c in model_norm)
    m = re.search(pattern, _SYMBOLS.sub("", text), re.I)
    if not m:
        return ""
    start, end = max(0, m.start() - width), min(len(text), m.end() + width)
    return ("…" if start else "") + text[start:end].strip() + ("…" if end < len(text) else "")


@dataclass
class Evidence:
    signal: str
    detail: str
    snippet: str = ""

    def to_dict(self) -> dict:
        return {"signal": self.signal, "detail": self.detail, "snippet": self.snippet}


@dataclass
class Match:
    recall_key: str
    tier: Tier
    score: float
    evidence: list[Evidence] = field(default_factory=list)


@dataclass
class _Prepared:
    recall: Recall
    text: str
    models: set[str]
    upcs: set[str]
    brands: list[tuple[str, ...]]
    words: set[str]           # lowercase words in title + product names (for brand-in-title)
    product_terms: set[str]   # stemmed product-name words for the low tier


class RecallIndex:
    def __init__(self, recalls: list[Recall]):
        self.prepared: dict[str, _Prepared] = {}
        self.by_model: dict[str, set[str]] = defaultdict(set)
        self.by_upc: dict[str, set[str]] = defaultdict(set)
        self.by_word: dict[str, set[str]] = defaultdict(set)
        for r in recalls:
            self._add(r)

    def __len__(self) -> int:
        return len(self.prepared)

    def _add(self, r: Recall) -> None:
        names = " ".join(r.product_names)
        text = f"{r.title}\n{names}\n{r.description}"
        brands = [b for b in (brand_tokens(x) for x in r.brands) if b]
        p = _Prepared(
            recall=r,
            text=text,
            models=model_tokens(text),
            upcs={norm_upc(u) for u in r.upcs if norm_upc(u)},
            brands=brands,
            words=set(_WORD.findall(f"{r.title} {names}".lower())),
            product_terms=name_tokens(f"{r.title} {names} {' '.join(r.product_types)}"),
        )
        self.prepared[r.key] = p
        for m in p.models:
            self.by_model[m].add(r.key)
        for u in p.upcs:
            self.by_upc[u].add(r.key)
        for b in brands:
            self.by_word[b[0]].add(r.key)
        for w in p.words:
            self.by_word[w].add(r.key)


def _item_upcs(item: dict) -> set[str]:
    out: set[str] = set()
    for f in item.get("fields") or []:
        if UPC_FIELD_NAMES.search(f.get("name", "")):
            u = norm_upc(f.get("value", ""))
            if len(u) >= 8:
                out.add(u)
    for text in (item.get("description", ""),):
        for d in _DIGITS.findall(text):
            if len(d) in (12, 13):
                out.add(norm_upc(d))
    return out


def _item_models(item: dict) -> set[str]:
    models: set[str] = set()
    m = norm_model(item.get("modelNumber") or "")
    if m and (is_model_like(m) or len(m) >= 4):
        models.add(m)
    # Model numbers often get typed into the name ("Scotch TL1302Z Laminator").
    models |= {t for t in model_tokens(item.get("name") or "") if any(c.isalpha() for c in t)}
    return models


def _item_product_line(item: dict) -> str | None:
    """An all-letters model number like 'MALM', usable only alongside a brand match."""
    raw = _SYMBOLS.sub("", item.get("modelNumber") or "").strip()
    if len(raw) >= 4 and raw.replace(" ", "").isalpha() and raw.lower() not in NAME_STOPWORDS:
        return raw
    return None


def _brand_matches(item_brand: tuple[str, ...], recall_brand: tuple[str, ...]) -> bool:
    if not item_brand or not recall_brand:
        return False
    if item_brand == recall_brand:
        return True
    a, b = set(item_brand), set(recall_brand)
    small, big = (a, b) if len(a) <= len(b) else (b, a)
    # "lithonia" vs "lithonia lighting": all of the shorter name's words appear, and
    # the overlap isn't a single tiny word.
    return small <= big and (len(small) > 1 or len(next(iter(small))) >= 3) and (
        item_brand[0] == recall_brand[0]
    )


class Matcher:
    def __init__(self, index: RecallIndex, purchase_grace_days: int = 365):
        self.index = index
        self.grace = timedelta(days=purchase_grace_days)

    def _brand_hit(self, item: dict, p: _Prepared) -> str | None:
        """Return a human-readable reason if the item's brand matches the recall."""
        manu = brand_tokens(item.get("manufacturer") or "")
        if manu and " ".join(manu) not in GENERIC_BRANDS:
            for b in p.brands:
                if _brand_matches(manu, b):
                    return f"Manufacturer “{item['manufacturer']}” ≈ recalling firm “{' '.join(b)}”"
            # e.g. manufacturer "Scotch" when the recalling firm is 3M
            if all(w in p.words for w in manu) and len(" ".join(manu)) >= 3:
                return f"Manufacturer “{item['manufacturer']}” appears in recall title/product"
            return None
        # No manufacturer recorded: accept a recalling firm's name inside the item name.
        item_words = set(_WORD.findall((item.get("name") or "").lower()))
        for b in p.brands:
            if all(w in item_words for w in b) and len(" ".join(b)) >= 3:
                return f"Item name contains recalling firm “{' '.join(b)}”"
        return None

    def _candidates(self, item: dict, models: set[str], upcs: set[str]) -> set[str]:
        idx = self.index
        keys: set[str] = set()
        for m in models:
            keys |= idx.by_model.get(m, set())
        for u in upcs:
            keys |= idx.by_upc.get(u, set())
        manu = brand_tokens(item.get("manufacturer") or "")
        if manu:
            keys |= idx.by_word.get(manu[0], set())
        else:
            for w in _WORD.findall((item.get("name") or "").lower()):
                if len(w) >= 3 and w not in NAME_STOPWORDS:
                    keys |= idx.by_word.get(w, set())
        return keys

    def match_item(self, item: dict) -> list[Match]:
        models = _item_models(item)
        upcs = _item_upcs(item)
        results: list[Match] = []
        for key in self._candidates(item, models, upcs):
            m = self._score(item, self.index.prepared[key], models, upcs)
            if m:
                results.append(m)
        results.sort(key=lambda m: m.score, reverse=True)
        return results

    def _score(self, item: dict, p: _Prepared, models: set[str], upcs: set[str]) -> Match | None:
        r = p.recall
        evidence: list[Evidence] = []
        tier: Tier | None = None
        score = 0.0

        upc_hits = upcs & p.upcs
        if upc_hits:
            evidence.append(Evidence("upc", f"UPC {', '.join(sorted(upc_hits))} listed in recall"))
            tier, score = Tier.high, 3.9

        brand_reason = self._brand_hit(item, p)

        exact = sorted(models & p.models)
        if exact:
            model = max(exact, key=len)
            evidence.append(Evidence("model", f"Model {model} appears in recall",
                                     snippet(p.text, model)))
            t, s = (Tier.high, 3.5) if brand_reason else (Tier.medium, 2.5)
            if tier is None or t > tier:
                tier, score = t, s
        elif brand_reason and (line := _item_product_line(item)) and re.search(
            rf"\b{re.escape(line)}\b", p.text, re.I
        ):
            # Alphabetic model/product-line names (IKEA "MALM") are too generic on their own.
            evidence.append(Evidence("model", f"Product line “{line}” appears in recall",
                                     snippet(p.text, norm_model(line))))
            tier, score = Tier.medium if tier is None else tier, max(score, 2.4)
        elif brand_reason and models:
            # Variant/prefix: recall says "TL909", item says "TL909-50" (or vice versa).
            for im in models:
                if len(im) < 5:
                    continue
                for rm in p.models:
                    if len(rm) >= 5 and (rm.startswith(im) or im.startswith(rm)):
                        evidence.append(Evidence(
                            "model-variant", f"Item model {im} ≈ recall model {rm}",
                            snippet(p.text, rm)))
                        if tier is None:
                            tier, score = Tier.medium, 2.2
                        break
                else:
                    continue
                break

        if brand_reason:
            evidence.insert(0, Evidence("brand", brand_reason))
            if tier is None:
                exclude = set(brand_tokens(item.get("manufacturer") or ""))
                for b in p.brands:
                    exclude |= set(b)
                item_terms = name_tokens(item.get("name") or "", exclude)
                shared = item_terms & (p.product_terms - exclude)
                if shared and len(shared) / max(len(item_terms), 1) >= 0.5 and any(
                    len(w) >= 4 for w in shared
                ):
                    evidence.append(Evidence(
                        "name", f"Product words in common: {', '.join(sorted(shared))}"))
                    ratio = len(shared) / max(len(item_terms), 1)
                    # Every distinctive word of the item name (at least two) is in the recall.
                    # ...unless both sides list model numbers and none of them agreed.
                    strong = ratio == 1.0 and len(shared) >= 2 and not (models and p.models)
                    tier = Tier.medium if strong else Tier.low
                    score = (2.0 if strong else 1.0) + ratio * 0.5

        if tier is None:
            return None

        purchased = item.get("purchaseDate")
        if purchased and r.recall_date:
            try:
                bought = date.fromisoformat(purchased[:10])
            except ValueError:
                bought = None
            if bought and bought > r.recall_date + self.grace:
                evidence.append(Evidence(
                    "purchase-date",
                    f"Purchased {bought} — more than {self.grace.days} days after the "
                    f"recall ({r.recall_date}); likely a post-recall unit"))
                if tier > Tier.low:
                    tier = Tier(tier - 1)
                score -= 1.0

        return Match(r.key, tier, round(score, 3), evidence)
