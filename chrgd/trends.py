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
      "mechanic": "a virality mechanic (e.g. rage_agreement, archetype_ranking)",
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
        # Explicit base_url: see Settings.get_openai_base_url.
        self._client = OpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.get_openai_base_url(),
        )
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


def _iter_json_objects(s: str):
    """Yield each top-level ``{...}`` substring from `s`, string/escape aware."""
    depth = 0
    start = None
    in_str = False
    esc = False
    for i, ch in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    yield s[start : i + 1]
                    start = None


def _salvage_moments(text: str) -> list[dict]:
    """Recover the good moment objects when the model's JSON is imperfect.

    Real model output occasionally has a missing comma, an unescaped quote,
    or is truncated mid-array. Rather than throw the whole scan away, walk the
    ``"moments"`` array and keep every object that parses on its own.
    """
    key = text.rfind('"moments"')
    region = text[key:] if key != -1 else text
    lb = region.find("[")
    if lb != -1:
        region = region[lb + 1 :]
    out: list[dict] = []
    for obj in _iter_json_objects(region):
        try:
            parsed = json.loads(obj)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and parsed.get("title"):
            out.append(parsed)
    return out


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


# --- UK moments radar ---------------------------------------------------------
#
# Broader than the gym-trend scout: what is the UK *collectively* experiencing
# right now / in the next week (sport, weather, telly, viral, seasonal)?
# Content that meets people inside a shared national moment massively
# outperforms generic niche content — the brand's England-game "staying up"
# post did ~20k views against a ~300-view baseline.

MOMENTS_PROMPT = """You are the Cultural Radar for CHRGD, a premium UK gym/supplement brand.

Use web search to find the biggest shared moments people are COLLECTIVELY
experiencing and talking about right now and over the next 7-10 days — both in
the UK and the huge GLOBAL stories a UK audience is fully aware of. You are NOT
looking for gym content — you are looking for shared cultural moments the brand
can show up inside:

- Global mega-moments: World Cup / Olympics / major finals, a huge news story
  everyone's discussing (e.g. something a world leader like Trump just did), a
  worldwide viral moment, a massive release or event. If it's genuinely global,
  say so — these travel further than UK-only stories.
- UK sport: big fixtures, kick-off times, results everyone will be talking about
  (football, boxing, F1, tennis, the lot) — note late/awkward UK kick-off times.
- Weather: heatwaves, storms, cold snaps, the first hot/cold weekend (UK).
- Telly & culture: reality shows, finales, big releases everyone's watching.
- Viral: memes/formats/conversations currently everywhere.
- Seasonal rituals: bank holidays, payday, exam season, January, clock changes.

Aim for a MIX — include a couple of big global moments alongside the UK ones,
ranked by how many people in the UK audience are actually aware of and engaged
with them. A story only counts if the audience genuinely knows about it.

For EACH moment, propose 2-3 ready-to-build content angles for a gym/supplement
audience: usually one practical/advice angle (genuinely useful, e.g. "how to
survive the 3am kick-off"), one funny/relatable angle, and one natural product
tie-in ONLY where it isn't forced (observational/educational, never medical or
guaranteed-outcome claims; humour is social commentary; be light-touch and
non-partisan on politics — never attack a named person, just play the shared
cultural awareness).

Limitation you MUST respect: {limitation}

Return a SINGLE JSON object, no markdown, no commentary:
{{
  "moments": [
    {{
      "title": "the moment in one line (e.g. World Cup semi-final Weds night)",
      "emoji": "one emoji",
      "category": "global | sport | weather | tv | viral | seasonal | news",
      "scope": "global | uk",
      "when": "tonight | this weekend | Thu 10 Jul | now",
      "peak": "when attention peaks",
      "why": "why people care right now, one line",
      "decay_speed": "days | weeks",
      "angles": [
        {{
          "type": "advice | funny | tiein",
          "title": "short label",
          "hook": "the slide-1 hook this would open with",
          "concept_note": "1-2 sentence buildable brief"
        }}
      ]
    }}
  ]
}}
Rank by expected reach for this brand THIS week (a mix of global and UK).
Prefer moments still ahead or live over ones already fading. Only claim-safe,
brand-safe angles.
""".format(limitation=LIMITATION)


