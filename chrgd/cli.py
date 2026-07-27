"""`chrgd` command-line interface.

Milestone 1 implements `capture` (plus `backlog` for inspecting the store).
The remaining commands from the build brief are registered as stubs so the
surface is visible and stable; each later milestone fills one in.
"""

from __future__ import annotations

import typer

from .capture import capture_ideas
from .config import get_settings
from .db import Store
from .models import DecaySpeed, Status

app = typer.Typer(
    add_completion=False,
    help="CHRGD content engine — backlog → import-ready carousels + Metricool CSV.",
)


@app.callback()
def _main() -> None:
    """Configure logging once for every command."""
    from .logging_setup import configure_logging

    configure_logging(get_settings().log_level)


def _store() -> Store:
    settings = get_settings()
    settings.ensure_dirs()
    return Store(settings.db_path)


@app.command()
def capture(
    dump: str = typer.Argument(..., help="Rough idea or dump (one idea per line)."),
    priority: int = typer.Option(3, "--priority", "-p", help="1 = highest."),
    category: str = typer.Option("", "--category", "-c", help="content_category."),
    target_viewer: str = typer.Option("", "--target-viewer", help="Who it's for."),
    pain_point: str = typer.Option("", "--pain-point", help="The pain it pokes."),
    core_tension: str = typer.Option("", "--core-tension", help="The tension."),
    learning_tag: str = typer.Option("", "--learning-tag", help="Learning tag."),
    decay: DecaySpeed = typer.Option(
        None, "--decay", help="Topical decay speed: days/weeks/evergreen."
    ),
) -> None:
    """Capture a rough idea or dump into seed backlog rows.

    Splits on newlines / `;` into distinct ideas, dedupes against the
    backlog, and writes queued seed rows. No paid API calls.
    """
    settings = get_settings()
    with _store() as store:
        created, skipped = capture_ideas(
            store,
            settings,
            dump,
            priority=priority,
            content_category=category,
            target_viewer=target_viewer,
            pain_point=pain_point,
            core_tension=core_tension,
            learning_tag=learning_tag,
            decay_speed=decay,
        )

    if created:
        typer.secho(f"Captured {len(created)} idea(s):", fg=typer.colors.GREEN)
        for idea in created:
            typer.echo(f"  {idea.idea_id}  [{idea.status.value}]  {idea.concept_note}")
    if skipped:
        typer.secho(
            f"Skipped {len(skipped)} duplicate(s) already in the backlog:",
            fg=typer.colors.YELLOW,
        )
        for note in skipped:
            typer.echo(f"  - {note}")
    if not created and not skipped:
        typer.secho("Nothing to capture — empty input.", fg=typer.colors.RED)
        raise typer.Exit(code=1)


@app.command()
def backlog(
    status: Status = typer.Option(None, "--status", "-s", help="Filter by status."),
) -> None:
    """List backlog rows (newest last). Handy for testing milestone 1."""
    with _store() as store:
        ideas = store.list_ideas(status=status)
        total = store.count()
        queued = store.count(Status.queued)

    if not ideas:
        typer.echo("Backlog is empty." if status is None else "No matching rows.")
        return

    for idea in ideas:
        prio = f"P{idea.priority}"
        decay = f" decay={idea.decay_speed.value}" if idea.decay_speed else ""
        cat = f" [{idea.content_category}]" if idea.content_category else ""
        typer.echo(
            f"{idea.idea_id}  {idea.status.value:<10} {prio}{cat}{decay}  "
            f"{idea.concept_note}"
        )
    typer.secho(
        f"\n{len(ideas)} shown · {total} total · {queued} queued",
        fg=typer.colors.BLUE,
    )


# --- Stubs for later milestones --------------------------------------------


def _not_yet(name: str, milestone: int) -> None:
    typer.secho(
        f"`chrgd {name}` arrives in milestone {milestone}. "
        "Milestone 1 ships `capture` + `backlog`.",
        fg=typer.colors.YELLOW,
    )
    raise typer.Exit(code=2)


