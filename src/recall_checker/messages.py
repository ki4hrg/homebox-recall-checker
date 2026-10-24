"""Human-readable text for notifications and Homebox maintenance entries."""
from __future__ import annotations

from .config import Tier
from .sources.base import Recall

MAX_IN_NOTIFICATION = 20


def maintenance_text(recall: Recall, tier: Tier, ui_link: str = "") -> str:
    lines = [recall.title, ""]
    if recall.hazards:
        lines += ["Hazard: " + " ".join(recall.hazards), ""]
    if recall.remedies:
        lines += ["Remedy: " + " ".join(recall.remedies), ""]
    if recall.consumer_contact:
        lines += ["Contact: " + recall.consumer_contact, ""]
    lines.append(f"Recall date: {recall.recall_date}  ·  Match confidence: {tier.name}")
    lines.append(recall.url)
    if ui_link:
        lines.append(f"Review: {ui_link}")
    return "\n".join(lines)


def notification(entries: list[dict], ui_base: str = "") -> tuple[str, str]:
    """entries: dicts with item, recall, tier, item_link, match_id."""
    n = len(entries)
    title = (
        f"Recall alert: {entries[0]['item']['name']}" if n == 1
        else f"Recall alert: {n} possible recalls on your Homebox items"
    )
    parts = []
    for e in entries[:MAX_IN_NOTIFICATION]:
        item, recall, tier = e["item"], e["recall"], e["tier"]
        block = [f"**{item['name']}** ({tier.name} confidence)"]
        if item.get("location"):
            block[0] += f" — {item['location']}"
        block.append(f"[{recall.title}]({recall.url})")
        if recall.hazards:
            block.append(f"Hazard: {' '.join(recall.hazards)[:300]}")
        if recall.remedies:
            block.append(f"Remedy: {' '.join(recall.remedies)[:300]}")
        links = [f"[Homebox item]({e['item_link']})"]
        if ui_base:
            links.append(f"[Review match]({ui_base}/match/{e['match_id']})")
        block.append(" · ".join(links))
        parts.append("\n".join(block))
    if n > MAX_IN_NOTIFICATION:
        more = f"…and {n - MAX_IN_NOTIFICATION} more."
        if ui_base:
            more += f" See {ui_base}"
        parts.append(more)
    return title, "\n\n".join(parts)
