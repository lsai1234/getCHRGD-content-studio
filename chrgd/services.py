"""Shared engine services used by the CLI, worker, web app, and orchestrator.

Centralises "render one idea" so image spend is recorded in the `runs` table
consistently (previously only build spend was logged). `record_run=False`
lets the orchestrator fold render spend into one combined run row instead of
double-counting.

It also centralises the slide-1 concept gate. Every path that puts a paid
slide-1 image on screen — the create journey, the review screen, a single-slide
regenerate, the CLI, the nightly orchestrator, the sync render endpoint — goes
through `ensure_concept_gated` first, so there is exactly one answer to "has
this opener been checked?" and no door into the engine that skips it.
"""

from __future__ import annotations

import json

from .config import Settings
from .db import Store
from .images import RenderResult, render_carousel
from .logging_setup import get_logger
from .models import Idea

log = get_logger("services")


def ensure_concept_gated(
    store: Store,
    settings: Settings,
    idea: Idea,
    *,
    notify=None,
    on_stage=None,
    force: bool = False,
):
    """Run the slide-1 concept gate unless this exact opener already cleared it.

    Returns `(idea, gate_result_or_None)` — the idea is the (possibly sharpened
    or swapped) one to render. `None` means the gate didn't run, which happens
    when it's disabled or when slide 1's concept is unchanged since it was last
    checked: an editor asking for new pixels on an approved opener should get
    new pixels, not a fresh tournament that quietly rewrites their post. Change
    the hook and it is a new concept, and it gets checked again.
    """
    # A serial's slide 1 is decided by the story, which has already been
    # written and passed a cold read. The gate's job is to invent rival
    # openers and pick the most arresting — on an episode that means four to
    # six extra calls (one of them carrying the whole engine prompt) actively
    # competing with the plot. Skipping it is both cheaper and better.
    from .story import story_from_route

    try:
        route = json.loads(idea.route_json) if idea.route_json else {}
    except (json.JSONDecodeError, TypeError):
        route = {}
    if story_from_route(route) is not None:
        return idea, None

    if not settings.concept_gate_enabled:
        return idea, None
    from .conceptgate import gate_slide_one, needs_gate

    if not force and not needs_gate(idea):
        log.info("concept_gate idea=%s skipped=already-checked", idea.idea_id)
        return idea, None

    gate = gate_slide_one(idea, settings, store, notify=notify, on_stage=on_stage)
    log.info(
        "concept_gate idea=%s score=%d/%d rounds=%d refined=%s passed=%s "
        "field=%d swapped=%s glance=%s%s",
        idea.idea_id, gate.score, gate.min_score, gate.rounds,
        gate.refined, gate.passed, len(gate.candidates), gate.swapped,
        gate.glance.stops if gate.glance else "n/a",
        f" error={gate.error}" if gate.error else "",
    )
    return gate.idea, gate


def render_idea(
    store: Store,
    settings: Settings,
    idea: Idea,
    *,
    dry_run: bool = False,
    record_run: bool = True,
    on_slide=None,
    notify=None,
    on_stage=None,
) -> RenderResult:
    """Render one carousel, persist asset paths, and log image spend.

    Before the (paid, single) slide-1 image is generated, the concept gate
    develops the opener — inventing rival angles and judging the field — then
    validates and if needed sharpens the winner, so the money is spent on an
    opener that has already beaten alternatives and cleared an independent
    quality bar. The gate is skipped on dry runs, when disabled in settings, and
    when this exact opener has already been through it.
    """
    from .profile import brand_character_ref, brand_style_note, brand_swipe_style

    gate_spend = 0.0
    if not dry_run:
        idea, gate = ensure_concept_gated(
            store, settings, idea, notify=notify, on_stage=on_stage
        )
        if gate is not None:
            gate_spend = gate.spend_usd

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
