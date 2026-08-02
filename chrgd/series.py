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


class CharacterCanon(BaseModel):
    """What this show has established about one character, as it goes.

    The roster gives a character their seed bits; this is what they BECOME.
    A running gag is only running if the engine knows it ran, so every episode
    adds what it landed and the next brief plays or escalates it.
    """

    #: Where they stand right now — free text, one line.
    standing: str = ""
    #: Bits this show has actually landed for them, newest last.
    gags: list[str] = Field(default_factory=list)
    #: How they stand with other characters, keyed by roster key.
    relationships: dict[str, str] = Field(default_factory=dict)
    episodes: int = 0

    def add_gags(self, gags: list[str], *, cap: int = 6) -> None:
        """Append new bits, de-duped, keeping the most recent `cap`.

        Capped because the cast block goes into every episode's prompt: an
        uncapped list would grow until the brief was mostly history, and the
        oldest gags are the ones a serial has already wrung dry.
        """
        for gag in gags:
            gag = (gag or "").strip()
            if gag and gag not in self.gags:
                self.gags.append(gag)
        if len(self.gags) > cap:
            del self.gags[:-cap]


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
    #: What the show has established per character, keyed by roster key —
    #: standing, running gags, relationships. This is the half that makes a
    #: returning character feel like one.
    characters: dict[str, CharacterCanon] = Field(default_factory=dict)
    #: Legacy/simple form: a one-line standing per character. Kept because the
    #: operator may edit it directly and it predates `characters`; it is folded
    #: into the brief alongside the richer record rather than replaced.
    standing: dict[str, str] = Field(default_factory=dict)
    #: Anything the operator wants every episode to know. The escape hatch that
    #: stops the structure above becoming a cage.
    notes: str = ""

    def next_number(self) -> int:
        return max((e.number for e in self.episodes), default=0) + 1

    def recent(self, depth: int = RECAP_DEPTH) -> list[Episode]:
        return sorted(self.episodes, key=lambda e: e.number)[-depth:]

    def for_character(self, key: str) -> CharacterCanon:
        return self.characters.get(key) or CharacterCanon()

    def gags(self, keys: list[str] | None = None) -> dict[str, list[str]]:
        """Landed bits per character, for the cast brief."""
        wanted = set(keys) if keys else None
        return {
            key: rec.gags
            for key, rec in self.characters.items()
            if rec.gags and (wanted is None or key in wanted)
        }

    def standing_for(self, key: str) -> str:
        """The one-line standing, from either place it can be written."""
        return (self.for_character(key).standing or self.standing.get(key, "")).strip()

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
        keys = set(cast_keys) if cast_keys is not None else (
            set(self.characters) | set(self.standing)
        )
        standing: list[tuple[str, str]] = []
        for key in sorted(keys):
            note = self.standing_for(key)
            if note:
                standing.append((key, note))
        if standing:
            from .roster import get_character

            lines.append("")
            lines.append("WHERE THE CAST STANDS RIGHT NOW:")
            for key, note in standing:
                char = get_character(key)
                rec = self.for_character(key)
                line = f"- {char.name if char else key}: {note}"
                if rec.relationships:
                    rels = "; ".join(
                        f"{(get_character(k).name if get_character(k) else k)}: {v}"
                        for k, v in rec.relationships.items() if v.strip()
                    )
                    if rels:
                        line += f"  (with — {rels})"
                lines.append(line)
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


def episode_brief(
    store: Store,
    cast_keys: list[str] | None = None,
    *,
    example: bool = True,
    canon: bool = True,
) -> str:
    """The full Multiverse brief: the world, the cast (with their landed running
    gags) and the canon.

    `example` and `canon` are switchable because the brief is now read by two
    different calls with different needs, and paying for both halves twice is
    pure waste:

    * the STORY call is writing the episode, so it needs everything — what has
      already happened, and the worked example that teaches how to write it;
    * the BUILD call is CUTTING a finished story into slides. It cannot
      contradict a canon it is not allowed to change, and a standard for prose
      it is not writing is a thousand tokens of instruction for a job that no
      longer exists. It needs the world and the cast (for the artwork) and the
      story itself, and nothing else.
    """
    from .roster import cast_block, load_example, load_world, resolve

    cast = resolve(cast_keys)
    record = load_canon(store)
    keys = [c.key for c in cast]
    blocks = [
        load_world().as_block(),
        cast_block(cast, gags=record.gags(keys or None)),
        record.brief_block(cast_keys=keys or None) if canon else "",
        load_example() if example else "",
    ]
    return "\n\n".join(b for b in blocks if b)


# --- writing the canon back, automatically ----------------------------------