@app.command()
def trends(
    count: int = typer.Option(6, "--count", "-n", help="How many hooks to find."),
    seed: bool = typer.Option(False, "--seed", help="Seed the results as backlog rows."),
) -> None:
    """Scout current UK-gym topical hooks (OpenAI web search).

    Prints a ranked, brand-safe list. With --seed, writes them as queued rows
    (fast-decaying trends get higher priority). Topical hooks only — no live
    TikTok in-app sounds/hashtags.
    """
    from .trends import TrendError, scout_trends, seed_trends

    settings = get_settings()
    try:
        result = scout_trends(settings, count)
    except TrendError as exc:
        typer.secho(f"Trend scout error: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    typer.secho(f"⚠ {result.limitation}", fg=typer.colors.YELLOW)
    for i, t in enumerate(result.trends, 1):
        fast = "  ⏱ ship fast" if t.ships_fast else ""
        typer.secho(f"\n{i}. {t.trend}  [{t.decay_speed.value}]{fast}", fg=typer.colors.GREEN)
        typer.echo(f"   why now: {t.why_now}")
        typer.echo(f"   mechanic: {t.mechanic} · visual: {t.visual_engine}")
        typer.echo(f"   concept: {t.concept_note}")
        typer.echo(f"   brand_fit: {t.brand_fit} · claim_safety: {t.claim_safety}")

    if seed:
        with _store() as store:
            outcome = seed_trends(store, settings, result.trends)
        typer.secho(
            f"\nSeeded {len(outcome.created)} row(s), skipped "
            f"{len(outcome.skipped)} duplicate(s).",
            fg=typer.colors.BLUE,
        )
    elif result.trends:
        typer.secho("\nRe-run with --seed to add these to the backlog.", fg=typer.colors.BLUE)


@app.command()
def build(
    count: int = typer.Option(5, "--count", "-n", help="How many queued ideas."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Run the LLM but skip paid image/video (M3+)."
    ),
) -> None:
    """Run the pipeline over N queued ideas into finished posts.

    Calls OpenAI to run the 6-stage engine, validates the JSON, applies the
    QA gate, and saves each result. Failures are flagged for `chrgd review`.
    """
    from .pipeline import LLMError, build_ideas

    settings = get_settings()
    with _store() as store:
        if store.count(Status.queued) == 0:
            typer.secho("No queued ideas. Capture some first.", fg=typer.colors.YELLOW)
            raise typer.Exit(code=1)
        try:
            results = build_ideas(store, settings, count, dry_run=dry_run)
        except LLMError as exc:
            typer.secho(f"LLM error: {exc}", fg=typer.colors.RED)
            raise typer.Exit(code=1)

    done = [r for r in results if r.status is Status.done]
    review = [r for r in results if r.status is Status.review]
    other = [r for r in results if r.status not in (Status.done, Status.review)]
    spend = sum(r.spend_usd for r in results)

    for r in done:
        typer.secho(
            f"  ✓ {r.idea_id}  built  ({r.attempts} attempt(s))  "
            f"hook: {r.post.hook if r.post else ''}",
            fg=typer.colors.GREEN,
        )
    for r in review:
        typer.secho(
            f"  ! {r.idea_id}  QA review — {', '.join(r.qa_failures)}",
            fg=typer.colors.YELLOW,
        )
    for r in other:
        typer.secho(f"  · {r.idea_id}  {r.error or r.status.value}", fg=typer.colors.RED)

    typer.secho(
        f"\n{len(done)} built · {len(review)} flagged · est. spend ${spend:.4f}",
        fg=typer.colors.BLUE,
    )


@app.command()
def render(
    idea_id: str = typer.Argument(..., help="The idea to (re)render."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Skip the paid image API; paint placeholders."
    ),
) -> None:
    """(Re)generate carousel image assets for one built post.

    Generates a background per slide, overlays the approved text in the safe
    zones, and writes JPEG/WebP slides to output/<idea_id>/. `--dry-run`
    produces branded placeholder backgrounds with no spend.
    """
    from .images import ImageError
    from .models import PostType
    from .services import render_idea
    from .video import VideoDisabledError, VideoError, render_video

    settings = get_settings()
    with _store() as store:
        idea = store.get_idea(idea_id)
        if idea is None:
            typer.secho(f"No idea {idea_id}.", fg=typer.colors.RED)
            raise typer.Exit(code=1)

        if idea.post_type == PostType.video:
            # Video posts render into a reel via the image-to-video pipeline.
            # A job carries the resumable per-clip state (no re-bill on retry).
            job_id = store.create_job("video", idea_id=idea_id, provider="higgsfield")
            try:
                path = render_video(
                    idea, settings, store, job_id=job_id,
                    notify=lambda note: typer.secho(f"  … {note}", fg=typer.colors.BLUE),
                )
            except VideoDisabledError as exc:
                store.update_job(job_id, status="ERROR", error=str(exc))
                typer.secho(f"Video disabled: {exc}", fg=typer.colors.YELLOW)
                raise typer.Exit(code=2)
            except VideoError as exc:
                store.update_job(job_id, status="ERROR", error=str(exc))
                typer.secho(f"Video error: {exc}", fg=typer.colors.RED)
                raise typer.Exit(code=1)
            store.update_job(job_id, status="COMPLETED", progress=100, output_path=path)
            typer.secho(f"\n  ✓ {path}", fg=typer.colors.GREEN)
            return

        try:
            result = render_idea(store, settings, idea, dry_run=dry_run)
        except ImageError as exc:
            typer.secho(f"Render error: {exc}", fg=typer.colors.RED)
            raise typer.Exit(code=1)

    for p in result.paths:
        typer.secho(f"  ✓ {p}", fg=typer.colors.GREEN)
    tag = " (dry-run placeholders)" if result.dry_run else ""
    typer.secho(
        f"\n{len(result.paths)} slide(s){tag} · {result.generated} generated · "
        f"est. spend ${result.spend_usd:.4f}",
        fg=typer.colors.BLUE,
    )


@app.command()
def export(
    week: bool = typer.Option(
        False, "--week", help="Export a week's batch of rendered posts."
    ),
    limit: int = typer.Option(None, "--limit", help="Cap the number of posts."),
    sample: bool = typer.Option(
        False, "--sample", help="Write a sample CSV to diff against Metricool."
    ),
) -> None:
    """Produce the Metricool bulk-import CSV + ready/ asset folder.

    Selects `done`, rendered, not-yet-exported posts; builds one row each
    (schedule, networks, caption+hashtags inline, media refs); copies assets
    into output/ready/; and stamps them exported so nothing goes twice.
    """
    from .publisher import get_publisher

    settings = get_settings()
    settings.ensure_dirs()
    publisher = get_publisher("metricool_csv")

    if sample:
        path = publisher.write_sample(settings)
        typer.secho(f"Sample CSV: {path}", fg=typer.colors.GREEN)
        typer.secho(
            "Diff its headers against Metricool's downloadable template, then "
            "edit config/metricool_columns.toml to match.",
            fg=typer.colors.BLUE,
        )
        return

    # --week defaults to a 7-slot batch; --limit overrides.
    if limit is None and week:
        cols = publisher.cols.schedule
        limit = cols.per_day * 7

    with _store() as store:
        result = publisher.export(store, settings, limit=limit)

    for idea_id, reason in result.skipped:
        typer.secho(f"  · {idea_id} skipped — {reason}", fg=typer.colors.YELLOW)

    if not result.exported_ids:
        typer.secho(
            "Nothing to export (need done + rendered posts).", fg=typer.colors.YELLOW
        )
        return

    for idea_id in result.exported_ids:
        typer.secho(f"  ✓ {idea_id}", fg=typer.colors.GREEN)
    typer.secho(f"\nCSV: {result.csv_path}", fg=typer.colors.BLUE)
    typer.secho(f"Assets: {result.ready_dir}", fg=typer.colors.BLUE)
    typer.secho(
        f"{len(result.exported_ids)} post(s) exported. Bulk-import the CSV in "
        "Metricool and attach media from ready/.",
        fg=typer.colors.BLUE,
    )


@app.command()
def run(
    count: int = typer.Option(5, "--count", "-n", help="Ideas to build this run."),
    scout: bool = typer.Option(False, "--scout", help="Trend-scout + seed first."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Skip paid image calls."),
    no_export: bool = typer.Option(False, "--no-export", help="Stop before export."),
) -> None:
    """Full chain: [scout] → build → render → export. For cron/scheduled runs."""
    from .orchestrate import run_chain
    from .pipeline import LLMError
    from .trends import TrendError

    settings = get_settings()
    try:
        with _store() as store:
            summary = run_chain(
                store, settings, count,
                scout=scout, dry_run=dry_run, do_export=not no_export,
            )
    except (LLMError, TrendError) as exc:
        typer.secho(f"Run aborted: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    typer.secho(
        f"built {len(summary.built)} · review {len(summary.review)} · "
        f"rendered {len(summary.rendered)} · exported {len(summary.exported)} · "
        f"est. spend ${summary.spend_usd:.4f}",
        fg=typer.colors.GREEN,
    )
    if summary.scouted:
        typer.echo(f"scouted + seeded {summary.scouted} trend row(s)")
    if summary.stopped:
        typer.secho(f"stopped: {summary.stopped}", fg=typer.colors.YELLOW)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address."),
    port: int = typer.Option(8000, "--port", help="Port."),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload (dev)."),
) -> None:
    """Launch the web dashboard (needs: pip install -e '.[web]')."""
    try:
        import uvicorn
    except ImportError:
        typer.secho(
            "Web deps missing. Run: pip install -e '.[web]'", fg=typer.colors.RED
        )
        raise typer.Exit(code=1)
    uvicorn.run(
        "chrgd.webapp:create_app", factory=True, host=host, port=port, reload=reload
    )


@app.command()
def review(
    show: str = typer.Option(
        None, "--show", help="Print full built JSON for one idea_id."
    ),
) -> None:
    """List posts flagged at QA for manual review, or show one in full."""
    import json as _json

    with _store() as store:
        if show:
            idea = store.get_idea(show)
            if idea is None:
                typer.secho(f"No idea {show}.", fg=typer.colors.RED)
                raise typer.Exit(code=1)
            payload = {
                "idea_id": idea.idea_id,
                "status": idea.status.value,
                "hook": idea.hook,
                "caption": idea.caption,
                "comment_trigger": idea.comment_trigger,
                "hashtags": _json.loads(idea.hashtags) if idea.hashtags else [],
                "slides": _json.loads(idea.slides_json) if idea.slides_json else [],
                "route": _json.loads(idea.route_json) if idea.route_json else {},
            }
            typer.echo(_json.dumps(payload, indent=2, ensure_ascii=False))
            return

        flagged = store.list_ideas(status=Status.review)

    if not flagged:
        typer.secho("Nothing flagged for review.", fg=typer.colors.GREEN)
        return
    typer.secho(f"{len(flagged)} post(s) flagged at QA:", fg=typer.colors.YELLOW)
    for idea in flagged:
        typer.echo(f"  {idea.idea_id}  {idea.concept_note}")
        if idea.route_json:
            route = _json.loads(idea.route_json)
            qa = route.get("qa", {})
            if qa:
                typer.echo(f"      qa: {qa}")
    typer.secho(
        "\nInspect one with:  chrgd review --show <idea_id>", fg=typer.colors.BLUE
    )


if __name__ == "__main__":
    app()
