"""Retention — automatically delete content that has aged out.

A studio that runs every day accumulates two kinds of weight, and both of them
are felt: **rendered images** fill the disk, and **idea rows** slow every screen
that reads the backlog (the calendar tray, the review wall, the create screen's
"recently used" scans all read the whole table). Neither is useful thirty days
after a post has been and gone.

The rules, deliberately conservative — this deletes real work, so anything the
studio might still be about to use is off limits:

* An idea's **age** is the newest of everything that ever happened to it
  (created / built / exported / scheduled), so a post scheduled for next week is
  not "old" just because the idea was captured two months ago.
* Nothing is deleted while it has a **queued or running job** against it, or a
  **future posting slot**.
* A **rated** post keeps its row (the learning loop reads real results back into
  every build — deleting them permanently dulls the engine) but still loses its
  images, which are the heavy part. `keep_rated=False` overrides that.
* `runs` history and finished `jobs` rows older than the cutoff go too, and so
  do `output/<idea_id>/` folders whose idea no longer exists.

Everything is idempotent and safe to run on a schedule: the web app sweeps once
a day, and `chrgd prune` does it on demand (`--dry-run` reports without
deleting).
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import Settings
from .db import Store
from .logging_setup import get_logger

log = get_logger("retention")

#: An idea's asset folder is named after its id, which `Store.next_idea_id`
#: mints as `<prefix>-<number>`. Orphan cleanup only ever considers folders that
#: match that shape, so the export folder (`ready/`) and the brand assets
#: (`_brand/`, holding the locked character portrait) can never be swept up by
#: it — including if someone adds another sidecar folder later.
IDEA_DIR_RE = re.compile(r"^[A-Za-z0-9]+-\d+$")


@dataclass
class PruneResult:
    """What a sweep did (or, on a dry run, what it would have done)."""

    cutoff: datetime
    ideas_deleted: list[str] = field(default_factory=list)
    ideas_stripped: list[str] = field(default_factory=list)  # row kept, assets removed
    asset_dirs_removed: int = 0
    bytes_freed: int = 0
    runs_deleted: int = 0
    jobs_deleted: int = 0
    orphan_dirs_removed: int = 0
    dry_run: bool = False

    @property
    def total_ideas(self) -> int:
        return len(self.ideas_deleted) + len(self.ideas_stripped)

    def summary(self) -> str:
        mb = self.bytes_freed / (1024 * 1024)
        verb = "would free" if self.dry_run else "freed"
        return (
            f"{len(self.ideas_deleted)} ideas deleted, "
            f"{len(self.ideas_stripped)} stripped to text, "
            f"{self.asset_dirs_removed + self.orphan_dirs_removed} asset folders, "
            f"{self.runs_deleted} runs, {self.jobs_deleted} jobs — {verb} {mb:.1f} MB"
        )


def _as_utc(value: str | None) -> datetime | None:
    """Parse a stored ISO timestamp into an aware UTC datetime."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _last_touched(row) -> datetime | None:
    """The newest thing that ever happened to this idea.

    Using the newest rather than `created_at` is what stops the sweep deleting
    an old idea that was only just built, or one that is scheduled to post.
    """
    stamps = [
        _as_utc(row[col])
        for col in ("created_at", "processed_at", "exported_at", "scheduled_for")
    ]
    live = [s for s in stamps if s is not None]
    return max(live) if live else None


def _dir_size(path: Path) -> int:
    try:
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    except OSError:
        return 0


def _remove_assets(settings: Settings, idea_id: str, result: PruneResult) -> None:
    """Delete `output/<idea_id>/`, counting what it freed."""
    # This is an rmtree driven by a value from the database, so the id has to
    # look like an id before it names a directory.
    if not IDEA_DIR_RE.match(idea_id):
        return
    target = Path(settings.output_dir) / idea_id
    if not target.is_dir():
        return
    result.bytes_freed += _dir_size(target)
    result.asset_dirs_removed += 1
    if not result.dry_run:
        shutil.rmtree(target, ignore_errors=True)


def _busy_idea_ids(store: Store) -> set[str]:
    """Ideas with a job still queued or running — never touched by a sweep."""
    rows = store.conn.execute(
        "SELECT DISTINCT idea_id FROM jobs "
        "WHERE idea_id IS NOT NULL AND status IN ('QUEUED','PROCESSING')"
    ).fetchall()
    return {r["idea_id"] for r in rows}


