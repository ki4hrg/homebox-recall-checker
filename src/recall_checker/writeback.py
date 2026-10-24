"""Mark matched items inside Homebox: a tag plus a maintenance entry."""
from __future__ import annotations

import logging
from datetime import date

from .config import Tier
from .homebox import HomeboxClient
from .messages import maintenance_text
from .sources.base import Recall

log = logging.getLogger(__name__)


class WriteBack:
    def __init__(self, client: HomeboxClient, tag_name: str):
        self.client = client
        self.tag_name = tag_name
        self._tag_id: str | None = None

    def tag_id(self) -> str:
        if self._tag_id:
            return self._tag_id
        for tag in self.client.list_tags():
            if (tag.get("name") or "").strip().lower() == self.tag_name.lower():
                self._tag_id = tag["id"]
                return self._tag_id
        created = self.client.create_tag(
            self.tag_name,
            description="Items with a matching product recall (homebox-recall-checker)",
            color="#dc2626",
        )
        log.info("Created Homebox tag %r", self.tag_name)
        self._tag_id = created["id"]
        return self._tag_id

    def apply(self, item_id: str, recall: Recall, tier: Tier, ui_link: str = "") -> None:
        """Idempotent: re-running never duplicates the tag or the maintenance entry."""
        entity = self.client.get_entity(item_id)
        tag_ids = [t["id"] for t in entity.get("tags") or []]
        tid = self.tag_id()
        if tid not in tag_ids:
            self.client.set_entity_tags(item_id, tag_ids + [tid])

        name = recall.label
        existing = self.client.list_maintenance(item_id)
        if not any((e.get("name") or "") == name for e in existing):
            self.client.add_maintenance(
                item_id, name=name,
                description=maintenance_text(recall, tier, ui_link),
                scheduled_date=date.today().isoformat(),
            )
        log.info("Wrote recall %s back to Homebox item %s", recall.key, item_id)
