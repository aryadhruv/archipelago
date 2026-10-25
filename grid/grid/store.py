"""SQLite state for one grid: runs, attempts and events.

grid.db is the source of truth for progress, so a restarted grid resumes where
it stopped and the dashboard can read the same file.
"""

import json
import sqlite3
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# A run moves through these while a worker holds it.
ACTIVE_STATES = (
    "env_starting",
    "populating",
    "apps_starting",
    "agent_running",
    "snapshotting",
    "grading",
)
PENDING_STATES = ("queued", "retry")
TERMINAL_STATES = ("scored", "gave_up")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    position INTEGER NOT NULL,
    task_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    repeat INTEGER NOT NULL,
    state TEXT NOT NULL DEFAULT 'queued',
    resume_from TEXT NOT NULL DEFAULT 'agent',
    agent_attempts INTEGER NOT NULL DEFAULT 0,
    grading_attempts INTEGER NOT NULL DEFAULT 0,
    attempt_dir TEXT,
    port INTEGER,
    agent_status TEXT,
    classification TEXT,
    reason TEXT,
    score REAL,
    tokens INTEGER,
    steps INTEGER,
    duration_s REAL,
    not_before REAL NOT NULL DEFAULT 0,
    started_at REAL,
    finished_at REAL
);
CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    number INTEGER NOT NULL,
    outcome TEXT,
    reason TEXT,
    dir TEXT,
    started_at REAL NOT NULL,
    finished_at REAL
);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    run_id TEXT,
    kind TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_run ON events (run_id, seq);
"""


@dataclass
class Run:
    run_id: str
    position: int
    task_id: str
    model_id: str
    repeat: int
    state: str
    resume_from: str
    agent_attempts: int
    grading_attempts: int
    attempt_dir: str | None
    port: int | None
    agent_status: str | None
    classification: str | None
    reason: str | None
    score: float | None
    tokens: int | None
    steps: int | None
    duration_s: float | None
    not_before: float
    started_at: float | None
    finished_at: float | None


def run_id_for(task_id: str, model_id: str, repeat: int) -> str:
    return f"{task_id}__{model_id}__r{repeat}"


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._db = sqlite3.connect(path, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    # meta

    def set_meta(self, key: str, value: Any) -> None:
        self._db.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )

    def get_meta(self, key: str) -> Any:
        row = self._db.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        return json.loads(row["value"]) if row else None

    # runs

    def add_runs(self, rows: Iterable[tuple[str, str, int]]) -> int:
        """Insert (task_id, model_id, repeat) rows that aren't there yet."""
        added = 0
        for position, (task_id, model_id, repeat) in enumerate(rows):
            cursor = self._db.execute(
                "INSERT OR IGNORE INTO runs (run_id, position, task_id, model_id, repeat) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    run_id_for(task_id, model_id, repeat),
                    position,
                    task_id,
                    model_id,
                    repeat,
                ),
            )
            added += cursor.rowcount
        return added

    def reset_interrupted(self) -> list[str]:
        """Put runs a previous process left mid-flight back in the queue."""
        marks = ",".join("?" * len(ACTIVE_STATES))
        rows = self._db.execute(
            f"SELECT run_id FROM runs WHERE state IN ({marks})", ACTIVE_STATES
        ).fetchall()
        self._db.execute(
            f"UPDATE runs SET state = 'retry', port = NULL, not_before = 0 "
            f"WHERE state IN ({marks})",
            ACTIVE_STATES,
        )
        return [r["run_id"] for r in rows]

    def runs(self, states: Iterable[str] | None = None) -> list[Run]:
        if states is None:
            rows = self._db.execute("SELECT * FROM runs ORDER BY position").fetchall()
        else:
            states = tuple(states)
            marks = ",".join("?" * len(states))
            rows = self._db.execute(
                f"SELECT * FROM runs WHERE state IN ({marks}) ORDER BY position", states
            ).fetchall()
        return [Run(**dict(r)) for r in rows]

    def run(self, run_id: str) -> Run:
        row = self._db.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return Run(**dict(row))

    def update_run(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return
        assignments = ", ".join(f"{k} = ?" for k in fields)
        self._db.execute(
            f"UPDATE runs SET {assignments} WHERE run_id = ?",
            (*fields.values(), run_id),
        )

    def counts(self) -> dict[str, int]:
        rows = self._db.execute("SELECT state, COUNT(*) AS n FROM runs GROUP BY state")
        return {r["state"]: r["n"] for r in rows}

    # attempts

    def start_attempt(
        self, run_id: str, kind: str, number: int, directory: Path
    ) -> int:
        cursor = self._db.execute(
            "INSERT INTO attempts (run_id, kind, number, dir, started_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (run_id, kind, number, str(directory), time.time()),
        )
        return int(cursor.lastrowid or 0)

    def finish_attempt(self, attempt_id: int, outcome: str, reason: str) -> None:
        self._db.execute(
            "UPDATE attempts SET outcome = ?, reason = ?, finished_at = ? WHERE id = ?",
            (outcome, reason, time.time(), attempt_id),
        )

    def attempts(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT * FROM attempts WHERE run_id = ? ORDER BY id", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # events

    def add_event(self, kind: str, run_id: str | None = None, **data: Any) -> int:
        cursor = self._db.execute(
            "INSERT INTO events (ts, run_id, kind, data) VALUES (?, ?, ?, ?)",
            (time.time(), run_id, kind, json.dumps(data, default=str)),
        )
        return int(cursor.lastrowid or 0)

    def events(
        self, run_id: str | None = None, after: int = 0, limit: int = 1000
    ) -> list[dict[str, Any]]:
        if run_id is None:
            rows = self._db.execute(
                "SELECT * FROM events WHERE seq > ? ORDER BY seq LIMIT ?",
                (after, limit),
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT * FROM events WHERE run_id = ? AND seq > ? ORDER BY seq LIMIT ?",
                (run_id, after, limit),
            ).fetchall()
        return [{**dict(r), "data": json.loads(r["data"])} for r in rows]
