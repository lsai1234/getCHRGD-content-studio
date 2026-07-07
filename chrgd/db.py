"""SQLite backlog + run-history store.

A single writable file replaces the old read-only Project knowledge sheet.
One `ideas` table holds both seed and build fields; a `runs` table records
what each pipeline run built, spent, and exported (used from milestone 2+).

Everything here is idempotent: re-running a capture or build must not
duplicate rows or re-bill.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from .models import DecaySpeed, Idea, Status

# Columns in declared order. Kept in one place so row<->Idea mapping stays
# honest if the schema grows.
_COLUMNS = [
    "idea_id",
    "status",
    "priority",
    "content_category",
    "target_viewer",
    "pain_point",
    "core_tension",
    "concept_note",
    "learning_tag",
    "decay_speed",
    "created_at",
    "post_type",
    "hook",
    "slides_json",
    "caption",
    "comment_trigger",
    "hashtags",
    "route_json",
    "asset_paths_json",
    "scheduled_for",
    "processed_at",
    "exported_at",
    "platform_urls_json",
]

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS ideas (
    idea_id           TEXT PRIMARY KEY,
    status            TEXT NOT NULL DEFAULT 'queued',
    priority          INTEGER NOT NULL DEFAULT 3,
    content_category  TEXT NOT NULL DEFAULT '',
    target_viewer     TEXT NOT NULL DEFAULT '',
    pain_point        TEXT NOT NULL DEFAULT '',
    core_tension      TEXT NOT NULL DEFAULT '',
    concept_note      TEXT NOT NULL DEFAULT '',
    learning_tag      TEXT NOT NULL DEFAULT '',
    decay_speed       TEXT,
    created_at        TEXT NOT NULL,
    post_type         TEXT,
    hook              TEXT,
    slides_json       TEXT,
    caption           TEXT,
    comment_trigger   TEXT,
    hashtags          TEXT,
    route_json        TEXT,
    asset_paths_json  TEXT,
    scheduled_for     TEXT,
    processed_at      TEXT,
    exported_at       TEXT,
    platform_urls_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_ideas_status ON ideas(status);

CREATE TABLE IF NOT EXISTS runs (
    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    command     TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    built       INTEGER NOT NULL DEFAULT 0,
    exported    INTEGER NOT NULL DEFAULT 0,
    spend_usd   REAL NOT NULL DEFAULT 0,
    notes       TEXT
);

-- Async job state (background build/render + long video jobs).
-- Persisted so a crash/restart resumes instead of re-billing.
CREATE TABLE IF NOT EXISTS jobs (
    job_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    idea_id     TEXT,                        -- null for whole-batch jobs (build)
    kind        TEXT NOT NULL,               -- 'build' | 'render' | 'video'
    provider    TEXT,                        -- e.g. 'higgsfield'
    external_id TEXT,                         -- provider generation_id
    status      TEXT NOT NULL DEFAULT 'QUEUED',
    params_json TEXT,                         -- job inputs (count, dry_run, ...)
    result_json TEXT,                         -- job output summary
    progress    INTEGER NOT NULL DEFAULT 0,   -- 0-100
    media_url   TEXT,
    output_path TEXT,
    error       TEXT,
    attempts    INTEGER NOT NULL DEFAULT 0,
    cost_usd    REAL NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_idea ON jobs(idea_id);
"""