def prune(
    store: Store,
    settings: Settings,
    *,
    days: int | None = None,
    keep_rated: bool | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
) -> PruneResult:
    """Delete content older than `days` (default `CHRGD_RETENTION_DAYS`).

    `days <= 0` disables the sweep entirely and returns an empty result, so
    switching retention off in the environment switches off every caller at
    once rather than needing each one to check.
    """
    days = settings.retention_days if days is None else days
    keep_rated = settings.retention_keep_rated if keep_rated is None else keep_rated
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max(0, days))
    result = PruneResult(cutoff=cutoff, dry_run=dry_run)
    if days <= 0:
        return result

    busy = _busy_idea_ids(store)
    rows = store.conn.execute(
        "SELECT idea_id, created_at, processed_at, exported_at, scheduled_for, "
        "metrics_json FROM ideas"
    ).fetchall()

    for row in rows:
        idea_id = row["idea_id"]
        if idea_id in busy:
            continue
        touched = _last_touched(row)
        # A row with no readable timestamp at all is left alone: we cannot show
        # it is old, and guessing here would delete somebody's work.
        if touched is None or touched >= cutoff:
            continue

        _remove_assets(settings, idea_id, result)
        if keep_rated and row["metrics_json"]:
            # The learning corpus survives as text — the engine reads these
            # results back into every build.
            result.ideas_stripped.append(idea_id)
            if not dry_run:
                store.conn.execute(
                    "UPDATE ideas SET asset_paths_json = NULL WHERE idea_id = ?",
                    (idea_id,),
                )
            continue
        result.ideas_deleted.append(idea_id)
        if not dry_run:
            store.conn.execute("DELETE FROM jobs WHERE idea_id = ?", (idea_id,))
            store.conn.execute("DELETE FROM ideas WHERE idea_id = ?", (idea_id,))

    result.runs_deleted = _prune_runs(store, cutoff, dry_run)
    result.jobs_deleted += _prune_jobs(store, cutoff, dry_run)
    _prune_orphan_dirs(store, settings, result)

    if not dry_run:
        store.conn.commit()
        # Reclaim the pages the deletes freed; without this the file only ever
        # grows and the scans stay as slow as they were before the sweep.
        try:
            store.conn.execute("VACUUM")
        except Exception as exc:  # noqa: BLE001 — a busy DB just stays large
            log.warning("vacuum skipped: %s", exc)

    log.info(
        "retention days=%d dry_run=%s cutoff=%s %s",
        days, dry_run, cutoff.date().isoformat(), result.summary(),
    )
    return result


def _prune_runs(store: Store, cutoff: datetime, dry_run: bool) -> int:
    sql = "FROM runs WHERE started_at < ?"
    args = (cutoff.isoformat(),)
    n = int(store.conn.execute(f"SELECT COUNT(*) AS c {sql}", args).fetchone()["c"])
    if n and not dry_run:
        store.conn.execute(f"DELETE {sql}", args)
    return n


def _prune_jobs(store: Store, cutoff: datetime, dry_run: bool) -> int:
    """Finished jobs older than the cutoff. Queued/running work is untouched."""
    sql = "FROM jobs WHERE status NOT IN ('QUEUED','PROCESSING') AND updated_at < ?"
    args = (cutoff.isoformat(),)
    n = int(store.conn.execute(f"SELECT COUNT(*) AS c {sql}", args).fetchone()["c"])
    if n and not dry_run:
        store.conn.execute(f"DELETE {sql}", args)
    return n


def _prune_orphan_dirs(store: Store, settings: Settings, result: PruneResult) -> None:
    """Remove `output/<id>/` folders whose idea row is gone.

    Catches assets left behind by an idea deleted from the UI as well as by an
    earlier sweep that was interrupted between the file delete and the commit.
    """
    out = Path(settings.output_dir)
    if not out.is_dir():
        return
    live = {
        r["idea_id"] for r in store.conn.execute("SELECT idea_id FROM ideas").fetchall()
    }
    for child in out.iterdir():
        if not child.is_dir() or not IDEA_DIR_RE.match(child.name):
            continue
        if child.name in live:
            continue
        result.bytes_freed += _dir_size(child)
        result.orphan_dirs_removed += 1
        if not result.dry_run:
            shutil.rmtree(child, ignore_errors=True)


def sweep_if_due(store: Store, settings: Settings, *, now: datetime | None = None) -> PruneResult | None:
    """Run a sweep at most once a day. Returns `None` when nothing was due.

    The last sweep time lives in `app_settings`, so the schedule survives
    restarts instead of re-sweeping every time the server comes up.
    """
    if settings.retention_days <= 0:
        return None
    now = now or datetime.now(timezone.utc)
    last = _as_utc(store.get_setting(LAST_SWEEP_KEY))
    if last is not None and now - last < timedelta(days=1):
        return None
    result = prune(store, settings, now=now)
    store.set_setting(LAST_SWEEP_KEY, now.isoformat())
    return result


#: Where the last sweep timestamp is stored (app_settings key).
LAST_SWEEP_KEY = "retention_last_sweep"
