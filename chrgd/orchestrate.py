"""Orchestration (Milestone 7): the full chain.

`run_chain` strings the stages together — optional trend-scout → build → render
→ export — for one-shot or scheduled (cron/timer) operation. It enforces a
single run-wide spend cap across build + render and records one combined `runs`
row so the dashboard's spend total stays honest.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import Settings
from .db import Store
from .logging_setup import get_logger
from .models import Status

log = get_logger("orchestrate")


@dataclass
class RunSummary:
    scouted: int = 0
    built: list[str] = field(default_factory=list)
    review: list[str] = field(default_factory=list)
    rendered: list[str] = field(default_factory=list)
    exported: list[str] = field(default_factory=list)
    spend_usd: float = 0.0
    stopped: str | None = None
    #: What the daily retention sweep cleared out, when one was due this run.
    pruned: str | None = None


def run_chain(
    store: Store,
    settings: Settings,
    count: int,
    *,
    scout: bool = False,
    dry_run: bool = False,
    do_render: bool = True,
    do_export: bool = True,
    build_client=None,
    trend_client=None,
) -> RunSummary:
    from .images import ImageError
    from .pipeline import build_ideas
    from .publisher import get_publisher
    from .services import render_idea

    summary = RunSummary()
    run_id = store.start_run("run")
    total = 0.0
    log.info("run.start count=%d scout=%s dry_run=%s", count, scout, dry_run)

    try:
        if scout:
            from .trends import scout_trends, seed_trends

            res = scout_trends(settings, count, client=trend_client)
            outcome = seed_trends(store, settings, res.trends)
            summary.scouted = len(outcome.created)
            log.info("run.scout seeded=%d skipped=%d", summary.scouted, len(outcome.skipped))

        results = build_ideas(
            store, settings, count, client=build_client, dry_run=dry_run, record_run=False
        )
        summary.built = [r.idea_id for r in results if r.status is Status.done]
        summary.review = [r.idea_id for r in results if r.status is Status.review]
        total += sum(r.spend_usd for r in results)
        log.info(
            "run.build built=%d review=%d spend=%.4f",
            len(summary.built), len(summary.review), total,
        )

        if do_render:
            for idea_id in summary.built:
                if not dry_run and total >= settings.max_spend_per_run:
                    summary.stopped = "spend cap reached before render"
                    log.warning("run.render stopped idea=%s reason=cap", idea_id)
                    break
                idea = store.get_idea(idea_id)
                try:
                    rr = render_idea(store, settings, idea, dry_run=dry_run, record_run=False)
                except ImageError as exc:
                    summary.stopped = f"render error on {idea_id}: {exc}"
                    log.error("run.render error idea=%s err=%s", idea_id, exc)
                    break
                total += rr.spend_usd
                summary.rendered.append(idea_id)

        if do_export and summary.rendered:
            result = get_publisher("metricool_csv").export(store, settings)
            summary.exported = result.exported_ids
            log.info("run.export exported=%d", len(summary.exported))

        summary.spend_usd = round(total, 4)

        # Housekeeping, after the paid work and inside the try so it can never
        # cost the run its summary. A studio driven entirely by the nightly
        # `chrgd run` timer never starts the web app, so this is the only place
        # its retention sweep would otherwise happen.
        from .retention import sweep_if_due

        swept = sweep_if_due(store, settings)
        if swept is not None:
            summary.pruned = swept.summary()
    finally:
        store.finish_run(
            run_id,
            built=len(summary.built),
            exported=len(summary.exported),
            spend_usd=round(total, 4),
            notes=f"run dry_run={dry_run} scout={scout} stopped={summary.stopped}",
        )
        log.info(
            "run.done built=%d rendered=%d exported=%d spend=%.4f stopped=%s",
            len(summary.built), len(summary.rendered), len(summary.exported),
            summary.spend_usd, summary.stopped,
        )
    return summary
