"""Shared engine services used by the CLI, worker, and orchestrator.

Centralises "render one idea" so image spend is recorded in the `runs` table
consistently (previously only build spend was logged). `record_run=False`
lets the orchestrator fold render spend into one combined run row instead of
double-counting.
"""

from __future__ import annotations

from .config import Settings
from .db import Store
from .images import RenderResult, render_carousel
from .logging_setup import get_logger
from .models import Idea

log = get_logger("services")


def render_idea(
    store: Store,
    settings: Settings,
    idea: Idea,
    *,
    dry_run: bool = False,
    record_run: bool = True,
    on_slide=None,
    notify=None,
) -> RenderResult:
    """Render one carousel, persist asset paths, and log image spend."""
    from .profile import brand_style_note, brand_swipe_style

    result = render_carousel(
        idea, settings, dry_run=dry_run, on_slide=on_slide, notify=notify,
        house_style=brand_style_note(store),
        swipe_style=brand_swipe_style(store),
    )
    store.save_asset_paths(idea.idea_id, result.paths)
    log.info(
        "render idea=%s slides=%d generated=%d spend=%.4f dry_run=%s",
        idea.idea_id,
        len(result.paths),
        result.generated,
        result.spend_usd,
        dry_run,
    )
    if record_run and result.generated:
        run_id = store.start_run("render")
        store.finish_run(
            run_id, spend_usd=round(result.spend_usd, 4), notes=idea.idea_id
        )
    return result
