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

    def __init__(self, settings: Settings, model: str | None = None):
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
        # Research/summarise work uses the cheap scout model by default; lanes
        # whose OUTPUT is shipped creative (ragebait claims, trending angles)
        # pass the creative model in explicitly.
        self._model = model or settings.scout_model

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
with them.

THE AWARENESS TEST (hard filter — apply to every candidate): would millions of
ordinary people in the UK already know about this WITHOUT being told? It counts
only if it's on front pages, all over group chats, or in office small-talk. If
you'd have to explain that it's even happening, it FAILS — cut it. (An
athletics meet in London that isn't making national headlines fails; an England
knockout game, a heatwave, a Love Island final, a story everyone's arguing
about passes.) There is NO quota: return three genuinely huge moments rather
than six padded with niche events — every weak inclusion costs the editor a
wasted post.

THE "IS IT ACTUALLY LIVE?" GATE (apply before anything else): a date on the
calendar is NOT a live moment. The moment must be genuinely happening NOW or
within the next 7-10 days — something the UK is talking about THIS week. If it's
weeks or months away, CUT IT, no matter how big it is (a World Cup that's four
months out is not today's post). Sanity-check the actual date before you include
anything seasonal or annual.

NO GENERIC AWARENESS-DAY / HERITAGE-MONTH FILLER. Awareness days, weeks and
months (Black History Month, Pride, Mental Health Awareness Week, Earth Day,
International Women's Day, World Book Day, and the rest) are the classic content
trap: they're a calendar entry, not a moment people are actually buzzing about,
and a gym/supplement brand posting a bland "a celebration of X" carousel reads
as hollow corporate box-ticking — low reach AND a brand risk. REJECT them by
default. Include one ONLY if BOTH are true: (a) it is genuinely live this exact
week, and (b) you have a sharp, specific, non-obvious, unmistakably-gym angle
that a scrappy brand chasing virality would actually post — never a generic
"various events and activities" summary. When in doubt, cut it and find a real
moment instead.

THINK OUTSIDE THE BOX. The safe, obvious, everyone-else-is-posting-it moment is
the worst pick — it's invisible in a sea of identical brand posts. Favour the
sharp, specific, slightly-unexpected angle a cynical 18-30 UK gym viewer would
actually stop for: the overlooked detail, the contrarian read, the oddly-timed
niche thing that's blowing up. If the strongest thing you can find is a generic
calendar holiday, that is a sign to dig harder, not to settle.

For EACH moment, propose 2-3 ready-to-build content angles for a gym/supplement
audience: usually one practical/advice angle (genuinely useful, e.g. "how to
survive the 3am kick-off"), one funny/relatable angle, and one natural product
tie-in ONLY where it isn't forced (observational/educational, never medical or
guaranteed-outcome claims; humour is social commentary; be light-touch and
non-partisan on politics — never attack a named person, just play the shared
cultural awareness). In every angle the MOMENT stays the star — the angle is
about the moment as people live it, with the brand riding along; never a
product post wearing a topical hat.

Two hard requirements on every moment's angles: (1) each angle must carry at
least one INSIDER-SPECIFIC detail of the moment itself — the exact kick-off
time, the 2am alarm, the named episode, the specific weather — because rounded
generalities read as content-farm filler and specifics read as lived truth;
(2) at least one angle per moment must be the take the OTHER brands WON'T post
— everyone covers the moment head-on, so give us the corner of it nobody else
is standing in (the overlooked side-effect, the contrarian read, the tiny
detail everyone felt but nobody's said).

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
    # Trending lane only: how well this rides as a PHOTO carousel.
    #   'native'    — already a stills+text format (list, take, POV, screenshot)
    #   'adaptable' — a video trend whose IDEA is reframed into a swipe shape
    # Blank on the moments/evergreen lanes (they don't judge carousel fit).
    carousel_fit: str = ""
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
- Sendable — you can complete "sending this to a gym mate says ___ about me"
  for it. If that blank can't be filled, it's trivia people read and forget,
  not content they forward — cut it or find the sharper fact.

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

# The third discovery lane: not events, not facts — what people are actually
# PARTICIPATING in. Formats, memes, challenges, gym discourse: the trends an
# 18-30 TikTok audience is living inside even when nothing is "happening".
TRENDING_PROMPT = """You are the Trend Hijacker for CHRGD, a premium UK gym/supplement brand.

CHRGD posts PHOTO CAROUSELS only — swipeable still images with text. Your job
is NOT to list the biggest trends. The biggest trend is the most saturated:
by the time everyone's on it, a small account posting the obvious take is
invisible. Your job is the INTERSECTION: a rising or under-exploited trend ×
CHRGD's niche (gym, energy, training culture) × an angle nobody else is
taking. Fresh-and-rising beats huge-and-covered, every time.

Use web search for what a UK 18-30, TikTok-native audience is participating in
right now (NOT news/fixtures/weather — another lane covers those). Hunt in two
buckets:
1. CAROUSEL-NATIVE formats — tier lists, "types of ___", hot takes, POV
   text-on-image, red/green flags, screenshot & notes-app posts, fake texts,
   photo dumps, expectation vs reality, "overheard at…", meme cards.
2. ADAPTABLE video trends — a video format whose underlying idea/joke can be
   reframed into a swipe. Keep the idea, drop the motion, and say exactly how.

FOR EVERY CANDIDATE, CALL ITS TIER honestly:
- "emerging" — early signals, small creators, high upside.
- "rising" — clearly growing, not yet wall-to-wall. ← THE SWEET SPOT.
- "peaked" — everywhere this week. Only usable with a twist that reads as
  in-on-the-joke, not late.
- "saturated" — brands are on it. REJECT, unless the angle mocks the
  saturation itself (the only fresh move left).
Prefer rising > emerging > peaked-with-a-twist. Never return a saturated trend
played straight.

THE OBVIOUS-TAKE FIREWALL (hard, per trend): before writing any angle, state
the obvious take — the one every fitness account will post — and REJECT it.
Every angle you return must be a different move from the obvious take. The
bridge formula for each angle: trend format → a SPECIFIC lived moment from
CHRGD's world (a named gym situation, an exact ritual, a price, a time) → the
twist that makes it ours. Generic gym-ification of a trend fails the firewall.

THE CAROUSEL TEST (hard filter): could this land the SAME dopamine as still
images with text someone swipes through? If it needs motion, a transition,
lip-sync or a specific trending SOUND — and can't be reframed into stills —
it FAILS. Cut it. Do not return dance challenges, transition trends or
sound-dependent memes as-is.

THE FEED TEST (hard filter): would a UK 18-30 TikTok user recognise this from
their own feed or group chats THIS week? If only marketers and trend reports
talk about it, it FAILS. No quota: three real, rising, buildable trends beat
six padded ones.

Limitation you MUST respect: {limitation}
Because you cannot see the live For You feed: work from current reporting and
roundups, never claim a specific sound/audio is trending, and describe each
trend precisely enough that the editor can verify it in the app in 30 seconds.

For EACH trend, return 2-3 CAROUSEL angles in CHRGD's world (gym, energy,
training culture). Each angle's concept_note MUST spell out the SWIPE SHAPE
(slide 1 hook → middle beats → final payoff/CTA) and MUST NOT be the obvious
take. Claim-safe as ever: nothing medical, no guaranteed outcomes, humour is
social commentary.

Return a SINGLE JSON object, no markdown, no commentary, in EXACTLY this shape:
{{
  "moments": [
    {{
      "title": "the trend in one line, named the way people say it",
      "emoji": "one emoji",
      "category": "list | take | pov | screenshot | flags | aesthetic | gym | discourse",
      "scope": "global | uk",
      "when": "rising — growing, not saturated | emerging — early | peaked — needs the twist",
      "peak": "when to post to catch it",
      "carousel_fit": "native | adaptable",
      "obvious_take": "the take every fitness account will post — REJECTED",
      "why": "what participating signals + the evidence it's at this tier — and, if adaptable, how the video trend becomes a carousel",
      "decay_speed": "days | weeks",
      "angles": [
        {{
          "type": "advice | funny | tiein",
          "title": "short label",
          "hook": "the slide-1 hook this would open with",
          "concept_note": "the bridge (trend → our specific lived moment → the twist), then the swipe shape: slide 1 → middle beats → payoff/CTA"
        }}
      ]
    }}
  ]
}}
Rank by hijack potential: (tier: rising first) × (niche fit) × (freshness of
the angle vs the obvious take). Never by raw size.
""".format(limitation=LIMITATION)

# The fourth discovery lane: arguments worth starting. Not events, not facts,
# not formats — the genuinely divisive debates in gym culture that a
# deliberately provocative "ragebait" post can ride, because on TikTok a
# comment section at war IS the distribution engine.
RAGEBAIT_PROMPT = """You are the Argument Engineer for CHRGD, a premium UK gym/supplement brand.

Your job is NOT to find balanced debates. It is to ENGINEER provocations:
confident, slightly-overreaching claims that put a specific out-group on the
defensive and make them NEED to reply, while our own crowd cheers, shares and
piles in. On TikTok the comment war is the distribution engine — and the war
starts when someone's identity, effort or spending is threatened, never when a
familiar debate topic is politely raised.

WHO WE FIGHT FOR (the in-group — never the target): people who train seriously
and spend money doing it properly — lifters, early-session regulars, people who
buy protein/creatine/electrolytes/pre-workout and actually read the label.
Every take must land with THEM on the winning side, feeling seen and armed.
The rage comes from an OUT-GROUP: a rival tribe, a lazy habit, an overpriced
product category, or a myth-peddling school of thought. We never rage at our
own buyers.

Use web search for what gym-tok, fitness forums and current reporting are
actually arguing about right now — then ENGINEER each take with this formula:
1. TARGET an out-group behaviour / product category / myth / tribe
   (never an individual, never a brand by name, never a protected group).
2. CLAIM: one confident, slightly-overreaching position that invalidates
   something the out-group has spent money, effort or self-image on. Threat is
   the engine — use at least one lever per take:
   · sunk cost — "you've been paying £X a month for…"
   · effort invalidation — "your 2-hour session is a 40-minute workout with a
     podcast"
   · status inversion — "that's beginner behaviour dressed up as advanced"
   · price/markup exposure — "you're paying for the can, not the caffeine"
3. GAP: leave ONE obvious "well, actually" unaddressed — the opening the
   comments rush to fill. Name it explicitly.
4. RECEIPT: attach the single checkable fact/number we reply with when it
   kicks off. A claim with no receipt is a liability, not a take — cut it.

THE ASYMMETRY TEST (hard filter — this replaces any 50/50 instinct): a great
take is NOT evenly split. Roughly 60-80% of OUR audience should be cheering
and sharing ("finally someone said it"); the remaining out-group must be big
and invested enough to fight loudly in the comments. Apply ruthlessly:
- If nearly everyone would agree → crowd-pleaser, cut it.
- If the people it wounds are OUR OWN BUYERS → cut it, whatever the numbers.
- If it's a tired both-sides debate the internet has already had a thousand
  times (cardio vs weights, as a topic) → cut it, or sharpen it into a
  one-sided claim with a fresh threat lever.

RULES OF THE FIGHT (hard, non-negotiable — provocation, never harm):
- DEFENSIBLE ONLY: every claim must be a position we can back with its receipt
  when challenged — never a fabricated fact, invented statistic, or debunked
  myth presented as truth. The fight is over the take, not over whether we lied.
- Punch at BEHAVIOURS, PRODUCT CATEGORIES, MYTHS and TRIBES — never people:
  no named individuals or brands, nothing aimed at a protected group, no
  body-shaming, no beginner-shaming. Mock choices people can laugh about
  defending, never who someone is.
- Claim safety as ever: nothing medical, no cure/treat/prevent or
  guaranteed-outcome language; supplement claims stay observational.
- The brand must be able to STAND BEHIND every word when it blows up. If the
  only honest reply to backlash would be "we didn't mean it", cut it.

Limitation you MUST respect: {limitation}

For EACH engineered argument, return 2-3 angles (take / ranking / callout),
each executing the full formula.

Return a SINGLE JSON object, no markdown, no commentary, in EXACTLY this shape:
{{
  "moments": [
    {{
      "title": "the CLAIM in one line at full confidence — the take itself, never the debate topic",
      "emoji": "one emoji",
      "category": "etiquette | training | supplements | diet | culture | cardio",
      "when": "live — arguing about it this week | evergreen beef",
      "why": "one line each side — 'in-group cheering: … / out-group raging: …'",
      "decay_speed": "days | weeks",
      "angles": [
        {{
          "type": "take | ranking | callout",
          "title": "short label",
          "hook": "the slide-1 claim, full confidence, threat lever visible in the words",
          "concept_note": "buildable brief ENDING with: 'the fight: <in-group> vs <out-group> over <what> · the gap: <the well-actually left open> · the receipt: <the fact we reply with>'"
        }}
      ]
    }}
  ]
}}
Rank by how hard the out-group will fight while our crowd cheers — the most
asymmetric, most-felt provocations first. Only takes that pass the asymmetry
test AND the rules of the fight.
""".format(limitation=LIMITATION)

_DISCOVER_PROMPTS = {
    "moments": MOMENTS_PROMPT,
    "evergreen": EVERGREEN_PROMPT,
    "trending": TRENDING_PROMPT,
    "ragebait": RAGEBAIT_PROMPT,
}
_DISCOVER_ASKS = {
    "moments": (
        "Find up to {count} shared UK moments for the coming week, each with "
        "2-3 ready angles. Rank by expected reach and return the JSON object."
    ),
    "evergreen": (
        "Find up to {count} fascinating evergreen facts, each with 2-3 ready "
        "angles. Rank by shareability and return the JSON object."
    ),
    "trending": (
        "Find up to {count} trends this audience is participating in right now "
        "that CHRGD can ride AS A PHOTO CAROUSEL — mostly carousel-native "
        "formats (tier lists, 'types of', hot takes, POV, flags, screenshots), "
        "plus any video trends you translate into a swipe shape. Apply the "
        "carousel test; skip anything that only works as video. Each with 2-3 "
        "ready carousel angles whose concept_note states the swipe shape. Rank "
        "by how live and how buildable each is, and return the JSON object."
    ),
    "ragebait": (
        "Find up to {count} genuinely divisive UK gym/fitness debates that "
        "pass the split test, each with 2-3 ready ragebait angles engineered "
        "to start the fight (and each passing the rules of the fight). Rank "
        "by how hard the comments will go and return the JSON object."
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


# Stage-1 (two-stage scans): return the HEADLINES fast; the per-story angles are
# written separately when the editor taps a story (generate_lane_angles). This
# is what turns a 60-90s "everything at once" scan into a ~15-25s headline list.
_HEADLINES_ONLY_SUFFIX = (
    "\n\nHEADLINES ONLY THIS RUN: return each item with its title, emoji, why, "
    "category, scope and when/tier ONLY, and set its \"angles\" to an empty "
    "array []. Do NOT write any angles now — they are generated separately when "
    "the editor taps a story, so spend all your effort finding more and sharper "
    "stories instead."
)


def _with_uk_spine(system: str) -> str:
    """Append the UK market pack to a discovery system prompt (Bet 2), so the
    scout scouts UK-native. Concatenated (not .format-ed) so the pack's £/&
    characters never hit a format pass."""
    from .uk import uk_context_block

    spine = uk_context_block()
    return system + "\n\n" + spine if spine else system


def scout_discover(
    settings: Settings,
    kind: str = "moments",
    count: int = 6,
    *,
    client: TrendSearchClient | None = None,
    headlines_only: bool = False,
) -> MomentsResult:
    """One discovery scan: 'moments' (UK now), 'evergreen' (worth knowing),
    'trending' (formats to ride) or 'ragebait' (arguments worth starting).

    `headlines_only` runs the fast stage-1 pass (titles + why, no angles); the
    angles are then written per-story by `generate_lane_angles`."""
    if kind not in _DISCOVER_PROMPTS:
        raise TrendError(f"unknown discover kind '{kind}'")
    client = client or OpenAITrendClient(settings)
    ask = _DISCOVER_ASKS[kind].format(count=count)
    if headlines_only:
        ask += _HEADLINES_ONLY_SUFFIX
    system = _with_uk_spine(_DISCOVER_PROMPTS[kind])
    # The moments lane opens knowing what week it is in Britain (Bet 2): the
    # curated cultural calendar seeds the season so the scan spends its search
    # budget on the specific live detail, not on rediscovering that it's, say,
    # Six Nations weekend.
    if kind == "moments":
        from .uk import uk_calendar_seed

        seed = uk_calendar_seed()
        if seed:
            ask += "\n\n" + seed
    result = parse_moments(client.search(system, ask))
    result.moments = result.moments[:count]
    return result


def generate_lane_angles(
    settings: Settings,
    kind: str,
    title: str,
    why: str = "",
    *,
    count: int = 3,
    client: TrendSearchClient | None = None,
) -> list[MomentAngle]:
    """Stage-2: write the angles for ONE already-surfaced story, using the
    lane's own rules (ragebait's fight formula, trending's firewall, …). Reuses
    the lane system prompt + parser, so the angles match the lane exactly."""
    if kind not in _DISCOVER_PROMPTS:
        raise TrendError(f"unknown discover kind '{kind}'")
    if not title.strip():
        raise TrendError("no story to write angles for")
    client = client or OpenAITrendClient(settings)
    ask = (
        f"Write {count} ready-to-build carousel angles for this ONE story, "
        "following every rule above. Return the JSON object with a SINGLE "
        "moment — this exact story — carrying the angles.\n\n"
        f"Story: {title.strip()}\n" + (f"Context: {why.strip()}\n" if why.strip() else "")
    )
    result = parse_moments(client.search(_with_uk_spine(_DISCOVER_PROMPTS[kind]), ask))
    angles = result.moments[0].angles if result.moments else []
    return angles[:count]


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


# --- the meta scan: self-updating "what bangs right now" evidence ---------------
#
# The playbook is curated and durable; the META is what's winning THIS month.
# No legitimate raw TikTok firehose exists (no public API; scraping breaks
# TOS), but public evidence does: TikTok Creative Center trend data, viral-post
# breakdowns, creator-economy reporting, case studies with real numbers. This
# scan mines those on a schedule so the concept stage always reasons from the
# live meta — with zero input from the editor.

META_MAX_AGE_DAYS = 7

META_PROMPT = """You are the Meta Analyst for CHRGD, a premium UK gym/supplement brand on TikTok.

Use web search to research what is CURRENTLY winning in short-form fitness/gym
content — TikTok first, carousels especially. You are building the live
evidence base a concept engine will reason from, so be concrete and grounded:

- SPECIFIC winners: posts/creators/campaigns in or near the fitness space that
  reporting, roundups or case studies say performed, WITH the numbers given
  (views/likes/growth). Name them. If a claim has no number, say so.
- HOOK FORMULAS visible across current winners — the actual opening-line
  patterns, quoted or closely paraphrased.
- FORMAT SHIFTS: what's rising and what's fading (carousel vs video, slide
  counts, text density, photo-dump aesthetics, POV styles, audio-less posts).
- TIKTOK CREATIVE CENTER signals for the Sports/Fitness and Health verticals:
  trending hashtags/topics and what they imply for organic content.
- PLATFORM CHANGES that affect reach (e.g. how the algorithm is treating
  carousels, search-led discovery, watch-time weighting) — as reported, dated.

HONESTY RULES (hard): report only what you actually found; NEVER invent
numbers, names or studies. Prefer the last 4-8 weeks; date what you can.
If the search comes back thin on a section, return fewer items — an empty
list beats a made-up one.

Limitation you MUST respect: {limitation}

Return a SINGLE JSON object, no markdown, no commentary:
{{
  "winning_now": [
    {{
      "pattern": "the shape/approach in one line",
      "evidence": "who/what performed with it + numbers as reported (or 'no numbers given')",
      "example": "one named example, as specific as the source allows"
    }}
  ],
  "hook_formulas": ["opening-line pattern seen across winners, quoted/paraphrased"],
  "rising": ["format or behaviour gaining ground, with why"],
  "fading": ["format or behaviour losing ground / overdone, with why"],
  "platform_notes": ["reported platform/algorithm change affecting reach, dated where possible"]
}}
""".format(limitation=LIMITATION)


class MetaFinding(BaseModel):
    pattern: str
    evidence: str = ""
    example: str = ""


class MetaReport(BaseModel):
    as_of: str = ""  # ISO date, stamped by the scan
    winning_now: list[MetaFinding] = Field(default_factory=list)
    hook_formulas: list[str] = Field(default_factory=list)
    rising: list[str] = Field(default_factory=list)
    fading: list[str] = Field(default_factory=list)
    platform_notes: list[str] = Field(default_factory=list)


def scan_meta(
    settings: Settings, *, client: TrendSearchClient | None = None
) -> MetaReport:
    """One research pass over public sources → the current meta report."""
    from datetime import date

    client = client or OpenAITrendClient(settings)
    text = client.search(
        META_PROMPT,
        "Research the current fitness/gym short-form meta (last 4-8 weeks) "
        "and return the JSON object. Grounded findings only — never invent "
        "numbers or names.",
    )
    try:
        report = MetaReport.model_validate(json.loads(_extract_json(text)))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise TrendError(f"could not parse meta report: {exc}") from exc
    report.as_of = date.today().isoformat()
    return report


def _latest_meta_job(store: Store) -> dict | None:
    row = store.conn.execute(
        "SELECT * FROM jobs WHERE kind = 'meta_scan' AND status = 'COMPLETED' "
        "ORDER BY job_id DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def meta_is_stale(store: Store, max_age_days: int = META_MAX_AGE_DAYS) -> bool:
    """True when there's no completed scan, or the newest one has aged out."""
    from datetime import date, timedelta

    job = _latest_meta_job(store)
    if job is None:
        return True
    try:
        report = json.loads(job["result_json"] or "{}").get("report", {})
        as_of = date.fromisoformat(report.get("as_of", ""))
    except (ValueError, json.JSONDecodeError):
        return True
    return date.today() - as_of > timedelta(days=max_age_days)


def meta_notes(store: Store) -> str:
    """The prompt block carrying the live meta ('' until a scan has run)."""
    job = _latest_meta_job(store)
    if job is None:
        return ""
    try:
        data = json.loads(job["result_json"] or "{}").get("report", {})
        report = MetaReport.model_validate(data)
    except (json.JSONDecodeError, ValidationError):
        return ""

    lines = [
        f"CURRENT TIKTOK META — auto-researched from public sources "
        f"(Creative Center, viral breakdowns, reporting) on {report.as_of}. "
        "This is the LIVE meta; weight it above generic instinct, and lean on "
        "it wherever it fits the seed:"
    ]
    for f in report.winning_now[:5]:
        bits = [f.pattern]
        if f.evidence:
            bits.append(f.evidence)
        if f.example:
            bits.append(f"e.g. {f.example}")
        lines.append("- WINNING: " + " — ".join(bits))
    if report.hook_formulas:
        lines.append("- HOOK FORMULAS seen across winners: "
                     + " · ".join(report.hook_formulas[:5]))
    if report.rising:
        lines.append("- RISING: " + " · ".join(report.rising[:4]))
    if report.fading:
        lines.append("- FADING (avoid leaning on these): "
                     + " · ".join(report.fading[:4]))
    if report.platform_notes:
        lines.append("- PLATFORM: " + " · ".join(report.platform_notes[:3]))
    return "\n".join(lines)