class MomentAngle(BaseModel):
    type: str = "advice"  # advice | funny | tiein
    title: str
    hook: str = ""
    concept_note: str


class Moment(BaseModel):
    title: str
    emoji: str = "📌"
    category: str = "news"
    scope: str = "uk"  # global | uk
    when: str = ""
    peak: str = ""
    why: str = ""
    decay_speed: DecaySpeed = DecaySpeed.days
    angles: list[MomentAngle] = Field(default_factory=list)


class MomentsResult(BaseModel):
    limitation: str = LIMITATION
    moments: list[Moment] = Field(default_factory=list)


# The second discovery lane: not time-pegged at all — the fascinating,
# tell-your-mates facts that pull interaction in any week of the year.
EVERGREEN_PROMPT = """You are the Curiosity Scout for CHRGD, a premium UK gym/supplement brand.

Use web search to find genuinely FASCINATING, shareable facts and curiosities —
the kind a normal person reads and immediately tells their mate. Not tied to
any date, season or trend.

CRUCIAL — every fact must live in CHRGD's WORLD so the brand can naturally own
it. That world is: the body and how it works, energy and caffeine, focus and
motivation, performance and strength, recovery and sleep, hydration, nutrition
and food science, muscle, metabolism, the psychology of habits and discipline,
and sport/training history. The fact itself does NOT have to be about
supplements — but a CHRGD (gym/energy/performance) audience must feel it's
"for them", and there must be a believable bridge from the fact back to the
brand's territory. REJECT fascinating facts with no link to the body, energy,
performance or the gym mindset (e.g. space, geology, random history) — those
belong to a different brand, not CHRGD.

Quality bar for each fact:
- Surprising or counterintuitive — a "wait, WHAT?" reaction.
- True and checkable — no myths presented as fact, no shaky pop-science.
- Sticky — explainable in one slide, argued about in the comments.
- On-world — clearly connects to the body / energy / performance / gym mindset.

For EACH fact, propose 2-3 ready-to-build content angles: usually one
straight-telling angle (the fact, told well), one funny/relatable angle, and
one "here's what it means for your training/energy/recovery" angle that bridges
to the brand's territory. At least one angle per fact must make that bridge
explicit (never forced, never a hard sell). Claim-safe: nothing medical, no
cure/treat/prevent/guaranteed-outcome language; supplements observational only.

Limitation you MUST respect: {limitation}

Return a SINGLE JSON object, no markdown, no commentary, in EXACTLY this shape:
{{
  "moments": [
    {{
      "title": "the fact in one tight line",
      "emoji": "one emoji",
      "category": "body | energy | performance | recovery | nutrition | psychology",
      "why": "why a gym/energy audience will stop, share and argue — plus the brand link, one line",
      "decay_speed": "weeks",
      "angles": [
        {{
          "type": "advice | funny | tiein",
          "title": "short label",
          "hook": "the slide-1 hook this would open with",
          "concept_note": "1-2 sentence buildable brief"
        }}
      ]
    }}
  ]
}}
Rank by expected reach. Strongest, most shareable facts first.
""".format(limitation=LIMITATION)

_DISCOVER_PROMPTS = {"moments": MOMENTS_PROMPT, "evergreen": EVERGREEN_PROMPT}
_DISCOVER_ASKS = {
    "moments": (
        "Find up to {count} shared UK moments for the coming week, each with "
        "2-3 ready angles. Rank by expected reach and return the JSON object."
    ),
    "evergreen": (
        "Find up to {count} fascinating evergreen facts, each with 2-3 ready "
        "angles. Rank by shareability and return the JSON object."
    ),
}