EXTRACT_SYSTEM = """You are the continuity editor for a comic serial. You have just been given a finished episode. Record what it established, so the next episode can build on it.

Be strict about three things:
- ONE STATUS CHANGE. Name the single thing that changed for someone: who gained, lost or learned. Not a summary of the plot — the change.
- THE OPEN THREAD. What the ending deliberately left unresolved, in the audience's words. This is the debt the next episode owes.
- RUNNING GAGS. For each character, any repeatable BIT this episode established or escalated — something a future episode could play again and build on. A one-off line is not a bit. If a character did nothing repeatable, give them an empty list. Never invent a gag that isn't in the episode.

Also note where each character now STANDS in one short line, and any relationship that visibly changed.

Return a SINGLE JSON object, no markdown:
{
  "title": "a short episode title",
  "change": "the one status change, one line",
  "unresolved": "the open thread, one line",
  "characters": {
    "<roster key>": {
      "standing": "one line on where they now stand",
      "gags": ["a repeatable bit this episode established"],
      "relationships": {"<other roster key>": "how they now stand with them"}
    }
  }
}"""


class EpisodeRecord(BaseModel):
    title: str = ""
    change: str = ""
    unresolved: str = ""
    characters: dict[str, CharacterCanon] = Field(default_factory=dict)


def _episode_payload(post: dict, cast_keys: list[str]) -> str:
    from .roster import get_character

    lines = []
    if cast_keys:
        names = ", ".join(
            f"{k} ({get_character(k).name})" if get_character(k) else k
            for k in cast_keys
        )
        lines.append(f"Cast (use these exact roster keys): {names}")
        lines.append("")
    lines.append("THE EPISODE:")
    for i, slide in enumerate(post.get("slides") or [], 1):
        for field_name in ("headline", "supporting", "body"):
            text = str((slide or {}).get(field_name) or "").strip()
            if text:
                lines.append(f"[slide {i}] {text}")
    caption = str(post.get("caption") or "").strip()
    if caption:
        lines.append(f"[caption] {caption}")
    return "\n".join(lines)


def parse_record(text: str) -> EpisodeRecord:
    raw = (text or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in the episode record")
    return EpisodeRecord.model_validate(json.loads(raw[start:end + 1]))


def _fallback_record(post: dict, cast_keys: list[str]) -> EpisodeRecord:
    """What gets written when there's no model to extract with.

    Deliberately not nothing: an episode that isn't recorded breaks the serial
    far more than a thin entry does, so we keep the hook as the change and the
    last slide as the open thread and let the operator sharpen it.
    """
    slides = post.get("slides") or []
    first = str((slides[0] or {}).get("headline") or "").strip() if slides else ""
    last = str((slides[-1] or {}).get("headline") or "").strip() if slides else ""
    return EpisodeRecord(
        title=first[:60],
        change=first or "(not recorded — add what changed)",
        unresolved=last,
        characters={k: CharacterCanon() for k in cast_keys},
    )


def record_from_post(
    store: Store,
    post: dict,
    *,
    cast_keys: list[str],
    idea_id: str = "",
    judge=None,
    use_judge: bool = True,
) -> Episode:
    """Extract what an episode established and write it into the canon.

    This is what makes the serial actually serial. Without it the canon only
    grows if someone remembers to fill it in by hand, and episode 2 opens
    knowing nothing about episode 1.

    Never raises: on any LLM/parse failure it falls back to a thin entry rather
    than losing the episode, because a gap in a serial's memory is worse than
    an imprecise line the operator can edit.
    """
    record = None
    if use_judge and judge is not None:
        try:
            record = parse_record(
                judge.judge(EXTRACT_SYSTEM, _episode_payload(post, cast_keys))
            )
        except Exception:  # noqa: BLE001 — never lose an episode to a bad call
            record = None
    if record is None:
        record = _fallback_record(post, cast_keys)

    canon = load_canon(store)
    episode = record_episode(
        store, change=record.change, unresolved=record.unresolved,
        title=record.title, cast=cast_keys, idea_id=idea_id,
    )
    # record_episode persisted; re-read so we extend rather than clobber it.
    canon = load_canon(store)
    from .roster import get_character

    for key, incoming in (record.characters or {}).items():
        if get_character(key) is None:
            continue        # the roster is the allow-list here too
        rec = canon.characters.setdefault(key, CharacterCanon())
        if incoming.standing.strip():
            rec.standing = incoming.standing.strip()
        rec.add_gags(incoming.gags)
        for other, note in (incoming.relationships or {}).items():
            if get_character(other) is not None and str(note).strip():
                rec.relationships[other] = str(note).strip()
        rec.episodes += 1
    save_canon(store, canon)
    return episode
