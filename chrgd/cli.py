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
def trends() -> None:
    """Scout topical hooks and seed rows. (Milestone 5)"""
    _not_yet("trends", 5)


@app.command()
def build(
    count: int = typer.Option(5, "--count", "-n"),
    type: str = typer.Option("carousel", "--type"),
) -> None:
    """Run the pipeline over N queued ideas into finished posts. (Milestone 2)"""
    _not_yet("build", 2)


@app.command()
def render(idea_id: str = typer.Argument(...)) -> None:
    """(Re)generate assets for one post. (Milestone 3)"""
    _not_yet("render", 3)


@app.command()
def export(week: bool = typer.Option(False, "--week")) -> None:
    """Produce the Metricool CSV + ready/ folder. (Milestone 4)"""
    _not_yet("export", 4)


@app.command()
def run(count: int = typer.Option(5, "--count", "-n")) -> None:
    """Full chain: scout → pick → build → render → export. (Milestone 7)"""
    _not_yet("run", 7)


@app.command()
def review() -> None:
    """List posts flagged at QA for manual review. (Milestone 2)"""
    _not_yet("review", 2)


if __name__ == "__main__":
    app()
