"""Trend scout (Milestone 5).

Uses OpenAI web search to find current UK-gym-relevant *topical/cultural*
signals, filters hard for brand fit + claim safety, and turns the strong ones
into seed backlog rows with a decay speed and a ship-fast priority.

Hard limitation, stated in every output: we do **not** have live access to
TikTok's in-app trending sounds/hashtags or the For You algorithm. This is
topical/cultural hooks only — never claim a specific sound is trending.

The OpenAI SDK is imported lazily and the search client is injectable so tests
run offline with no key.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, Field, ValidationError

from .config import Settings
from .db import Store
from .models import DecaySpeed, Idea, Status

LIMITATION = (
    "No live access to TikTok in-app trending sounds/hashtags or the For You "
    "feed — these are topical/cultural hooks only, not audio trends."
)

SYSTEM_PROMPT = f"""You are the Trend Scout for CHRGD, a premium UK gym/supplement brand.

Use web search to find CURRENT signals a UK gym/supplement audience is talking
about right now: seasonal/weather hooks (heatwaves, January, dark mornings),
gym discourse and arguments doing the rounds, broader viral formats/memes you
can bend to gym culture, relevant news, cultural moments.

PRIORITY SIGNAL — event collisions: upcoming fixtures/events with awkward UK
timing (late-night World Cup kickoffs, early-morning F1/boxing, midweek
Champions League, marathon weekends) that crash into work, sleep, or training
the next day. These feed the proven best-performing "survival playbook" format
(genuinely useful tactics + British humour, save-first). For each, name the
exact collision (event time x obligation time) in the concept_note and set
decay_speed to "days" — a match-day playbook must post 1-3 days BEFORE the
event.

Filter HARD before returning anything:
- Brand fit: UK gym/supplement culture, dry mate-to-mate voice, no parody dialect.
- Claim safety: NO medical/cure/treat/prevent/guaranteed-outcome language;
  supplements observational/educational only; humour is social commentary, never
  a named individual.
- Shelf life: prefer things still live; flag anything likely already stale.

Limitation you MUST respect and surface: {LIMITATION}

Return a SINGLE JSON object, no markdown, no commentary:
{{
  "limitation": "{LIMITATION}",
  "trends": [
    {{
      "trend": "the trend in one line",
      "why_now": "why this UK gym audience cares right now",
      "decay_speed": "days | weeks | evergreen",
      "mechanic": "a virality mechanic (e.g. survival_playbook, event_collision, rage_agreement, archetype_ranking)",
      "visual_engine": "a visual engine (e.g. real_gym_micro_scene)",
      "brand_fit": "ok | flag: <reason>",
      "claim_safety": "ok | flag: <reason>",
      "concept_note": "one specific lived gym moment this becomes — buildable",
      "target_viewer": "who it's for",
      "pain_point": "the pain it pokes",
      "core_tension": "the tension it plays on"
    }}
  ]
}}
Rank strongest first. Only include trends that pass brand fit AND claim safety.
"""


class Trend(BaseModel):
    trend: str
    why_now: str = ""
    decay_speed: DecaySpeed = DecaySpeed.weeks
    mechanic: str = ""
    visual_engine: str = ""
    brand_fit: str = "ok"
    claim_safety: str = "ok"
    concept_note: str
    target_viewer: str = ""
    pain_point: str = ""
    core_tension: str = ""

    @property
    def ships_fast(self) -> bool:
        return self.decay_speed is DecaySpeed.days


class TrendResult(BaseModel):
    limitation: str = LIMITATION
    trends: list[Trend] = Field(default_factory=list)


class TrendError(RuntimeError):
    pass


# --- search client ----------------------------------------------------------


class TrendSearchClient(Protocol):
    def search(self, system: str, user: str) -> str:
        """Run a web-search-grounded completion, return the model's text."""
        ...


class OpenAITrendClient:
    """OpenAI web search via the Responses API."""

    def __init__(self, settings: Settings):
        if not settings.openai_api_key:
            raise TrendError("OPENAI_API_KEY is not set — add it to your .env")
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise TrendError("openai not installed. Run: pip install -e '.[llm]'") from exc
        kwargs = {"api_key": settings.openai_api_key}
        if settings.openai_base_url:
            kwargs["base_url"] = settings.openai_base_url
        self._client = OpenAI(**kwargs)
        self._model = settings.openai_model

    def search(self, system: str, user: str) -> str:
        try:
            resp = self._client.responses.create(
                model=self._model,
                tools=[{"type": "web_search_preview"}],
                input=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
        except Exception as exc:  # noqa: BLE001
            raise TrendError(str(exc)) from exc
        return getattr(resp, "output_text", "") or ""


# --- parsing + scouting -----------------------------------------------------


def _extract_json(text: str) -> str:
    """Pull the JSON object out of a possibly fenced/annotated response."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{") :]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise TrendError("no JSON object found in trend response")
    return text[start : end + 1]


def parse_trends(text: str) -> TrendResult:
    try:
        return TrendResult.model_validate(json.loads(_extract_json(text)))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise TrendError(f"could not parse trends: {exc}") from exc


def scout_trends(
    settings: Settings, count: int = 6, *, client: TrendSearchClient | None = None
) -> TrendResult:
    """Run the scout and return a ranked, brand-safe TrendResult."""
    client = client or OpenAITrendClient(settings)
    user = (
        f"Find up to {count} strong, current UK-gym-relevant topical/cultural "
        "hooks. Rank strongest first and return the JSON object."
    )
    result = parse_trends(client.search(SYSTEM_PROMPT, user))
    result.trends = result.trends[:count]
    return result


# --- seeding ----------------------------------------------------------------

# Faster-decaying trends jump the queue.
_PRIORITY = {DecaySpeed.days: 1, DecaySpeed.weeks: 2, DecaySpeed.evergreen: 3}


@dataclass
class SeedOutcome:
    created: list[Idea]
    skipped: list[str]


def seed_trends(store: Store, settings: Settings, trends: list[Trend]) -> SeedOutcome:
    """Write strong trends as queued seed rows (deduped on concept_note)."""
    created: list[Idea] = []
    skipped: list[str] = []
    for t in trends:
        if store.find_by_concept_note(t.concept_note):
            skipped.append(t.concept_note)
            continue
        idea = Idea(
            idea_id=store.next_idea_id(settings.id_prefix),
            status=Status.queued,
            priority=_PRIORITY.get(t.decay_speed, 2),
            content_category="trend",
            target_viewer=t.target_viewer,
            pain_point=t.pain_point,
            core_tension=t.core_tension,
            concept_note=t.concept_note,
            learning_tag=f"trend:{t.mechanic}" if t.mechanic else "trend",
            decay_speed=t.decay_speed,
        )
        created.append(store.add_idea(idea))
    return SeedOutcome(created=created, skipped=skipped)
