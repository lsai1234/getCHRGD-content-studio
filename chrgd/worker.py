"""Background job worker (Phase A2).

A single in-process thread drains the `jobs` table so the web request that
enqueued a build/render returns immediately and the UI can poll progress.

Design choices:
  * **Single worker** — the claim step assumes one consumer, so the web app
    must run as one process (see deploy docs). Simpler than Redis/Celery and
    plenty for one user.
  * **Resumable, no re-bill** — on startup, non-pollable jobs (build/render)
    left mid-flight by a crash are marked ERROR for manual re-run rather than
    silently re-charged. Video jobs (pollable via external_id) resume in M6.
  * **Its own DB connection** — SQLite connections are per-thread; the worker
    opens its own `Store`.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Callable

from .config import Settings
from .db import Store
from .models import Status


class Worker:
    def __init__(self, settings: Settings, poll_interval: float = 1.0):
        self.settings = settings
        self.poll_interval = poll_interval
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._store: Store | None = None

    # --- lifecycle ----------------------------------------------------------

    def store(self) -> Store:
        if self._store is None:
            self._store = Store(self.settings.db_path)
        return self._store

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.store().recover_interrupted_jobs()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="chrgd-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        if self._store:
            self._store.close()
            self._store = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                did = self.run_once()
            except Exception:  # noqa: BLE001 - a bad job must not kill the loop
                did = None
            if did is None:
                self._stop.wait(self.poll_interval)

    # --- processing ---------------------------------------------------------

    def run_once(self) -> int | None:
        """Process at most one queued job. Returns its job_id, or None if idle."""
        store = self.store()
        job = store.claim_next_job()
        if job is None:
            return None

        handler = _HANDLERS.get(job["kind"])
        try:
            if handler is None:
                raise ValueError(f"no handler for job kind '{job['kind']}'")
            result = handler(store, self.settings, job)
            store.update_job(
                job["job_id"],
                status="COMPLETED",
                progress=100,
                result_json=json.dumps(result or {}),
                cost_usd=(result or {}).get("spend_usd", 0.0),
            )
        except Exception as exc:  # noqa: BLE001 - record failure, keep serving
            store.update_job(job["job_id"], status="ERROR", error=str(exc))
        return job["job_id"]


# --- handlers ---------------------------------------------------------------


def _handle_build(store: Store, settings: Settings, job: dict) -> dict:
    from .pipeline import build_ideas

    params = json.loads(job["params_json"] or "{}")
    count = int(params.get("count", 1))
    dry_run = bool(params.get("dry_run", False))
    results = build_ideas(store, settings, count, dry_run=dry_run)
    return {
        "built": [r.idea_id for r in results if r.status is Status.done],
        "review": [r.idea_id for r in results if r.status is Status.review],
        "spend_usd": sum(r.spend_usd for r in results),
    }


def _handle_render(store: Store, settings: Settings, job: dict) -> dict:
    from .services import render_idea

    params = json.loads(job["params_json"] or "{}")
    dry_run = bool(params.get("dry_run", False))
    idea = store.get_idea(job["idea_id"])
    if idea is None:
        raise ValueError(f"no such idea {job['idea_id']}")
    result = render_idea(store, settings, idea, dry_run=dry_run)
    return {"paths": result.paths, "spend_usd": result.spend_usd}


def _handle_video(store: Store, settings: Settings, job: dict) -> dict:
    from .video import render_video

    render_video(store.get_idea(job["idea_id"]), settings, store)  # raises: OFF/M6
    return {}


def _handle_trends(store: Store, settings: Settings, job: dict) -> dict:
    from .trends import scout_trends

    params = json.loads(job["params_json"] or "{}")
    result = scout_trends(settings, int(params.get("count", 6)))
    return {
        "limitation": result.limitation,
        "trends": [t.model_dump(mode="json") for t in result.trends],
    }


def _handle_run(store: Store, settings: Settings, job: dict) -> dict:
    from .orchestrate import run_chain

    params = json.loads(job["params_json"] or "{}")
    s = run_chain(
        store,
        settings,
        int(params.get("count", 5)),
        scout=bool(params.get("scout", False)),
        dry_run=bool(params.get("dry_run", False)),
    )
    return {
        "built": s.built,
        "review": s.review,
        "rendered": s.rendered,
        "exported": s.exported,
        "spend_usd": s.spend_usd,
        "stopped": s.stopped,
    }


_HANDLERS: dict[str, Callable[[Store, Settings, dict], dict]] = {
    "build": _handle_build,
    "render": _handle_render,
    "video": _handle_video,
    "trends": _handle_trends,
    "run": _handle_run,
}


# --- enqueue helpers (used by the web API) ----------------------------------


def enqueue_build(store: Store, *, count: int, dry_run: bool) -> int:
    return store.create_job("build", params={"count": count, "dry_run": dry_run})


def enqueue_render(store: Store, idea_id: str, *, dry_run: bool) -> int:
    existing = store.active_job_for(idea_id, "render")
    if existing:
        return existing["job_id"]  # don't double-queue the same render
    return store.create_job("render", idea_id=idea_id, params={"dry_run": dry_run})


def enqueue_trends(store: Store, *, count: int) -> int:
    return store.create_job("trends", params={"count": count})


def enqueue_run(store: Store, *, count: int, scout: bool, dry_run: bool) -> int:
    return store.create_job(
        "run", params={"count": count, "scout": scout, "dry_run": dry_run}
    )
