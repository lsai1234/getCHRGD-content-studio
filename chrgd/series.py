"""THE MULTIVERSE's canon — what happened, so the next episode can extend it.

A serial only works if episode 7 remembers episode 1. But a serial also has to
land for someone who has never seen it, or it can only ever shrink. Those two
requirements pull in opposite directions and the resolution is mechanical:

  * **the canon** compounds — every episode writes what changed, and the next
    episode reads it;
  * **the cold open** resets — slide 1 is always written for a newcomer, and
    the canon is the reward for staying rather than the price of entry.

D16: **the operator authors the canon.** This is not an engine-only log the
studio observes. You can write and edit storylines and history directly, and a
hard reset wipes the world and starts it clean. The engine proposes the next
episode from what the log says; you own what it says.

Stored as one JSON document in `app_settings` (the same key→JSON store the
brand profile uses), which is why there's no schema migration here: the canon
is one editable document, a reset is deleting it, and an export is copying it.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from .db import Store

SETTINGS_KEY = "multiverse_canon"

#: How many past episodes the next episode's brief carries. Enough for a
#: storyline to compound, few enough that the prompt stays lean.
RECAP_DEPTH = 6


class Episode(BaseModel):
    number: int = 1
    title: str = ""
    #: The one status change. Exactly one per episode: someone gains, loses or
    #: learns — and it should be caused by a character's flaw, not coincidence.
    change: str = ""
    #: What the cliffhanger left open. This is what the NEXT episode owes.
    unresolved: str = ""
    cast: list[str] = Field(default_factory=list)
    idea_id: str = ""
    created_at: str = ""

    def line(self) -> str:
        bits = [f"Episode {self.number}"]
        if self.title:
            bits.append(f"“{self.title}”")
        text = " ".join(bits) + ": " + (self.change or "—")
        if self.unresolved:
            text += f"  (left open: {self.unresolved})"
        return text


class Season(BaseModel):
    key: str = "villa"
    label: str = "The Villa"
    premise: str = (
        "A reality dating format at Iron Palace. Couplings, recouplings, one "
        "squat rack, and a public vote."
    )
    #: D16 downgraded the vote from structural to optional — with the canon
    #: authored in the studio the serial no longer depends on it to know what
    #: happens next.
    vote: bool = False


class Canon(BaseModel):
    season: Season = Field(default_factory=Season)
    episodes: list[Episode] = Field(default_factory=list)
    #: Where each character stands right now, keyed by roster key. Free text,
    #: written by the operator or carried forward from an episode.
    standing: dict[str, str] = Field(default_factory=dict)
    #: Anything the operator wants every episode to know. The escape hatch that
    #: stops the structure above becoming a cage.
    notes: str = ""

    def next_number(self) -> int:
        return max((e.number for e in self.episodes), default=0) + 1

    def recent(self, depth: int = RECAP_DEPTH) -> list[Episode]:
        return sorted(self.episodes, key=lambda e: e.number)[-depth:]

    def open_thread(self) -> str:
        """What the most recent episode left hanging — the next one owes this."""
        recent = self.recent(1)
        return recent[0].unresolved if recent else ""

    # --- what the write call is told -----------------------------------------

    def brief_block(self, *, cast_keys: list[str] | None = None) -> str:
        """The canon, as the instruction the next episode is written from."""
        lines = [
            f"THE SEASON: {self.season.label}. {self.season.premise}",
            f"THIS IS EPISODE {self.next_number()}.",
        ]
        recent = self.recent()
        if recent:
            lines.append("")
            lines.append(
                "WHAT HAS ALREADY HAPPENED — this is canon. Do not contradict "
                "it, and do not repeat a beat it already used:"
            )
            lines += [f"- {e.line()}" for e in recent]
        thread = self.open_thread()
        if thread:
            lines.append("")
            lines.append(
                f"THE OPEN THREAD this episode owes the audience: {thread}"
            )
        standing = {
            k: v for k, v in self.standing.items()
            if v.strip() and (cast_keys is None or k in set(cast_keys))
        }
        if standing:
            from .roster import get_character

            lines.append("")
            lines.append("WHERE THE CAST STANDS RIGHT NOW:")
            for key, note in standing.items():
                char = get_character(key)
                lines.append(f"- {char.name if char else key}: {note}")
        if self.notes.strip():
            lines.append("")
            lines.append(f"THE OPERATOR'S NOTES (these outrank the above): {self.notes.strip()}")
        lines.append("")
        lines.append(
            "THE COLD OPEN RULE, and it is not optional: slide 1 must land for "
            "someone who has NEVER seen an episode, while still rewarding "
            "someone who has. Establish who is here and what is at stake in "
            "one line. Never write 'previously on' housekeeping — the recap is "
            "a hook that happens to catch people up."
            if recent else
            "This is the FIRST episode: establish the world and the cast fast, "
            "and end on a question worth coming back for."
        )
        return "\n".join(lines)


# --- storage: one editable document (D16) -----------------------------------


def load_canon(store: Store) -> Canon:
    raw = store.get_setting(SETTINGS_KEY)
    if not raw:
        return Canon()
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        return Canon.model_validate(data)
    except Exception:  # noqa: BLE001 — a corrupt canon must not brick the show
        return Canon()


def save_canon(store: Store, canon: Canon) -> None:
    store.set_setting(SETTINGS_KEY, canon.model_dump_json())


def reset_canon(store: Store) -> Canon:
    """The hard reset (D16): wipe the world and start clean.

    Deliberately total — season, episodes, standings and notes — because a
    half-reset leaves contradictions that are worse than starting over. The
    caller is expected to have confirmed with a human first.
    """
    canon = Canon()
    save_canon(store, canon)
    return canon


def record_episode(
    store: Store,
    *,
    change: str,
    unresolved: str = "",
    title: str = "",
    cast: list[str] | None = None,
    idea_id: str = "",
) -> Episode:
    """Append an episode to the canon. Idempotent per idea_id.

    Re-recording the same idea updates its entry rather than adding a second —
    a rebuild of the same post is not a new episode, and a duplicated event
    silently corrupts every recap that follows.
    """
    canon = load_canon(store)
    existing = next((e for e in canon.episodes if idea_id and e.idea_id == idea_id), None)
    if existing is not None:
        existing.change = change or existing.change
        existing.unresolved = unresolved or existing.unresolved
        existing.title = title or existing.title
        if cast:
            existing.cast = list(cast)
        save_canon(store, canon)
        return existing

    episode = Episode(
        number=canon.next_number(),
        title=title,
        change=change,
        unresolved=unresolved,
        cast=list(cast or []),
        idea_id=idea_id,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    canon.episodes.append(episode)
    save_canon(store, canon)
    return episode


def episode_brief(store: Store, cast_keys: list[str] | None = None) -> str:
    """The full Multiverse brief: the world, the cast, and the canon."""
    from .roster import cast_block, load_world, resolve

    cast = resolve(cast_keys)
    blocks = [
        load_world().as_block(),
        cast_block(cast),
        load_canon(store).brief_block(cast_keys=[c.key for c in cast] or None),
    ]
    return "\n\n".join(b for b in blocks if b)
