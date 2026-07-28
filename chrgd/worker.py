"""Background job worker (Phase A2).

A single in-process thread drains the `jobs` table so the web request that
enqueued a build/render returns immediately and the UI can poll progress.

Design choices:
  * **Two in-process workers, one lane each** — a *fast* worker (builds,
    renders, takes, angles…) and a *research* worker (the slow web-search
    scans, `RESEARCH_KINDS`), so a minute-long scan can never delay a build or
    render. The claim step (`db.claim_next_job`) is concurrency-safe via a
    guarded UPDATE, so the two share the `jobs` table without double-processing.
    Still one process, still no Redis/Celery.
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


# The slow research lanes (web-search scans). Split onto their own worker so a
# minute-long scan can never delay a build/render on the fast worker.
RESEARCH_KINDS = frozenset({
    "moments", "evergreen", "trending", "ragebait",
    "moment_detail", "lane_angles", "trends", "meta_scan",
})

# Video renders poll the provider for minutes per clip. Put them on their own
# lane so a reel can never delay a build/render on the fast lane (the same split
# that keeps slow research scans out of the fast lane).
VIDEO_KINDS = frozenset({"video"})


class Worker:
    def __init__(
        self,
        settings: Settings,
        poll_interval: float = 1.0,
        *,
        kinds: "frozenset[str] | None" = None,
        exclude: "frozenset[str] | None" = None,
        name: str = "chrgd-worker",
        recover_on_start: bool = True,
    ):
        self.settings = settings
        self.poll_interval = poll_interval
        # Job-kind partitioning so multiple workers can share the table: `kinds`
        # restricts to those kinds, `exclude` skips them. Default (both None) =
        # the classic single worker that takes everything.
        self.kinds = kinds
        self.exclude = exclude
        self._name = name
        # When several workers run, recovery must happen ONCE before any of them
        # starts — otherwise a fresh worker reaps another's in-flight job as
        # "interrupted". The multi-worker lifespan does it explicitly and passes
        # recover_on_start=False here.
        self._recover_on_start = recover_on_start
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
        if self._recover_on_start:
            self.store().recover_interrupted_jobs()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name=self._name, daemon=True)
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
        job = store.claim_next_job(kinds=self.kinds, exclude=self.exclude)
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


def _gate_reporters(store: Store, job: dict):
    """The two progress channels a render job writes for the UI.

    `notify` is the prose line already shown under the progress bar; `on_stage`
    carries the concept gate's structured stage list alongside it, so the create
    journey can show WHICH of the four checks is running rather than a bar that
    just says "generating…". Both land in the same `result_json`, and the latest
    stage list survives subsequent prose-only notes.
    """
    state: dict = {"note": "", "gate_stages": []}

    def _write() -> None:
        store.update_job(job["job_id"], result_json=json.dumps(state))

    def notify(note: str) -> None:
        state["note"] = note
        _write()

    def on_stage(stages: list) -> None:
        state["gate_stages"] = stages
        _write()

    return notify, on_stage


def _handle_render(store: Store, settings: Settings, job: dict) -> dict:
    from .services import render_idea

    params = json.loads(job["params_json"] or "{}")
    dry_run = bool(params.get("dry_run", False))
    idea = store.get_idea(job["idea_id"])
    if idea is None:
        raise ValueError(f"no such idea {job['idea_id']}")

    def on_slide(done: int, total: int) -> None:
        store.update_job(job["job_id"], progress=int(done * 100 / total))

    notify, on_stage = _gate_reporters(store, job)

    notify("starting — composing the design prompts")
    result = render_idea(
        store, settings, idea, dry_run=dry_run, on_slide=on_slide, notify=notify,
        on_stage=on_stage,
    )
    # Every real render earns the adversarial slide-1 verdict, automatically —
    # the only independent check in the pipeline shouldn't wait for a button.
    # (Dry runs paint placeholders, so there's nothing honest to judge.)
    if not dry_run and result.paths:
        enqueue_scroll_test(store, idea.idea_id)
    return {
        "paths": result.paths,
        "variants": result.variants,
        "spend_usd": result.spend_usd,
    }


def _handle_build_one(store: Store, settings: Settings, job: dict) -> dict:
    """Build one specific idea (the create journey's writing step)."""
    from .pipeline import build_single_idea

    def on_progress(pct: int, note: str) -> None:
        store.update_job(
            job["job_id"], progress=pct, result_json=json.dumps({"note": note})
        )

    result = build_single_idea(
        store, settings, job["idea_id"], on_progress=on_progress
    )
    if result.error:
        # Fail the job loudly — a swallowed LLM error must never leave the UI
        # spinning on a "completed" job. The idea is already back in `queued`.
        raise RuntimeError(result.error)
    return {
        "idea_id": result.idea_id,
        "status": result.status.value,
        "qa_failures": result.qa_failures,
        "spend_usd": result.spend_usd,
    }


def _handle_angles(store: Store, settings: Settings, job: dict) -> dict:
    """Facts → pickable carousel angles (create journey 'facts' door)."""
    from .pipeline import generate_angles

    params = json.loads(job["params_json"] or "{}")
    result = generate_angles(
        str(params.get("facts", "")), settings, per_fact=int(params.get("per_fact", 3))
    )
    run_id = store.start_run("angles")
    store.finish_run(run_id, spend_usd=round(result.spend_usd, 4))
    return {
        "angles": [a.model_dump(mode="json") for a in result.angles],
        "spend_usd": result.spend_usd,
    }


def _handle_takes(store: Store, settings: Settings, job: dict) -> dict:
    """Fan-out: competing takes on one seed, before the expensive write.

    A re-fan (same idea, new round) automatically carries the takes the
    editor already passed on, so fresh rounds diverge instead of repeating.
    """
    from .pipeline import generate_takes

    params = json.loads(job["params_json"] or "{}")
    idea = store.get_idea(job["idea_id"])
    if idea is None:
        raise ValueError(f"no such idea {job['idea_id']}")

    prior: list[dict] = []
    row = store.conn.execute(
        "SELECT result_json FROM jobs WHERE idea_id = ? AND kind = 'takes' "
        "AND status = 'COMPLETED' AND job_id != ? ORDER BY job_id DESC LIMIT 1",
        (idea.idea_id, job["job_id"]),
    ).fetchone()
    if row and row["result_json"]:
        prior = json.loads(row["result_json"]).get("takes", [])

    store.update_job(
        job["job_id"], progress=20,
        result_json=json.dumps({"note": "sketching genuinely different takes"}),
    )
    from .learning import performance_notes
    from .profile import brand_profile_notes
    from .trends import meta_is_stale, meta_notes

    # Concepts reason from the live meta. If the last scan has aged out,
    # queue a refresh in the background — this round still uses what's
    # banked, the next one gets the fresh research.
    if meta_is_stale(store) and not _active_meta_scan(store):
        store.create_job("meta_scan")

    result = generate_takes(
        idea, settings,
        count=int(params.get("count", 5)),
        feedback=str(params.get("feedback", "")),
        prior=prior or None,
        performance_notes="\n\n".join(
            x for x in (
                brand_profile_notes(store),
                performance_notes(store),
                meta_notes(store),
            ) if x
        ),
    )
    run_id = store.start_run("takes")
    store.finish_run(run_id, spend_usd=round(result.spend_usd, 4), notes=idea.idea_id)
    return {
        "takes": [t.model_dump(mode="json") for t in result.takes],
        "spend_usd": result.spend_usd,
    }


def _handle_revise(store: Store, settings: Settings, job: dict) -> dict:
    """Targeted 'punch it up' revision on one QA metric."""
    from .pipeline import build_fields_from_post, creation_prefs, revise_post
    from .models import Status as St

    params = json.loads(job["params_json"] or "{}")
    focus = str(params.get("focus", "overall"))
    idea = store.get_idea(job["idea_id"])
    if idea is None:
        raise ValueError(f"no such idea {job['idea_id']}")

    store.update_job(
        job["job_id"],
        progress=20,
        result_json=json.dumps(
            {"note": f"rewriting to maximise {focus.replace('_', ' ')}"}
        ),
    )
    post, spend = revise_post(idea, focus, settings)
    fields = build_fields_from_post(post, creation_prefs(idea))
    if post.passes_qa():
        store.save_build(idea.idea_id, fields)
        status = St.done
    else:
        store.mark_review(idea.idea_id, fields)
        status = St.review

    run_id = store.start_run("revise")
    store.finish_run(run_id, spend_usd=round(spend, 4), notes=f"{idea.idea_id} {focus}")
    return {
        "idea_id": idea.idea_id,
        "status": status.value,
        "focus": focus,
        "qa_failures": post.qa_failures(),
        "spend_usd": spend,
    }


def _handle_concept(store: Store, settings: Settings, job: dict) -> dict:
    """One concept-development round: seed + brief + editor feedback → brief."""
    from .pipeline import develop_concept

    params = json.loads(job["params_json"] or "{}")
    idea = store.get_idea(job["idea_id"])
    if idea is None:
        raise ValueError(f"no such idea {job['idea_id']}")

    store.update_job(
        job["job_id"], progress=20,
        result_json=json.dumps({"note": "sketching the concept with the engine"}),
    )
    brief, spend = develop_concept(
        idea, settings, feedback=str(params.get("feedback", ""))
    )
    # Merge into route_json without touching status or other creation prefs.
    route = json.loads(idea.route_json) if idea.route_json else {}
    route["concept_brief"] = brief.model_dump()
    store.conn.execute(
        "UPDATE ideas SET route_json = ? WHERE idea_id = ?",
        (json.dumps(route), idea.idea_id),
    )
    store.conn.commit()

    run_id = store.start_run("concept")
    store.finish_run(run_id, spend_usd=round(spend, 4), notes=idea.idea_id)
    return {"brief": brief.model_dump(), "spend_usd": spend}


def _handle_render_slide(store: Store, settings: Settings, job: dict) -> dict:
    """(Re)generate the background(s) for a single slide."""
    from .images import list_variants, render_slide
    from .profile import brand_character_ref, brand_style_note, brand_swipe_style
    from .services import ensure_concept_gated

    params = json.loads(job["params_json"] or "{}")
    idea = store.get_idea(job["idea_id"])
    if idea is None:
        raise ValueError(f"no such idea {job['idea_id']}")
    slide_no = int(params.get("slide", 0))
    dry_run = bool(params.get("dry_run", False))
    notify, on_stage = _gate_reporters(store, job)

    # Slide 1 gets the same gate here as it does on a full render — otherwise
    # the sharper hook the scroll test invents, or one typed in by hand, reaches
    # a paid image without a single check. Unchanged concepts are recognised and
    # skipped, so "regenerate the background" stays exactly that.
    if slide_no == 0 and not dry_run:
        idea, _ = ensure_concept_gated(
            store, settings, idea, notify=notify, on_stage=on_stage
        )

    result = render_slide(
        idea,
        slide_no,
        settings,
        dry_run=dry_run,
        variants=params.get("variants"),
        notify=notify,
        house_style=brand_style_note(store),
        swipe_style=brand_swipe_style(store),
        character_ref_path=brand_character_ref(store, settings),
    )
    # Keep asset_paths_json in step for posts rendered slide-by-slide.
    paths = json.loads(idea.asset_paths_json) if idea.asset_paths_json else []
    if len(paths) > result.slide_index:
        paths[result.slide_index] = result.path
        store.save_asset_paths(idea.idea_id, paths)
    # Regenerating slide 1 changes the whole scroll-stop — re-judge it.
    if result.slide_index == 0 and result.generated and result.path:
        enqueue_scroll_test(store, idea.idea_id)
    if result.generated:
        run_id = store.start_run("render")
        store.finish_run(
            run_id, spend_usd=round(result.spend_usd, 4), notes=idea.idea_id
        )
    return {
        "path": result.path,
        "variant_paths": result.variant_paths,
        "all_variants": list_variants(store.get_idea(idea.idea_id), settings),
        "spend_usd": result.spend_usd,
    }


def _handle_video(store: Store, settings: Settings, job: dict) -> dict:
    """Render one idea's carousel into a reel (submit → poll → download → stitch).

    Resumable: the orchestrator persists per-clip provider ids + downloaded
    paths on this job, so a crash-requeued job re-polls/re-stitches instead of
    re-submitting paid clips. Guarded by the feature flag (OFF → VideoDisabled).
    """
    from .video import render_video

    idea = store.get_idea(job["idea_id"])
    if idea is None:
        raise ValueError(f"no such idea {job['idea_id']}")

    def notify(note: str) -> None:
        store.update_job(job["job_id"], result_json=json.dumps({"note": note}))

    path = render_video(idea, settings, store, job_id=job["job_id"], notify=notify)
    return {"output_path": path}


def _active_meta_scan(store: Store) -> dict | None:
    row = store.conn.execute(
        "SELECT job_id FROM jobs WHERE kind = 'meta_scan' "
        "AND status IN ('QUEUED','PROCESSING') LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def _handle_meta_scan(store: Store, settings: Settings, job: dict) -> dict:
    """Research the live fitness short-form meta from public sources."""
    from .trends import scan_meta

    store.update_job(
        job["job_id"], progress=20,
        result_json=json.dumps(
            {"note": "researching what's winning in fitness short-form right now"}
        ),
    )
    report = scan_meta(settings)
    run_id = store.start_run("meta_scan")
    store.finish_run(run_id, notes=f"as_of={report.as_of}")
    return {"report": report.model_dump(mode="json")}


def _handle_trends(store: Store, settings: Settings, job: dict) -> dict:
    from .trends import scout_trends

    params = json.loads(job["params_json"] or "{}")
    result = scout_trends(settings, int(params.get("count", 6)))
    return {
        "limitation": result.limitation,
        "trends": [t.model_dump(mode="json") for t in result.trends],
    }


def _handle_discover(store: Store, settings: Settings, job: dict) -> dict:
    """One discovery scan: 'moments', 'evergreen', 'trending' or 'ragebait'."""
    from .trends import OpenAITrendClient, scout_discover

    params = json.loads(job["params_json"] or "{}")
    # Ragebait and trending live or die on the sharpness of the WRITTEN claim,
    # so give them the creative model; moments/evergreen are research
    # aggregation and stay on the cheap scout model.
    client = (
        OpenAITrendClient(settings, model=settings.openai_model)
        if job["kind"] in ("ragebait", "trending")
        else None
    )
    # Two-stage: stage 1 returns headlines fast; the angles for a story are
    # written on tap by _handle_lane_angles. Turns a 60-90s "everything" scan
    # into a ~15-25s headline list.
    result = scout_discover(
        settings, job["kind"], int(params.get("count", 6)),
        client=client, headlines_only=True,
    )
    return {
        "limitation": result.limitation,
        "moments": [m.model_dump(mode="json") for m in result.moments],
    }


def _handle_lane_angles(store: Store, settings: Settings, job: dict) -> dict:
    """Stage-2 of a two-stage scan: write the angles for ONE surfaced story."""
    from .trends import OpenAITrendClient, generate_lane_angles

    params = json.loads(job["params_json"] or "{}")
    kind = str(params.get("lane", "moments"))
    client = (
        OpenAITrendClient(settings, model=settings.openai_model)
        if kind in ("ragebait", "trending")
        else None
    )
    angles = generate_lane_angles(
        settings, kind, str(params.get("title", "")), str(params.get("why", "")),
        count=int(params.get("count", 3)), client=client,
    )
    return {"angles": [a.model_dump(mode="json") for a in angles]}


def _handle_concepts(store: Store, settings: Settings, job: dict) -> dict:
    """Stage 1: the fast concept sketch, off the web request.

    Five headline-level concepts on the fast model — seconds, not the minute
    the old full-treatment run took. It still runs as a job (not inline in the
    HTTP request) so a slow day can't have the proxy drop the connection, and
    the create screen polls it the same way it polls builds/takes."""
    from .concepts import sketch_concepts

    params = json.loads(job["params_json"] or "{}")
    return sketch_concepts(
        store, settings,
        seed=str(params.get("seed", "")), fresh=bool(params.get("fresh")),
    )


def _handle_concept_detail(store: Store, settings: Settings, job: dict) -> dict:
    """Stage 2: drill ONE sketched concept down into a ready-to-build brief."""
    from .concepts import develop_concept

    params = json.loads(job["params_json"] or "{}")
    return develop_concept(
        store, settings, params.get("concept") or {}, seed=str(params.get("seed", ""))
    )


def _handle_moment_detail(store: Store, settings: Settings, job: dict) -> dict:
    """Dig into one broad moment → its specific current headlines."""
    from .trends import scout_moment_detail

    params = json.loads(job["params_json"] or "{}")
    result = scout_moment_detail(
        settings, str(params.get("topic", "")), int(params.get("count", 6))
    )
    return {
        "limitation": result.limitation,
        "topic": params.get("topic", ""),
        "moments": [m.model_dump(mode="json") for m in result.moments],
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


def _handle_scroll_test(store: Store, settings: Settings, job: dict) -> dict:
    """Cold scroll test: an honest vision verdict on the finished slide 1."""
    from .scrolltest import run_scroll_test

    idea = store.get_idea(job["idea_id"])
    if idea is None:
        raise ValueError(f"no such idea {job['idea_id']}")
    store.update_job(
        job["job_id"], progress=30,
        result_json=json.dumps({"note": "scroll-testing your finished slide 1"}),
    )
    verdict = run_scroll_test(idea, settings, store)
    # Persist onto the idea so the review UI, the calendar and the learning
    # loop can all see it — a verdict that lives only in a job row teaches
    # nothing and is invisible the moment the job scrolls off the queue.
    fresh = store.get_idea(idea.idea_id)
    route = json.loads(fresh.route_json) if fresh and fresh.route_json else {}
    route["scroll_verdict"] = verdict.model_dump(mode="json")
    store.conn.execute(
        "UPDATE ideas SET route_json = ? WHERE idea_id = ?",
        (json.dumps(route), idea.idea_id),
    )
    store.conn.commit()
    return {"verdict": verdict.model_dump(mode="json")}


_HANDLERS: dict[str, Callable[[Store, Settings, dict], dict]] = {
    "build": _handle_build,
    "build_one": _handle_build_one,
    "render": _handle_render,
    "render_slide": _handle_render_slide,
    "scroll_test": _handle_scroll_test,
    "angles": _handle_angles,
    "takes": _handle_takes,
    "revise": _handle_revise,
    "video": _handle_video,
    "trends": _handle_trends,
    "moments": _handle_discover,
    "evergreen": _handle_discover,
    "trending": _handle_discover,
    "ragebait": _handle_discover,
    "meta_scan": _handle_meta_scan,
    "moment_detail": _handle_moment_detail,
    "lane_angles": _handle_lane_angles,
    "concept": _handle_concept,
    "concepts": _handle_concepts,
    "concept_detail": _handle_concept_detail,
    "run": _handle_run,
}


# --- enqueue helpers (used by the web API) ----------------------------------


def enqueue_build(store: Store, *, count: int, dry_run: bool) -> int:
    return store.create_job("build", params={"count": count, "dry_run": dry_run})


def enqueue_scroll_test(store: Store, idea_id: str) -> int:
    """Judge the finished slide 1 — dedupe an in-flight test for the same idea."""
    existing = store.active_job_for(idea_id, "scroll_test")
    if existing is not None:
        return existing
    return store.create_job("scroll_test", idea_id=idea_id)


def enqueue_render(store: Store, idea_id: str, *, dry_run: bool) -> int:
    existing = store.active_job_for(idea_id, "render")
    if existing:
        return existing["job_id"]  # don't double-queue the same render
    return store.create_job("render", idea_id=idea_id, params={"dry_run": dry_run})


def enqueue_video(store: Store, idea_id: str) -> int:
    """Queue a reel render — reuse an in-flight video job so we never re-submit
    paid clips for the same idea."""
    existing = store.active_job_for(idea_id, "video")
    if existing:
        return existing["job_id"]
    return store.create_job("video", idea_id=idea_id, provider="higgsfield")


def enqueue_trends(store: Store, *, count: int) -> int:
    return store.create_job("trends", params={"count": count})


def enqueue_discover(store: Store, kind: str = "moments", *, count: int = 6) -> int:
    """One scan per lane at a time — reuse an in-flight scan, don't stack."""
    if kind not in ("moments", "evergreen", "trending", "ragebait"):
        raise ValueError(f"unknown discover kind '{kind}'")
    row = store.conn.execute(
        "SELECT job_id FROM jobs WHERE kind = ? "
        "AND status IN ('QUEUED','PROCESSING') ORDER BY job_id DESC LIMIT 1",
        (kind,),
    ).fetchone()
    if row:
        return int(row["job_id"])
    return store.create_job(kind, params={"count": count})


def enqueue_moment_detail(store: Store, *, topic: str, count: int = 6) -> int:
    """Dig into one broad moment — dedupe an in-flight dig for the same topic."""
    row = store.conn.execute(
        "SELECT job_id, params_json FROM jobs WHERE kind = 'moment_detail' "
        "AND status IN ('QUEUED','PROCESSING') ORDER BY job_id DESC LIMIT 1"
    ).fetchone()
    if row and json.loads(row["params_json"] or "{}").get("topic") == topic:
        return int(row["job_id"])
    return store.create_job("moment_detail", params={"topic": topic, "count": count})


def enqueue_concept(store: Store, idea_id: str, *, feedback: str = "") -> int:
    existing = store.active_job_for(idea_id, "concept")
    if existing:
        return existing["job_id"]
    return store.create_job("concept", idea_id=idea_id, params={"feedback": feedback})


def enqueue_concepts(store: Store, *, seed: str = "", fresh: bool = False) -> int:
    """Kick a concept-engine run on the worker. Deduped for the no-seed
    auto-load (a reopened page shouldn't stack runs); a seeded spin or an
    explicit ↻ Fresh set is always a new run, because the editor asked for one
    — handing back the in-flight job there is exactly how "refresh" ended up
    showing the same set again."""
    if not seed.strip() and not fresh:
        active = store.conn.execute(
            "SELECT job_id FROM jobs WHERE kind = 'concepts' "
            "AND status IN ('QUEUED','PROCESSING') "
            "AND COALESCE(params_json,'{}') LIKE '%\"seed\": \"\"%' "
            "ORDER BY job_id DESC LIMIT 1"
        ).fetchone()
        if active:
            return int(active["job_id"])
    return store.create_job("concepts", params={"seed": seed, "fresh": fresh})


def enqueue_concept_detail(store: Store, concept: dict, *, seed: str = "") -> int:
    """Kick the drill-down on one sketched concept. Deduped by title so a
    double-tap on the same card doesn't pay for the same brief twice."""
    title = str(concept.get("title") or "").strip()
    if title:
        rows = store.conn.execute(
            "SELECT job_id, params_json FROM jobs WHERE kind = 'concept_detail' "
            "AND status IN ('QUEUED','PROCESSING') ORDER BY job_id DESC LIMIT 20"
        ).fetchall()
        for row in rows:
            try:
                params = json.loads(row["params_json"] or "{}")
            except json.JSONDecodeError:
                continue
            if str((params.get("concept") or {}).get("title") or "").strip() == title:
                return int(row["job_id"])
    return store.create_job("concept_detail", params={"concept": concept, "seed": seed})


def enqueue_run(store: Store, *, count: int, scout: bool, dry_run: bool) -> int:
    return store.create_job(
        "run", params={"count": count, "scout": scout, "dry_run": dry_run}
    )


def enqueue_build_one(store: Store, idea_id: str) -> int:
    existing = store.active_job_for(idea_id, "build_one")
    if existing:
        return existing["job_id"]
    return store.create_job("build_one", idea_id=idea_id)


def enqueue_angles(store: Store, *, facts: str, per_fact: int = 3) -> int:
    return store.create_job("angles", params={"facts": facts, "per_fact": per_fact})


def enqueue_takes(
    store: Store, idea_id: str, *, feedback: str = "", count: int = 5
) -> int:
    existing = store.active_job_for(idea_id, "takes")
    if existing and not feedback:
        return existing["job_id"]
    return store.create_job(
        "takes", idea_id=idea_id, params={"feedback": feedback, "count": count}
    )


def enqueue_revise(store: Store, idea_id: str, *, focus: str) -> int:
    existing = store.active_job_for(idea_id, "revise")
    if existing:
        return existing["job_id"]
    return store.create_job("revise", idea_id=idea_id, params={"focus": focus})


def enqueue_render_slide(
    store: Store,
    idea_id: str,
    *,
    slide: int,
    dry_run: bool = False,
    variants: int | None = None,
) -> int:
    params: dict = {"slide": slide, "dry_run": dry_run}
    if variants is not None:
        params["variants"] = variants
    return store.create_job("render_slide", idea_id=idea_id, params=params)
