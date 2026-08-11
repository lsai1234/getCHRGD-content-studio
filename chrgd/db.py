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
import threading
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
    "metrics_json",
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
    platform_urls_json TEXT,
    metrics_json      TEXT
);

CREATE INDEX IF NOT EXISTS idx_ideas_status ON ideas(status);
-- The calendar reads a date window, the retention sweep and the backlog read by
-- age, and the export screen reads what has shipped. Without these every one of
-- those is a full scan of a table whose rows carry the slide + story JSON.
CREATE INDEX IF NOT EXISTS idx_ideas_scheduled ON ideas(scheduled_for);
CREATE INDEX IF NOT EXISTS idx_ideas_created ON ideas(created_at);
CREATE INDEX IF NOT EXISTS idx_ideas_exported ON ideas(exported_at);

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

-- Editable app settings (brand profile, visual style, ...): a tiny key→JSON
-- store so the settings page can change behaviour without editing files.
CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
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


# Schema creation is idempotent but not free: ~10 CREATE statements plus two
# PRAGMA table_info reads. The web app opens a Store per request (several per
# page), so paying that on every open showed up as real latency. Track which
# files this process has already prepared and skip it the second time.
_PREPARED: set[Path] = set()
_PREPARE_LOCK = threading.Lock()


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
        self._shared = False
        resolved = self.db_path.resolve()
        with _PREPARE_LOCK:
            seen = resolved in _PREPARED
            _PREPARED.add(resolved)
        # `seen` is a hint, not a promise — the file can be deleted and recreated
        # under us (tests do exactly that). Confirm with one sqlite_master lookup
        # before trusting it, which is still orders of magnitude cheaper than
        # re-running the whole schema script.
        if not seen or not self._has_schema():
            self.conn.executescript(_SCHEMA)
            self._migrate()
            self.conn.commit()

    def _has_schema(self) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ideas' LIMIT 1"
        ).fetchone()
        return row is not None

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

        # Learning loop: real TikTok results logged per post.
        idea_cols = {
            r["name"]
            for r in self.conn.execute("PRAGMA table_info(ideas)").fetchall()
        }
        if "metrics_json" not in idea_cols:
            self.conn.execute("ALTER TABLE ideas ADD COLUMN metrics_json TEXT")

    def close(self) -> None:
        # A shared store outlives the `with` block that borrowed it — closing it
        # there would pull the connection out from under the next request on
        # this thread. `shared_store` owns the lifetime instead.
        if self._shared:
            return
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- editable app settings (key → JSON) --------------------------------

    def get_setting(self, key: str, default=None):
        """Return the stored JSON value for `key`, or `default` if unset."""
        row = self.conn.execute(
            "SELECT value FROM app_settings WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            return default

    def set_setting(self, key: str, value) -> None:
        """Upsert a JSON-serialisable value under `key`."""
        now = datetime.now().astimezone().isoformat()
        self.conn.execute(
            "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = excluded.updated_at",
            (key, json.dumps(value), now),
        )
        self.conn.commit()

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

    def set_metrics(self, idea_id: str, metrics: dict) -> None:
        """Log real post results (views/likes/…). Overwrites. Idempotent."""
        self.conn.execute(
            "UPDATE ideas SET metrics_json = ? WHERE idea_id = ?",
            (json.dumps(metrics), idea_id),
        )
        self.conn.commit()

    def merge_metrics(self, idea_id: str, patch: dict) -> None:
        """Merge keys into metrics_json without clobbering the rest.

        Used by one-tap rating so logging a 🔥/😐/💀 keeps any views already
        entered, and vice versa.
        """
        row = self.conn.execute(
            "SELECT metrics_json FROM ideas WHERE idea_id = ?", (idea_id,)
        ).fetchone()
        current = {}
        if row and row["metrics_json"]:
            try:
                current = json.loads(row["metrics_json"])
            except json.JSONDecodeError:
                current = {}
        current.update(patch)
        self.conn.execute(
            "UPDATE ideas SET metrics_json = ? WHERE idea_id = ?",
            (json.dumps(current), idea_id),
        )
        self.conn.commit()

    #: What the learning loop actually reads off a rated post. Deliberately not
    #: `SELECT *`: the maths needs the metrics, the traits and the slide count,
    #: and never looks at the caption, the hashtags, the asset paths or the
    #: platform URLs — which are bytes carried into memory and thrown away on
    #: every build, every fan-out and every dashboard load.
    CORPUS_COLUMNS = (
        "idea_id", "content_category", "concept_note", "hook",
        "created_at", "slides_json", "route_json", "metrics_json",
    )

    #: How far back the learning loop looks. A rated post is kept forever (that
    #: is the point of `retention_keep_rated`), so an unbounded corpus is the
    #: same "slower with every post" shape the list screens had: the notes
    #: injected into EVERY build and every fan-out were derived from a table
    #: scan that only ever grew. The newest few hundred rated posts is both a
    #: bound and the more honest sample — an account's results from two years
    #: ago are not evidence about what works on it now.
    CORPUS_LIMIT = 500

    def ideas_with_metrics(self, limit: int | None = CORPUS_LIMIT) -> list[Idea]:
        """Posts with logged results, newest first — the learning corpus.

        Light rows (see `CORPUS_COLUMNS`) and bounded to `limit`. Pass
        `limit=None` for the whole history.
        """
        sql = (
            f"SELECT {', '.join(self.CORPUS_COLUMNS)} FROM ideas "
            "WHERE metrics_json IS NOT NULL ORDER BY created_at DESC, idea_id DESC"
        )
        args: list = []
        if limit is not None:
            sql += " LIMIT ?"
            args.append(int(limit))
        return [self._row_to_idea(r) for r in self.conn.execute(sql, args).fetchall()]

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

    #: What a list screen actually shows. The heavy columns an `Idea` carries —
    #: slides_json, route_json (which holds the whole prose episode), caption —
    #: are kilobytes each and are never read by a table row or a calendar card,
    #: so the list queries below select these and derive the rest as booleans.
    SUMMARY_COLUMNS = (
        "idea_id", "status", "priority", "content_category", "concept_note",
        "hook", "decay_speed", "created_at", "scheduled_for", "processed_at",
        "exported_at", "metrics_json",
        "slides_json IS NOT NULL AND slides_json != '' AS built",
        "asset_paths_json AS assets",
    )

    def _summaries(self, where: str = "", params: tuple = (), order: str = "created_at ASC",
                   limit: int | None = None, offset: int = 0) -> list[dict]:
        sql = f"SELECT {', '.join(self.SUMMARY_COLUMNS)} FROM ideas"
        args: list = list(params)
        if where:
            sql += f" WHERE {where}"
        sql += f" ORDER BY {order}"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            args += [limit, offset]
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def list_summaries(
        self,
        status: Status | None = None,
        *,
        limit: int | None = None,
        offset: int = 0,
        newest_first: bool = False,
    ) -> list[dict]:
        """Light rows for a list screen — no slide, route or caption payloads."""
        where, params = ("status = ?", (status.value,)) if status else ("", ())
        order = "created_at DESC, idea_id DESC" if newest_first else "created_at ASC"
        return self._summaries(where, params, order=order, limit=limit, offset=offset)

    def unscheduled_summaries(self, limit: int = 200) -> list[dict]:
        """Built-but-undated posts — the calendar's tray, newest first.

        Filtered in SQL rather than by walking every idea in Python: the tray is
        a handful of cards, but the old scan read (and JSON-parsed) the entire
        backlog to find them.
        """
        return self._summaries(
            "scheduled_for IS NULL AND exported_at IS NULL "
            "AND status IN ('done','review') "
            "AND slides_json IS NOT NULL AND slides_json != ''",
            (),
            order="created_at DESC",
            limit=limit,
        )

    def summaries_scheduled_between(self, start: datetime, end: datetime) -> list[dict]:
        """Light rows for the calendar's date window, soonest first."""
        return self._summaries(
            "scheduled_for IS NOT NULL AND scheduled_for >= ? AND scheduled_for < ? "
            "AND status != 'void'",
            (start.isoformat(), end.isoformat()),
            order="scheduled_for ASC",
        )

    def recent_routes(
        self, needle: str, *, scan: int = 400, limit: int = 60
    ) -> list[dict]:
        """Recent ideas whose `route_json` mentions `needle`, newest first.

        The create screen's "you've used this recently" nudges all ask this
        question. A bare `route_json LIKE '%needle%'` cannot use an index, so
        when the answer is "none" — the normal case for a show the studio isn't
        running — SQLite reads the route JSON (slides, design system, the whole
        prose episode) of every idea ever captured before it can say so. The
        inner query bounds that to the newest `scan` ideas, which is all a
        recency nudge could honestly look at anyway.
        """
        rows = self.conn.execute(
            "SELECT idea_id, route_json, created_at FROM ("
            "  SELECT idea_id, route_json, created_at FROM ideas"
            "  WHERE route_json IS NOT NULL ORDER BY idea_id DESC LIMIT ?"
            ") WHERE route_json LIKE ? LIMIT ?",
            (max(1, scan), f"%{needle}%", max(1, limit)),
        ).fetchall()
        return [dict(r) for r in rows]

    def count_where(self, where: str = "", params: tuple = ()) -> int:
        sql = "SELECT COUNT(*) AS c FROM ideas"
        if where:
            sql += f" WHERE {where}"
        return int(self.conn.execute(sql, params).fetchone()["c"])

    def list_ideas(
        self,
        status: Status | None = None,
        *,
        limit: int | None = None,
        offset: int = 0,
        newest_first: bool = False,
    ) -> list[Idea]:
        """Full ideas. Prefer `list_summaries` unless you need the payloads —
        every row here carries the slides, the route and the prose episode."""
        sql = "SELECT * FROM ideas"
        args: list = []
        if status is not None:
            sql += " WHERE status = ?"
            args.append(status.value)
        # idea_id breaks ties, so paging can't drop or repeat a row when several
        # ideas share a created_at (a capture writes a whole dump at once).
        sql += (
            " ORDER BY created_at DESC, idea_id DESC" if newest_first
            else " ORDER BY created_at ASC"
        )
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            args += [limit, offset]
        return [self._row_to_idea(r) for r in self.conn.execute(sql, args).fetchall()]

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

    def spend_by_command(self, since: datetime | None = None) -> list[dict]:
        """Spend grouped by stage, dearest first — where the credits actually go.

        A single total answers "am I spending too much" but never "on what",
        which is the only question you can act on. The stages are the `command`
        values the engine records: build, render, concept, takes, angles,
        revise, meta_scan, run.
        """
        sql = (
            "SELECT command, COUNT(*) AS runs, COALESCE(SUM(spend_usd), 0) AS spend "
            "FROM runs"
        )
        args: list = []
        if since is not None:
            sql += " WHERE started_at >= ?"
            args.append(since.isoformat())
        sql += " GROUP BY command HAVING spend > 0 ORDER BY spend DESC"
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

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

    def claim_next_job(
        self,
        kinds: "frozenset[str] | None" = None,
        exclude: "frozenset[str] | None" = None,
    ) -> dict | None:
        """Atomically take the oldest QUEUED job → PROCESSING.

        Optionally restrict to `kinds` (inclusion) or skip `exclude` kinds, so
        several workers can share the table without stepping on each other (a
        research worker taking scans, a fast worker taking everything else).

        Concurrency-safe: the claiming UPDATE is guarded on `status='QUEUED'`, so
        when two workers race the same row exactly one gets rowcount==1 and wins;
        the loser simply tries the next queued job. Backward compatible — called
        with no args it behaves exactly like the old single-worker claim.
        """
        where = "status = 'QUEUED'"
        params: list = []
        if kinds:
            where += " AND kind IN (%s)" % ",".join("?" * len(kinds))
            params += list(kinds)
        if exclude:
            where += " AND kind NOT IN (%s)" % ",".join("?" * len(exclude))
            params += list(exclude)

        while True:
            row = self.conn.execute(
                f"SELECT job_id FROM jobs WHERE {where} ORDER BY job_id ASC LIMIT 1",
                params,
            ).fetchone()
            if row is None:
                return None
            cur = self.conn.execute(
                "UPDATE jobs SET status = 'PROCESSING', attempts = attempts + 1, "
                "updated_at = ? WHERE job_id = ? AND status = 'QUEUED'",
                (datetime.now().astimezone().isoformat(), row["job_id"]),
            )
            self.conn.commit()
            if cur.rowcount == 1:
                return self.get_job(row["job_id"])
            # Lost the race — another worker claimed it; try the next queued job.

    def recover_interrupted_jobs(self) -> int:
        """On startup, fail any PROCESSING build/render jobs (avoid re-billing).

        Video jobs are pollable via external_id and resume separately, so they
        are left for the video poller; here we only reap non-pollable work.
        """
        cur = self.conn.execute(
            "UPDATE jobs SET status = 'ERROR', error = 'interrupted — re-run', "
            "updated_at = ? WHERE status = 'PROCESSING' "
            "AND kind IN ('build','build_one','render','render_slide',"
            "'angles','takes','revise','trends','moments','evergreen','trending','ragebait','meta_scan',"
            "'moment_detail','lane_angles','concept','run')",
            (datetime.now().astimezone().isoformat(),),
        )
        self.conn.commit()
        return cur.rowcount

    def requeue_interrupted_video_jobs(self) -> int:
        """On startup, put any PROCESSING video job back to QUEUED so it resumes.

        Video jobs are pollable and persist per-clip provider ids + downloaded
        paths, so re-running the handler re-polls/re-stitches from where it left
        off rather than re-submitting paid clips. (Non-pollable build/render jobs
        are reaped to ERROR by `recover_interrupted_jobs` instead.)
        """
        cur = self.conn.execute(
            "UPDATE jobs SET status = 'QUEUED', updated_at = ? "
            "WHERE status = 'PROCESSING' AND kind = 'video'",
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


# --- shared connections -------------------------------------------------------

_LOCAL = threading.local()


def shared_store(db_path: str | Path) -> Store:
    """A per-thread Store that is opened once and reused.

    SQLite connections are cheap but not free, and the web app was opening one
    per request — six on the create screen alone, each paying connect + PRAGMA.
    Connections are per-thread by convention here, so the cache is thread-local:
    the request threads, the three workers and the CLI each keep their own.

    The returned store ignores `close()`, so existing `with _store(...) as s:`
    call sites keep reading naturally without severing the shared connection.
    """
    cache: dict[Path, Store] = getattr(_LOCAL, "stores", None)
    if cache is None:
        cache = _LOCAL.stores = {}
    key = Path(db_path).resolve()
    store = cache.get(key)
    if store is None:
        store = Store(db_path)
        store._shared = True
        cache[key] = store
    return store


def close_shared_stores() -> None:
    """Drop this thread's cached connections (test teardown / shutdown)."""
    cache: dict[Path, Store] = getattr(_LOCAL, "stores", None) or {}
    for store in cache.values():
        store._shared = False
        store.close()
    cache.clear()
