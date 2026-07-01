"""Idea capture — turn a rough dump into seed backlog rows.

Milestone 1 is deliberately LLM-free (no paid calls). A dump is split into
distinct ideas mechanically: one idea per line, or per `;` separator. Blank
lines and list bullets are ignored. LLM-assisted splitting (cleanly
separating a mushy paragraph into non-overlapping ideas) lands in a later
milestone; until then the user controls separation with line breaks.
"""

from __future__ import annotations

import re

from .config import Settings
from .db import Store
from .models import DecaySpeed, Idea, Status

_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")


def split_dump(dump: str) -> list[str]:
    """Split a rough dump into one concept_note per idea.

    Splits on newlines first; any line still containing `;` is split again.
    Leading list bullets/numbers are stripped. Order is preserved.
    """
    notes: list[str] = []
    for line in dump.splitlines():
        for part in line.split(";"):
            cleaned = _BULLET.sub("", part).strip()
            if cleaned:
                notes.append(cleaned)
    # Single-line dump with no separators: treat as one idea.
    if not notes:
        cleaned = dump.strip()
        if cleaned:
            notes.append(cleaned)
    return notes


def capture_ideas(
    store: Store,
    settings: Settings,
    dump: str,
    *,
    priority: int = 3,
    content_category: str = "",
    target_viewer: str = "",
    pain_point: str = "",
    core_tension: str = "",
    learning_tag: str = "",
    decay_speed: DecaySpeed | None = None,
) -> tuple[list[Idea], list[str]]:
    """Capture a dump into seed rows.

    Returns ``(created, skipped)`` where `skipped` holds concept_notes that
    already existed (deduped). Shared seed metadata applies to every idea
    minted from this dump.
    """
    created: list[Idea] = []
    skipped: list[str] = []

    for note in split_dump(dump):
        if store.find_by_concept_note(note):
            skipped.append(note)
            continue
        idea = Idea(
            idea_id=store.next_idea_id(settings.id_prefix),
            status=Status.queued,
            priority=priority,
            content_category=content_category,
            target_viewer=target_viewer,
            pain_point=pain_point,
            core_tension=core_tension,
            concept_note=note,
            learning_tag=learning_tag,
            decay_speed=decay_speed,
        )
        created.append(store.add_idea(idea))

    return created, skipped
