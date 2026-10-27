"""One check cycle: sync recalls -> sync items -> match -> notify -> write back."""
from __future__ import annotations

import ctypes
import gc
import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

from . import messages
from .config import Settings, Tier
from .db import DB, now
from .homebox import HomeboxClient, is_trackable, slim_item
from .matching import Matcher, RecallIndex
from .notify.base import Notifier
from .sources.base import RecallSource
from .writeback import WriteBack

log = logging.getLogger(__name__)

SYNC_OVERLAP = timedelta(days=2)


def _release_memory() -> None:
    """Hand freed heap back to the OS. CPython's allocator keeps it otherwise, so a
    container that peaked at a few hundred MB once stays that size until restarted."""
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):  # not glibc (musl, macOS): best effort only
        pass


@dataclass
class RunResult:
    started_at: str = ""
    duration_s: float = 0.0
    recalls_fetched: int = 0
    recalls_changed: int = 0
    recalls_total: int = 0
    items_total: int = 0
    items_refreshed: int = 0
    matches_found: int = 0
    new_matches: int = 0
    notified: int = 0
    written_back: int = 0
    dry_run: bool = False
    errors: list[str] = field(default_factory=list)


class Runner:
    def __init__(self, settings: Settings, db: DB, sources: list[RecallSource],
                 homebox: HomeboxClient, notifier: Notifier):
        self.s = settings
        self.db = db
        self.sources = sources
        self.homebox = homebox
        self.notifier = notifier
        self.writeback = WriteBack(homebox, settings.recall_tag_name)
        self._lock = threading.Lock()
        self._index: RecallIndex | None = None

    @property
    def running(self) -> bool:
        return self._lock.locked()

    def item_link(self, item_id: str) -> str:
        return f"{self.s.homebox_link_base}/item/{item_id}"

    def ui_link(self, match_id: int) -> str:
        return f"{self.s.public_url.rstrip('/')}/match/{match_id}" if self.s.public_url else ""

    # --- steps ---
    def sync_recalls(self, res: RunResult) -> None:
        for source in self.sources:
            # One feed being down must not stop matching against the others.
            try:
                self._sync_source(source, res)
            except Exception as exc:
                log.exception("Recall sync failed for %s", source.name)
                res.errors.append(f"{source.name} sync: {exc}")
        # Rebuilt every run (a few seconds) rather than cached between runs: the index is
        # ~150 MB for the full CPSC + NHTSA set, and the checker idles 24h between uses.
        self._index = RecallIndex(self.db.all_recalls())
        res.recalls_total = len(self._index)

    def _sync_source(self, source: RecallSource, res: RunResult) -> None:
        last = self.db.get_meta(f"{source.name}_last_sync")
        version = str(source.parser_version)
        reparse = self.db.get_meta(f"{source.name}_parser_version") != version
        since = date.fromisoformat(last) - SYNC_OVERLAP if last and not reparse else None
        if reparse and last:
            log.info("%s parser changed; re-fetching all recalls", source.name)
        today = date.today()
        recalls = source.fetch(since)
        changed = self.db.upsert_recalls(recalls)
        res.recalls_fetched += len(recalls)
        res.recalls_changed += len(changed)
        self.db.set_meta(f"{source.name}_last_sync", today.isoformat())
        self.db.set_meta(f"{source.name}_parser_version", version)

    def sync_items(self, res: RunResult) -> None:
        known = self.db.item_versions()
        seen: set[str] = set()
        for summary in self.homebox.iter_entities():
            if not is_trackable(summary):
                continue
            iid = summary["id"]
            seen.add(iid)
            if known.get(iid) == summary.get("updatedAt"):
                continue
            detail = self.homebox.get_entity(iid)
            if not is_trackable(detail):
                seen.discard(iid)
                continue
            self.db.upsert_item(slim_item(detail))
            res.items_refreshed += 1
        gone = set(known) - seen
        if gone:
            self.db.deactivate_items(gone)
        res.items_total = len(seen)

    def match(self, res: RunResult) -> None:
        assert self._index is not None
        matcher = Matcher(self._index, self.s.purchase_grace_days)
        for item in self.db.active_items():
            for m in matcher.match_item(item):
                res.matches_found += 1
                evidence = [e.to_dict() for e in m.evidence]
                if self.db.insert_match(item["id"], m.recall_key, int(m.tier), m.score, evidence):
                    res.new_matches += 1

    def notify(self, res: RunResult) -> None:
        pending = self.db.pending_notifications(int(self.s.min_notify_tier))
        if not pending:
            return
        if not self.notifier.configured:
            log.warning("%d matches pending notification but no notifier is configured",
                        len(pending))
            return
        entries = []
        for row in pending:
            item, recall = self.db.get_item(row["item_id"]), self.db.get_recall(row["recall_key"])
            if not item or not recall:
                continue
            entries.append({
                "item": item, "recall": recall, "tier": Tier(row["tier"]),
                "item_link": self.item_link(item["id"]), "match_id": row["id"],
            })
        if not entries:
            return
        title, body = messages.notification(entries, self.s.public_url.rstrip("/"))
        if self.notifier.send(title, body):
            self.db.mark_notified(e["match_id"] for e in entries)
            res.notified = len(entries)
        else:
            res.errors.append("notification delivery failed; will retry next run")

    def write_back_pending(self, res: RunResult) -> None:
        if not self.s.writeback:
            return
        for row in self.db.pending_writebacks(int(self.s.auto_writeback_tier)):
            try:
                self.write_back_match(row["id"])
                res.written_back += 1
            except Exception as exc:
                log.exception("Write-back failed for match %s", row["id"])
                res.errors.append(f"write-back match {row['id']}: {exc}")

    def write_back_match(self, match_id: int) -> None:
        row = self.db.get_match(match_id)
        recall = self.db.get_recall(row["recall_key"]) if row else None
        if not row or not recall or row["writeback_at"]:
            return
        self.writeback.apply(row["item_id"], recall, Tier(row["tier"]), self.ui_link(match_id))
        self.db.mark_written_back(match_id)

    # --- orchestration ---
    def run(self, dry_run: bool = False) -> RunResult | None:
        """Run one cycle. Returns None if a cycle is already in progress."""
        if not self._lock.acquire(blocking=False):
            log.info("Check already running; skipping")
            return None
        res = RunResult(started_at=now(), dry_run=dry_run)
        t0 = time.monotonic()
        try:
            self.sync_recalls(res)
            self.sync_items(res)
            self.match(res)
            if not dry_run:
                self.notify(res)
                self.write_back_pending(res)
        except Exception as exc:
            log.exception("Check failed")
            res.errors.append(str(exc))
        finally:
            res.duration_s = round(time.monotonic() - t0, 2)
            self.db.set_meta("last_run", json.dumps(asdict(res)))
            self._index = None
            _release_memory()
            self._lock.release()
        log.info(
            "Check done in %.1fs: %d recalls (%d changed), %d items (%d refreshed), "
            "%d new matches, %d notified, %d written back%s",
            res.duration_s, res.recalls_total, res.recalls_changed, res.items_total,
            res.items_refreshed, res.new_matches, res.notified, res.written_back,
            f", errors: {res.errors}" if res.errors else "",
        )
        return res