def _to_db(value):
    """Serialise a model value into a SQLite-storable scalar."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (Status, DecaySpeed)):
        return value.value
    if hasattr(value, "value"):  # other str enums (PostType)
        return value.value
    return value


class Store:
    """Thin, explicit wrapper over a SQLite connection."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False so the web worker thread can share the store;
        # WAL lets readers and a writer coexist without blocking.
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self.conn.executescript(_SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Add columns introduced after a DB was first created (idempotent)."""
        cols = {
            r["name"]
            for r in self.conn.execute("PRAGMA table_info(jobs)").fetchall()
        }
        additions = {
            "params_json": "TEXT",
            "result_json": "TEXT",
            "progress": "INTEGER NOT NULL DEFAULT 0",
        }
        for name, decl in additions.items():
            if name not in cols:
                self.conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {decl}")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- id minting ---------------------------------------------------------

    def next_idea_id(self, prefix: str) -> str:
        """Return the next free id for `prefix`, e.g. ``G-0007``.

        Scans existing ids sharing the prefix and continues the sequence,
        so ids stay unique even across separate capture runs.
        """
        like = f"{prefix}-%"
        rows = self.conn.execute(
            "SELECT idea_id FROM ideas WHERE idea_id LIKE ?", (like,)
        ).fetchall()
        max_n = 0
        for row in rows:
            suffix = row["idea_id"].rsplit("-", 1)[-1]
            if suffix.isdigit():
                max_n = max(max_n, int(suffix))
        return f"{prefix}-{max_n + 1:04d}"

    # --- writes -------------------------------------------------------------

    def add_idea(self, idea: Idea) -> Idea:
        """Insert an idea. Idempotent on `idea_id` (re-insert is a no-op)."""
        data = idea.model_dump()
        values = [_to_db(data[col]) for col in _COLUMNS]
        placeholders = ", ".join("?" for _ in _COLUMNS)
        cols = ", ".join(_COLUMNS)
        self.conn.execute(
            f"INSERT OR IGNORE INTO ideas ({cols}) VALUES ({placeholders})", values
        )
        self.conn.commit()
        return self.get_idea(idea.idea_id)

    def find_by_concept_note(self, concept_note: str) -> Idea | None:
        """Find a live (non-void) idea with the same concept note.

        Used by `capture` to avoid seeding the same thought twice. Match is
        case-insensitive on trimmed text.
        """
        norm = concept_note.strip().lower()
        if not norm:
            return None
        row = self.conn.execute(
            "SELECT * FROM ideas "
            "WHERE lower(trim(concept_note)) = ? AND status != 'void' LIMIT 1",
            (norm,),
        ).fetchone()
        return self._row_to_idea(row) if row else None

    def mark_processing(self, idea_id: str) -> None:
        self.conn.execute(
            "UPDATE ideas SET status = 'processing' WHERE idea_id = ?", (idea_id,)
        )
        self.conn.commit()

    def save_build(self, idea_id: str, fields: dict) -> Idea:
        """Persist build fields for an idea and mark it done. Idempotent."""
        allowed = {
            "post_type",
            "hook",
            "slides_json",
            "caption",
            "comment_trigger",
            "hashtags",
            "route_json",
            "asset_paths_json",
            "scheduled_for",
            "processed_at",
            "platform_urls_json",
        }
        sets = {k: _to_db(v) for k, v in fields.items() if k in allowed}
        sets["status"] = Status.done.value
        assignments = ", ".join(f"{k} = ?" for k in sets)
        self.conn.execute(
            f"UPDATE ideas SET {assignments} WHERE idea_id = ?",
            [*sets.values(), idea_id],
        )
        self.conn.commit()
        return self.get_idea(idea_id)

    def save_asset_paths(self, idea_id: str, paths: list[str]) -> None:
        """Record rendered asset paths without changing status. Idempotent."""
        self.conn.execute(
            "UPDATE ideas SET asset_paths_json = ? WHERE idea_id = ?",
            (json.dumps(paths), idea_id),
        )
        self.conn.commit()

    def mark_exported(self, idea_id: str, when: datetime | None = None) -> None:
        """Stamp `exported_at` so a row never exports twice."""
        ts = _to_db(when) if when else datetime.now().astimezone().isoformat()
        self.conn.execute(
            "UPDATE ideas SET exported_at = ? WHERE idea_id = ? AND exported_at IS NULL",
            (ts, idea_id),
        )
        self.conn.commit()

    def mark_review(self, idea_id: str, fields: dict) -> Idea:
        """Persist a build that failed QA and flag it for `chrgd review`.

        Same as `save_build` but leaves status as `review` instead of `done`,
        so the post is inspectable but never exported.
        """
        idea = self.save_build(idea_id, fields)
        self.set_status(idea_id, Status.review)
        return self.get_idea(idea_id)

    def set_schedule(self, idea_id: str, when: datetime | None) -> None:
        """Set or clear the user-chosen posting datetime. Idempotent."""
        self.conn.execute(
            "UPDATE ideas SET scheduled_for = ? WHERE idea_id = ?",
            (when.isoformat() if when else None, idea_id),
        )
        self.conn.commit()

    def ideas_scheduled_between(self, start: datetime, end: datetime) -> list[Idea]:
        """Non-void ideas with scheduled_for in [start, end), soonest first."""
        rows = self.conn.execute(
            "SELECT * FROM ideas WHERE scheduled_for IS NOT NULL "
            "AND scheduled_for >= ? AND scheduled_for < ? AND status != 'void' "
            "ORDER BY scheduled_for ASC",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        return [self._row_to_idea(r) for r in rows]

    def set_status(self, idea_id: str, status: Status) -> None:
        self.conn.execute(
            "UPDATE ideas SET status = ? WHERE idea_id = ?",
            (status.value, idea_id),
        )
        self.conn.commit()

    def delete_idea(self, idea_id: str) -> None:
        self.conn.execute("DELETE FROM ideas WHERE idea_id = ?", (idea_id,))
        self.conn.commit()

    # --- reads --------------------------------------------------------------

    def get_idea(self, idea_id: str) -> Idea | None:
        row = self.conn.execute(
            "SELECT * FROM ideas WHERE idea_id = ?", (idea_id,)
        ).fetchone()
        return self._row_to_idea(row) if row else None

    def next_unprocessed(self, n: int) -> list[Idea]:
        """Next `n` queued ideas, highest priority first (1 = highest)."""
        rows = self.conn.execute(
            "SELECT * FROM ideas WHERE status = 'queued' "
            "ORDER BY priority ASC, created_at ASC LIMIT ?",
            (n,),
        ).fetchall()
        return [self._row_to_idea(r) for r in rows]

    def list_ideas(self, status: Status | None = None) -> list[Idea]:
        if status is None:
            rows = self.conn.execute(
                "SELECT * FROM ideas ORDER BY created_at ASC"
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM ideas WHERE status = ? ORDER BY created_at ASC",
                (status.value,),
            ).fetchall()
        return [self._row_to_idea(r) for r in rows]

    def count(self, status: Status | None = None) -> int:
        if status is None:
            row = self.conn.execute("SELECT COUNT(*) AS c FROM ideas").fetchone()
        else:
            row = self.conn.execute(
                "SELECT COUNT(*) AS c FROM ideas WHERE status = ?", (status.value,)
            ).fetchone()
        return int(row["c"])

    # --- run history --------------------------------------------------------

    def start_run(self, command: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO runs (command, started_at) VALUES (?, ?)",
            (command, datetime.now().astimezone().isoformat()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list_runs(self, limit: int = 50) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM runs ORDER BY run_id DESC LIMIT ?", (limit,)
        ).fetchall()

    def finish_run(
        self,
        run_id: int,
        *,
        built: int = 0,
        exported: int = 0,
        spend_usd: float = 0.0,
        notes: str = "",
    ) -> None:
        self.conn.execute(
            "UPDATE runs SET finished_at = ?, built = ?, exported = ?, "
            "spend_usd = ?, notes = ? WHERE run_id = ?",
            (
                datetime.now().astimezone().isoformat(),
                built,
                exported,
                spend_usd,
                notes,
                run_id,
            ),
        )
        self.conn.commit()

    # --- async jobs ---------------------------------------------------------

    def create_job(
        self,
        kind: str,
        *,
        idea_id: str | None = None,
        provider: str | None = None,
        params: dict | None = None,
    ) -> int:
        now = datetime.now().astimezone().isoformat()
        cur = self.conn.execute(
            "INSERT INTO jobs (idea_id, kind, provider, status, params_json, "
            "created_at, updated_at) VALUES (?, ?, ?, 'QUEUED', ?, ?, ?)",
            (idea_id, kind, provider, json.dumps(params or {}), now, now),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def claim_next_job(self) -> dict | None:
        """Atomically take the oldest QUEUED job → PROCESSING. Single worker."""
        row = self.conn.execute(
            "SELECT * FROM jobs WHERE status = 'QUEUED' ORDER BY job_id ASC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        self.conn.execute(
            "UPDATE jobs SET status = 'PROCESSING', attempts = attempts + 1, "
            "updated_at = ? WHERE job_id = ?",
            (datetime.now().astimezone().isoformat(), row["job_id"]),
        )
        self.conn.commit()
        return self.get_job(row["job_id"])

    def recover_interrupted_jobs(self) -> int:
        """On startup, fail any PROCESSING build/render jobs (avoid re-billing).

        Video jobs are pollable via external_id and resume separately, so they
        are left for the video poller; here we only reap non-pollable work.
        """
        cur = self.conn.execute(
            "UPDATE jobs SET status = 'ERROR', error = 'interrupted — re-run', "
            "updated_at = ? WHERE status = 'PROCESSING' "
            "AND kind IN ('build','build_one','render','render_slide',"
            "'angles','revise','trends','moments','evergreen','concept','run')",
            (datetime.now().astimezone().isoformat(),),
        )
        self.conn.commit()
        return cur.rowcount

    def list_jobs(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM jobs ORDER BY job_id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_job(self, job_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        return dict(row) if row else None

    def active_job_for(self, idea_id: str, kind: str) -> dict | None:
        """An unfinished job for this idea/kind, so we never double-submit."""
        row = self.conn.execute(
            "SELECT * FROM jobs WHERE idea_id = ? AND kind = ? "
            "AND status IN ('QUEUED','PROCESSING') ORDER BY job_id DESC LIMIT 1",
            (idea_id, kind),
        ).fetchone()
        return dict(row) if row else None

    def update_job(self, job_id: int, **fields) -> None:
        allowed = {
            "external_id",
            "status",
            "params_json",
            "result_json",
            "progress",
            "media_url",
            "output_path",
            "error",
            "attempts",
            "cost_usd",
        }
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return
        sets["updated_at"] = datetime.now().astimezone().isoformat()
        assignments = ", ".join(f"{k} = ?" for k in sets)
        self.conn.execute(
            f"UPDATE jobs SET {assignments} WHERE job_id = ?",
            [*sets.values(), job_id],
        )
        self.conn.commit()

    # --- mapping ------------------------------------------------------------

    @staticmethod
    def _row_to_idea(row: sqlite3.Row) -> Idea:
        return Idea.model_validate(dict(row))
