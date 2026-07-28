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
    """Render one carousel, persist asset paths, and log image spend.

    Before the (paid, single) slide-1 image is generated, the concept gate
    develops the opener — inventing rival angles and judging the field — then
    validates and if needed sharpens the winner, so the money is spent on an
    opener that has already beaten alternatives and cleared an independent
    quality bar. The gate is skipped on dry runs and when disabled in settings.
    """
    from .profile import brand_character_ref, brand_style_note, brand_swipe_style

    gate_spend = 0.0
    if not dry_run and settings.concept_gate_enabled:
        from .conceptgate import gate_slide_one

        gate = gate_slide_one(idea, settings, store, notify=notify)
        gate_spend = gate.spend_usd
        idea = gate.idea  # the (possibly sharpened) concept we now render
        log.info(
            "concept_gate idea=%s score=%d/%d rounds=%d refined=%s passed=%s "
            "field=%d swapped=%s glance=%s%s",
            idea.idea_id, gate.score, gate.min_score, gate.rounds,
            gate.refined, gate.passed, len(gate.candidates), gate.swapped,
            gate.glance.stops if gate.glance else "n/a",
            f" error={gate.error}" if gate.error else "",
        )

    result = render_carousel(
        idea, settings, dry_run=dry_run, on_slide=on_slide, notify=notify,
        house_style=brand_style_note(store),
        swipe_style=brand_swipe_style(store),
        character_ref_path=brand_character_ref(store, settings),
    )
    store.save_asset_paths(idea.idea_id, result.paths)
    image_spend = result.spend_usd
    log.info(
        "render idea=%s slides=%d generated=%d image_spend=%.4f gate_spend=%.4f dry_run=%s",
        idea.idea_id,
        len(result.paths),
        result.generated,
        image_spend,
        gate_spend,
        dry_run,
    )
    if record_run and (result.generated or gate_spend):
        run_id = store.start_run("render")
        store.finish_run(
            run_id, spend_usd=round(image_spend + gate_spend, 4), notes=idea.idea_id
        )
    # Report the true cost of the render (image + the gate that guarded it) so
    # the cost guard and dashboard stay honest.
    result.spend_usd = round(image_spend + gate_spend, 4)
    return result
