from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .sources.base import Recall

SCHEMA = """
CREATE TABLE IF NOT EXISTS recalls (
    key               TEXT PRIMARY KEY,         -- "<source>:<source_id>"
    source            TEXT NOT NULL,
    last_publish_date TEXT,
    data              TEXT NOT NULL,
    synced_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
    id          TEXT PRIMARY KEY,
    updated_at  TEXT NOT NULL,
    active      INTEGER NOT NULL DEFAULT 1,     -- 0 once archived/sold/deleted in Homebox
    data        TEXT NOT NULL,
    synced_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS matches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id         TEXT NOT NULL,
    recall_key      TEXT NOT NULL,
    tier            INTEGER NOT NULL,
    score           REAL NOT NULL,
    evidence        TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'new', -- new | confirmed | dismissed
    notified_at     TEXT,
    writeback_at    TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE (item_id, recall_key)
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class DB:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        # The scheduler thread and web handlers share one connection.
        self.lock = threading.RLock()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self.lock, self.conn:
            yield self.conn

    # --- meta ---
    def get_meta(self, key: str) -> str | None:
        with self.lock:
            row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    # --- recalls ---
    def upsert_recalls(self, recalls: Iterable[Recall]) -> list[str]:
        """Insert or update recalls; return keys that are new or whose publish date changed."""
        changed: list[str] = []
        ts = now()
        with self.tx() as c:
            for r in recalls:
                lpd = r.last_publish_date.isoformat() if r.last_publish_date else None
                row = c.execute(
                    "SELECT last_publish_date FROM recalls WHERE key=?", (r.key,)
                ).fetchone()
                if row is None or row["last_publish_date"] != lpd:
                    changed.append(r.key)
                c.execute(
                    "INSERT INTO recalls(key, source, last_publish_date, data, synced_at) "
                    "VALUES(?, ?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
                    "last_publish_date=excluded.last_publish_date, data=excluded.data, "
                    "synced_at=excluded.synced_at",
                    (r.key, r.source, lpd, json.dumps(r.to_dict()), ts),
                )
        return changed

    def all_recalls(self) -> list[Recall]:
        with self.lock:
            rows = self.conn.execute("SELECT data FROM recalls").fetchall()
        return [Recall.from_dict(json.loads(r["data"])) for r in rows]

    def get_recall(self, key: str) -> Recall | None:
        with self.lock:
            row = self.conn.execute("SELECT data FROM recalls WHERE key=?", (key,)).fetchone()
        return Recall.from_dict(json.loads(row["data"])) if row else None

    def recall_count(self) -> int:
        with self.lock:
            return self.conn.execute("SELECT COUNT(*) FROM recalls").fetchone()[0]

    # --- items ---
    def item_versions(self) -> dict[str, str]:
        with self.lock:
            rows = self.conn.execute("SELECT id, updated_at FROM items WHERE active=1").fetchall()
        return {r["id"]: r["updated_at"] for r in rows}

    def upsert_item(self, item: dict) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO items(id, updated_at, active, data, synced_at) VALUES(?, ?, 1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at, active=1, "
                "data=excluded.data, synced_at=excluded.synced_at",
                (item["id"], item.get("updatedAt") or "", json.dumps(item), now()),
            )

    def deactivate_items(self, ids: Iterable[str]) -> None:
        with self.tx() as c:
            c.executemany("UPDATE items SET active=0 WHERE id=?", [(i,) for i in ids])

    def active_items(self) -> list[dict]:
        with self.lock:
            rows = self.conn.execute("SELECT data FROM items WHERE active=1").fetchall()
        return [json.loads(r["data"]) for r in rows]

    def get_item(self, item_id: str) -> dict | None:
        with self.lock:
            row = self.conn.execute("SELECT data FROM items WHERE id=?", (item_id,)).fetchone()
        return json.loads(row["data"]) if row else None

    # --- matches ---
    def insert_match(self, item_id: str, recall_key: str, tier: int, score: float,
                     evidence: list[dict]) -> int | None:
        """Insert a new match. Existing pairs only get their tier/evidence refreshed and
        never resurface: a dismissed match stays dismissed. Returns the id if newly created."""
        ts = now()
        with self.tx() as c:
            row = c.execute(
                "SELECT id FROM matches WHERE item_id=? AND recall_key=?", (item_id, recall_key)
            ).fetchone()
            if row:
                c.execute(
                    "UPDATE matches SET tier=?, score=?, evidence=?, updated_at=? WHERE id=?",
                    (tier, score, json.dumps(evidence), ts, row["id"]),
                )
                return None
            cur = c.execute(
                "INSERT INTO matches(item_id, recall_key, tier, score, evidence, created_at, "
                "updated_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
                (item_id, recall_key, tier, score, json.dumps(evidence), ts, ts),
            )
            return cur.lastrowid

    def get_match(self, match_id: int) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()

    def list_matches(self, status: str | None = None, min_tier: int = 1) -> list[sqlite3.Row]:
        sql = "SELECT * FROM matches WHERE tier>=?"
        args: list = [min_tier]
        if status:
            sql += " AND status=?"
            args.append(status)
        sql += " ORDER BY tier DESC, created_at DESC"
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def set_match_status(self, match_id: int, status: str) -> None:
        with self.tx() as c:
            c.execute("UPDATE matches SET status=?, updated_at=? WHERE id=?",
                      (status, now(), match_id))

    def mark_notified(self, match_ids: Iterable[int]) -> None:
        ts = now()
        with self.tx() as c:
            c.executemany("UPDATE matches SET notified_at=? WHERE id=?",
                          [(ts, i) for i in match_ids])

    def mark_written_back(self, match_id: int) -> None:
        with self.tx() as c:
            c.execute("UPDATE matches SET writeback_at=? WHERE id=?", (now(), match_id))

    def pending_notifications(self, min_tier: int) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(
                "SELECT * FROM matches WHERE notified_at IS NULL AND status!='dismissed' "
                "AND tier>=? AND item_id IN (SELECT id FROM items WHERE active=1) "
                "ORDER BY tier DESC, created_at",
                (min_tier,),
            ).fetchall()

    def pending_writebacks(self, min_tier: int) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(
                "SELECT * FROM matches WHERE writeback_at IS NULL AND status!='dismissed' "
                "AND (tier>=? OR status='confirmed') "
                "AND item_id IN (SELECT id FROM items WHERE active=1)",
                (min_tier,),
            ).fetchall()

    def match_counts(self) -> dict[str, int]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT status, COUNT(*) n FROM matches GROUP BY status"
            ).fetchall()
        return {r["status"]: r["n"] for r in rows}
