
"""SQLite-backed checkpoints for Assignment 4, Part A."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from harness import ROOT
from harness.formats import CHECKPOINT_FIELDS, DURABILITY_MODES

DEFAULT = ROOT / "state" / "checkpoints.db"


def _has_conversation(value) -> bool:
    """Reject chat transcripts, even if nested inside a checkpoint field."""
    if isinstance(value, dict):
        keys = {str(key).lower() for key in value}
        if keys & {"conversation", "messages", "history", "transcript"}:
            return True
        if {"role", "content"} <= keys:
            return True
        return any(_has_conversation(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_conversation(item) for item in value)
    return False


class Checkpointer:
    def __init__(self, path: Path | None = None):
        # Use the assignment database by default, or a separate file in tests.
        self.path = Path(path) if path is not None else DEFAULT
        self.path.parent.mkdir(parents=True, exist_ok=True)

        with closing(self._connect()) as conn:
            with conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS checkpoints (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id TEXT NOT NULL,
                        step TEXT NOT NULL,
                        mode TEXT NOT NULL,
                        payload TEXT NOT NULL
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS checkpoints_by_run
                    ON checkpoints (run_id, id)
                """)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.execute("PRAGMA synchronous = FULL")
        return conn

    def save(self, run_id: str, checkpoint: dict, *, step, mode: str = "sync") -> None:
        """Save one checkpoint, committing to disk before returning."""
        if mode not in DURABILITY_MODES:
            raise ValueError(f"Unknown durability mode: {mode}")
        if mode != "sync":
            raise NotImplementedError("This implementation selects sync durability")

        if not isinstance(checkpoint, dict) or set(checkpoint) != set(CHECKPOINT_FIELDS):
            raise ValueError(f"A checkpoint must have exactly: {CHECKPOINT_FIELDS}")

        calls = checkpoint["completed_calls"]
        required_call_fields = {"step", "tool", "args", "key", "result"}
        if not isinstance(calls, list) or any(
            not isinstance(call, dict) or not required_call_fields.issubset(call)
            for call in calls
        ):
            raise ValueError("Each completed call needs step, tool, args, key and result")

        if _has_conversation(checkpoint):
            raise ValueError("A checkpoint must not contain model conversations")

        # JSON serialization snapshots the state and rejects unserializable objects.
        payload = json.dumps(checkpoint, allow_nan=False)

        # SQLite commits the insert before this function returns.
        with closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    "INSERT INTO checkpoints (run_id, step, mode, payload) VALUES (?, ?, ?, ?)",
                    (run_id, str(step), mode, payload),
                )

    def last(self, run_id: str) -> dict | None:
        """Load the newest saved state for a run, or None."""
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT payload FROM checkpoints WHERE run_id = ? ORDER BY id DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        return json.loads(row[0]) if row is not None else None

    def all(self, run_id: str) -> list[dict]:
        """Load every saved state for a run, oldest first."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT payload FROM checkpoints WHERE run_id = ? ORDER BY id ASC",
                (run_id,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def runs(self) -> list[str]:
        """Return each run ID that has at least one saved checkpoint."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT DISTINCT run_id FROM checkpoints ORDER BY run_id"
            ).fetchall()
        return [row[0] for row in rows]