def parse_moments(text: str) -> MomentsResult:
    # Strict parse first — the happy path.
    try:
        return MomentsResult.model_validate(json.loads(_extract_json(text)))
    except (json.JSONDecodeError, ValidationError, TrendError):
        pass
    # Salvage: keep every moment object that parses, drop the broken one.
    salvaged = []
    for m in _salvage_moments(text):
        try:
            salvaged.append(Moment.model_validate(m))
        except ValidationError:
            continue
    if salvaged:
        return MomentsResult(moments=salvaged)
    raise TrendError("could not parse any moments from the response")


def scout_discover(
    settings: Settings,
    kind: str = "moments",
    count: int = 6,
    *,
    client: TrendSearchClient | None = None,
) -> MomentsResult:
    """One discovery scan: 'moments' (UK now) or 'evergreen' (worth knowing)."""
    if kind not in _DISCOVER_PROMPTS:
        raise TrendError(f"unknown discover kind '{kind}'")
    client = client or OpenAITrendClient(settings)
    result = parse_moments(
        client.search(_DISCOVER_PROMPTS[kind], _DISCOVER_ASKS[kind].format(count=count))
    )
    result.moments = result.moments[:count]
    return result


def scout_moments(
    settings: Settings, count: int = 6, *, client: TrendSearchClient | None = None
) -> MomentsResult:
    """Scan what the UK is living through right now → pickable moments."""
    return scout_discover(settings, "moments", count, client=client)


# Dig into ONE broad moment → the specific, current headlines inside it.
MOMENT_DETAIL_PROMPT = """You are the Cultural Radar for CHRGD, a premium UK gym/supplement brand.

The user picked one broad moment and wants to go DEEPER — the specific, current
sub-stories, results, headlines and talking points INSIDE that moment RIGHT NOW.
Use web search for the latest concrete developments (e.g. for "Wimbledon": a
British wildcard reaching the semis, a shock result, a specific match tonight;
for "the World Cup": a specific fixture, an upset, a viral incident, a standout
player). Be specific and current — real results/headlines, not generic takes.

For EACH specific sub-story, propose 2-3 ready-to-build content angles for a
gym/supplement audience (one practical/advice, one funny/relatable, one natural
tie-in where it fits). Claim-safe (no medical/guaranteed-outcome language),
brand-safe, light-touch and non-partisan on anything political.

Limitation you MUST respect: {limitation}

Return a SINGLE JSON object, no markdown, no commentary, in EXACTLY this shape —
each "moment" here is ONE specific headline/sub-story:
{{
  "moments": [
    {{
      "title": "the specific headline in one line",
      "emoji": "one emoji",
      "category": "the parent moment's area",
      "when": "now | tonight | today",
      "why": "why the UK audience cares about this specific thing, one line",
      "decay_speed": "days",
      "angles": [
        {{
          "type": "advice | funny | tiein",
          "title": "short label",
          "hook": "the slide-1 hook this would open with",
          "concept_note": "1-2 sentence buildable brief"
        }}
      ]
    }}
  ]
}}
Only genuinely current, specific sub-stories. Strongest/most talked-about first.
""".format(limitation=LIMITATION)


def scout_moment_detail(
    settings: Settings,
    topic: str,
    count: int = 6,
    *,
    client: TrendSearchClient | None = None,
) -> MomentsResult:
    """Dig into one broad moment → its specific current headlines/sub-stories."""
    if not topic.strip():
        raise TrendError("no topic to dig into")
    client = client or OpenAITrendClient(settings)
    user = (
        f"Dig into this moment: \"{topic.strip()}\". Find up to {count} specific, "
        "current sub-stories/headlines inside it right now, each with 2-3 ready "
        "angles. Return the JSON object."
    )
    result = parse_moments(client.search(MOMENT_DETAIL_PROMPT, user))
    result.moments = result.moments[:count]
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
