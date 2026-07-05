"""Spark — the Create page's engine: story → post, and the fact finder.

Two jobs live here:

* **Story research** — the user shares a news story they saw on another
  platform (a URL, a headline, or pasted text). If it needs looking up, a
  web-search-grounded call produces a factual digest that is stored on the
  idea as `source_context`, so the build stays anchored to the real story.
* **Fact finder** — the user has a seed observation ("tonight is loads of
  people's first time staying up for an England game") and wants more facts
  like it: verifiably true, surprising, and aimed squarely at the UK
  gym/football-adjacent audience. Each fact comes back buildable — with a
  concept note and seed fields — so it can be queued or turned into a post
  in one click.

Both reuse the web-search client protocol from `trends` so tests inject fakes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import BaseModel, Field, ValidationError

from .config import Settings
from .db import Store
from .models import DecaySpeed, Idea, Status
from .trends import OpenAITrendClient, TrendSearchClient, _extract_json


class SparkError(RuntimeError):
    pass


# --- story research ----------------------------------------------------------

STORY_SYSTEM_PROMPT = """You are the research desk for CHRGD, a premium UK
gym/supplement brand. The user shares a news story they saw on social media —
a URL, a headline, or a rough description. Use web search to find the actual
story and return a SHORT factual digest the content engine can build from.

Return a SINGLE JSON object, no markdown, no commentary:
{
  "found": true,
  "headline": "the story in one line",
  "digest": "5-10 sentences of verified facts: who, what, when (with dates),
             the numbers, and any detail with comedic or emotional potential
             for a UK audience. Facts only - no angles, no jokes.",
  "uk_relevance": "one line: why a UK gym/football-adjacent audience cares",
  "decay_speed": "days | weeks | evergreen"
}
If you cannot verify the story, return {"found": false, "digest": "what you
could and couldn't confirm"}. NEVER pad the digest with invented specifics.
"""


class StoryDigest(BaseModel):
    found: bool = False
    headline: str = ""
    digest: str = ""
    uk_relevance: str = ""
    decay_speed: DecaySpeed = DecaySpeed.days


def research_story(
    settings: Settings, story: str, *, client: TrendSearchClient | None = None
) -> StoryDigest:
    """Look up a shared story and return a factual digest."""
    client = client or OpenAITrendClient(settings)
    user = f"Research this story and return the JSON object:\n\n{story.strip()}"
    text = client.search(STORY_SYSTEM_PROMPT, user)
    try:
        return StoryDigest.model_validate(json.loads(_extract_json(text)))
    except (json.JSONDecodeError, ValidationError, RuntimeError) as exc:
        raise SparkError(f"could not parse story research: {exc}") from exc


# --- fact finder --------------------------------------------------------------

FACTS_SYSTEM_PROMPT = """You are the fact finder for CHRGD, a premium UK
gym/supplement brand. The user gives you a seed observation — the kind of
"huh, that's true" thought that makes brilliant TikTok content (example:
"tonight will be lots of people's FIRST time staying up late for an England
game"). Your job: use web search to find MORE facts in that vein.

A qualifying fact must be ALL of:
- **True and checkable** — grounded in something you found via search
  (a date, a stat, a record, a fixture, an anniversary). Say where it's from.
- **Surprising or identity-hitting** for a UK gym/football-adjacent audience —
  the reaction should be "wait, really?", "that's me", or "sending this to
  the group chat". Not trivia for trivia's sake.
- **Buildable** — it must suggest a concrete post (a lived moment, a
  comparison, a "you were X years old when..." frame).

Claim safety: no medical/cure/treat/prevent/guaranteed-outcome facts.
No facts targeting a real private individual.

Return a SINGLE JSON object, no markdown, no commentary:
{
  "facts": [
    {
      "fact": "the fact, one line, with its number/date",
      "why_it_lands": "the emotional hit for this audience, one line",
      "source_note": "where this comes from (publication/body, no URL needed)",
      "decay_speed": "days | weeks | evergreen",
      "concept_note": "one specific lived moment this becomes as a post",
      "target_viewer": "who it's for",
      "pain_point": "the pain/nostalgia/identity it pokes",
      "core_tension": "the tension it plays on"
    }
  ]
}
Rank strongest first. Only include facts you actually verified in search —
if you can only stand behind three, return three.
"""


class Fact(BaseModel):
    fact: str
    why_it_lands: str = ""
    source_note: str = ""
    decay_speed: DecaySpeed = DecaySpeed.weeks
    concept_note: str
    target_viewer: str = ""
    pain_point: str = ""
    core_tension: str = ""


class FactResult(BaseModel):
    facts: list[Fact] = Field(default_factory=list)


def find_facts(
    settings: Settings,
    seed: str,
    count: int = 6,
    *,
    client: TrendSearchClient | None = None,
) -> FactResult:
    """Find up to `count` audience-ready facts riffing on a seed observation."""
    client = client or OpenAITrendClient(settings)
    user = (
        f"Seed observation:\n{seed.strip()}\n\n"
        f"Find up to {count} more facts in this vein for the UK audience and "
        "return the JSON object."
    )
    text = client.search(FACTS_SYSTEM_PROMPT, user)
    try:
        result = FactResult.model_validate(json.loads(_extract_json(text)))
    except (json.JSONDecodeError, ValidationError, RuntimeError) as exc:
        raise SparkError(f"could not parse facts: {exc}") from exc
    result.facts = result.facts[:count]
    return result


# --- seeding ------------------------------------------------------------------

_PRIORITY = {DecaySpeed.days: 1, DecaySpeed.weeks: 2, DecaySpeed.evergreen: 3}


@dataclass
class SeedOutcome:
    created: list[Idea]
    skipped: list[str]


def fact_to_idea(store: Store, settings: Settings, fact: Fact) -> Idea:
    """Mint a queued Idea row from one fact (does not insert)."""
    return Idea(
        idea_id=store.next_idea_id(settings.id_prefix),
        status=Status.queued,
        priority=_PRIORITY.get(fact.decay_speed, 2),
        content_category="fact",
        target_viewer=fact.target_viewer,
        pain_point=fact.pain_point,
        core_tension=fact.core_tension,
        concept_note=fact.concept_note,
        source_context=(
            f"Fact: {fact.fact}\n"
            f"Why it lands: {fact.why_it_lands}\n"
            f"Source: {fact.source_note}"
        ),
        learning_tag="fact",
        decay_speed=fact.decay_speed,
    )


def seed_facts(store: Store, settings: Settings, facts: list[Fact]) -> SeedOutcome:
    """Write facts as queued seed rows (deduped on concept_note)."""
    created: list[Idea] = []
    skipped: list[str] = []
    for fact in facts:
        if store.find_by_concept_note(fact.concept_note):
            skipped.append(fact.concept_note)
            continue
        created.append(store.add_idea(fact_to_idea(store, settings, fact)))
    return SeedOutcome(created=created, skipped=skipped)
