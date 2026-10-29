"""Persistent, provenance-aware memory store for Assignment 4 Part D."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3

from harness import ROOT

DEFAULT = ROOT / "state" / "store.db"
REPOSITORY = "ledgerline"
KINDS = ("episodic", "semantic", "procedural")
ORIGINS = ("derived", "ingested")
MAX_ACTIVE = 250


class Store:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path is not None else DEFAULT
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    repository TEXT NOT NULL,
                    issue INTEGER,
                    kind TEXT NOT NULL,
                    text TEXT NOT NULL,
                    source TEXT NOT NULL,
                    written_at TEXT NOT NULL,
                    origin TEXT NOT NULL,
                    confirmed INTEGER NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    supersedes INTEGER REFERENCES memories(id),
                    stated_by TEXT
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_memories_scope
                ON memories(repository, active, issue)
            """)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _normalize(record: dict) -> dict:
        if not isinstance(record, dict):
            raise TypeError("memory record must be a dictionary")

        required = ("repository", "kind", "text", "source", "origin", "confirmed")
        for field in required:
            if field not in record:
                raise ValueError(f"missing memory field: {field}")

        repository = record["repository"]
        if not isinstance(repository, str) or not repository.strip():
            raise ValueError("repository must be a nonempty string")
        if record["kind"] not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}")
        if not isinstance(record["text"], str) or not record["text"].strip():
            raise ValueError("text must be a nonempty string")

        source = record["source"]
        if not isinstance(source, str) or not (
            source in ("user", "repo", "memory")
            or re.fullmatch(r"issue:[0-9]+", source)
        ):
            raise ValueError("source must be user, repo, memory, or issue:<n>")
        if record["origin"] not in ORIGINS:
            raise ValueError(f"origin must be one of {ORIGINS}")
        if type(record["confirmed"]) is not bool:
            raise ValueError("confirmed must be a boolean")

        issue = record.get("issue")
        if issue is not None and (type(issue) is not int or issue <= 0):
            raise ValueError("issue must be a positive integer or None")
        active = record.get("active", True)
        if type(active) is not bool:
            raise ValueError("active must be a boolean")

        written_at = record.get("written_at")
        if written_at is None:
            written_at = datetime.now(timezone.utc).isoformat(
                timespec="seconds"
            ).replace("+00:00", "Z")
        if not isinstance(written_at, str):
            raise ValueError("written_at must be an ISO 8601 timestamp")
        try:
            parsed = datetime.fromisoformat(written_at.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("timestamp must have a timezone")
        except ValueError as exc:
            raise ValueError("written_at must be a timezone-aware ISO 8601 timestamp") from exc

        stated_by = record.get("stated_by")
        if stated_by is not None and (
            not isinstance(stated_by, str) or not stated_by.strip()
        ):
            raise ValueError("stated_by must be a nonempty string when provided")

        return {
            "repository": repository,
            "issue": issue,
            "kind": record["kind"],
            "text": record["text"],
            "source": source,
            "written_at": written_at,
            "origin": record["origin"],
            "confirmed": record["confirmed"],
            "active": active,
            "supersedes": record.get("supersedes"),
            "stated_by": stated_by,
        }

    @staticmethod
    def _insert(db, record: dict) -> int:
        cursor = db.execute("""
            INSERT INTO memories
              (repository, issue, kind, text, source, written_at,
               origin, confirmed, active, supersedes, stated_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            record["repository"], record["issue"], record["kind"],
            record["text"], record["source"], record["written_at"],
            record["origin"], int(record["confirmed"]),
            int(record["active"]), record["supersedes"], record["stated_by"],
        ))
        return cursor.lastrowid

    @staticmethod
    def _record(row) -> dict:
        result = dict(row)
        result["confirmed"] = bool(result["confirmed"])
        result["active"] = bool(result["active"])
        return result

    @staticmethod
    def _enforce_capacity(db, repository: str) -> None:
        count = db.execute(
            "SELECT COUNT(*) FROM memories WHERE repository = ? AND active = 1",
            (repository,),
        ).fetchone()[0]
        while count > MAX_ACTIVE:
            # Evict low-confidence episodic memories first; protect maintainer rules.
            victim = db.execute("""
                SELECT id FROM memories
                WHERE repository = ? AND active = 1
                  AND (confirmed = 0 OR (source = 'memory' AND kind = 'episodic'))
                ORDER BY
                  CASE WHEN confirmed = 0 AND kind = 'episodic' THEN 0
                       WHEN confirmed = 0 THEN 1 ELSE 2 END,
                  written_at ASC, id ASC
                LIMIT 1
            """, (repository,)).fetchone()
            if victim is None:
                raise ValueError(
                    "memory store is at capacity with protected records; "
                    "consolidate or supersede records before writing"
                )
            db.execute("UPDATE memories SET active = 0 WHERE id = ?", (victim["id"],))
            count -= 1

    def write(self, record: dict):
        data = self._normalize(record)
        if data["supersedes"] is not None:
            raise ValueError("use supersede(old_id, record) to replace a memory")
        with self._db() as db:
            new_id = self._insert(db, data)
            self._enforce_capacity(db, data["repository"])
            return new_id

    def supersede(self, old_id, record: dict):
        with self._db() as db:
            old = db.execute(
                "SELECT * FROM memories WHERE id = ?", (old_id,)
            ).fetchone()
            if old is None:
                raise KeyError(f"memory {old_id} does not exist")
            if not old["active"]:
                raise ValueError(f"memory {old_id} is already inactive")

            replacement = dict(record)
            replacement.setdefault("repository", old["repository"])
            replacement.setdefault("issue", old["issue"])
            replacement["active"] = True
            replacement["supersedes"] = old["id"]
            data = self._normalize(replacement)
            if (data["repository"], data["issue"]) != (
                old["repository"], old["issue"]
            ):
                raise ValueError("replacement must retain the repository and issue scope")

            db.execute("UPDATE memories SET active = 0 WHERE id = ?", (old_id,))
            new_id = self._insert(db, data)
            self._enforce_capacity(db, data["repository"])
            return new_id

    def active(self, repository: str, issue: int | None = None) -> list[dict]:
        with self._db() as db:
            if issue is None:
                rows = db.execute("""
                    SELECT * FROM memories
                    WHERE repository = ? AND active = 1
                    ORDER BY id
                """, (repository,)).fetchall()
            else:
                rows = db.execute("""
                    SELECT * FROM memories
                    WHERE repository = ? AND active = 1
                      AND (issue IS NULL OR issue = ?)
                    ORDER BY id
                """, (repository, issue)).fetchall()
        return [self._record(row) for row in rows]

    def records(self) -> list[dict]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM memories ORDER BY id").fetchall()
        return [self._record(row) for row in rows]
