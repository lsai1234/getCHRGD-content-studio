"""The concept engine — the creative leap, not just a feed.

The old lanes (moments / trending / evergreen / ragebait) treated the SOURCE as
the product: they surfaced a raw event/trend/fact and left the editor to invent
the angle. This flips it. The concept engine draws from a mixed live pool — what
the UK is genuinely talking about, what's trending, a stat that stops a thumb, a
debate worth picking a side on, a sharp gym observation — and manufactures the
FINISHED, opinionated, unmistakably-CHRGD post concept.

The north star is the operator's own example: Andy Burnham cutting bus fares to
£2 → "make gyms free like he's making buses free." The source (the policy) is
fuel; the *product* is the leap into a gym angle nobody else would post. That
leap is the whole job here.

It is deliberately NOT siloed: each daily set is varied in flavour (a topical
take, a surprising stat, a hot take, a lived observation) so it never collapses
back into "here's a big event, pick a lane." The editor can also feed it a seed
("Burnham just became PM") and get the gym spin on their own input.

Concept/text only — it renders nothing and never touches the image budget. The
generators are injectable so tests run offline with no key.

TWO STAGES, because waiting is the enemy of a daily habit:
  * `sketch_concepts` — five concepts at HEADLINE level (the leap + the slide-1
    hook), on the fast model with a lean prompt. This is what the create screen
    opens on, and it lands in seconds.
  * `develop_concept` — the full creative treatment (why it'll travel, what to
    build, the carousel's beats) for the ONE concept the editor drills into, on
    the creative model with the whole evidence base behind it.
A sketch is buildable as-is, so the drill-down is a choice, never a toll gate.
"""

from __future__ import annotations

import json
import logging

from pydantic import BaseModel, ValidationError

from .config import Settings
from .db import Store

# Flavours exist for VARIETY, not as rigid lanes — the engine mixes them across a
# set so the day's concepts aren't all the same shape. Free-form is tolerated;
# these are the ones we nudge toward and label nicely in the UI.
FLAVOURS = ("topical", "stat", "hot_take", "observation")

_MAX_CONCEPTS = 5

# --- the speed budget ---------------------------------------------------------
# The old single-stage engine wrote all five concepts in FULL (hook + why +
# angle + source) on the creative model, with the whole brand bible and viral
# playbook in the prompt — a 60-90s wait before the create screen showed
# anything. It's now two stages:
#   1. SKETCH  — five headline-level concepts on the fast model with a lean
#                prompt and a capped response. Seconds, not a minute.
#   2. DRILL   — the full creative treatment (why it'll travel, what to build,
#                the sharpened hook) for the ONE concept the editor taps, on
#                the creative model with the full evidence base.
# Most days the editor builds one concept, so four fifths of the old wait was
# work nobody read.
_SKETCH_CANDIDATES = 8    # live-signal lines handed to the sketch (was 14)
_SKETCH_MAX_TOKENS = 900  # five short concepts fit easily; the cap keeps it quick
_SKETCH_TITLE_CHARS = 110  # candidate titles are trimmed to keep the prompt lean

# --- keeping "Fresh set" actually fresh ---------------------------------------
# Left alone, every spin sees the same live pool in the same order and leads with
# the same biggest story, so refreshing only ever reworded yesterday's top idea.
# Two things fix that, and both are needed:
#   * MEMORY — the concepts already pitched recently go into the prompt as a
#     banned list, subject and all, not just as titles to avoid rewording.
#   * ROTATION — each spin sees the candidate pool from a different starting
#     point, so the raw material itself changes rather than the phrasing.
_RECALL_SETS = 4          # how many recent sets are held as "already pitched"
_RECALL_TITLES = 16       # ceiling on the banned list, so the prompt stays lean


class ConceptError(RuntimeError):
    pass


class Concept(BaseModel):
    flavour: str = ""      # topical | stat | hot_take | observation (or free-form)
    title: str = ""        # the concept in one line — the creative leap itself
    hook: str = ""         # the slide-1 opener a scroller sees first
    why: str = ""          # one line: why this will actually perform
    angle: str = ""        # what to build — the seed the carousel is written from
    source: str = ""       # the real signal/seed it sprang from (transparency)
    beats: list[str] = []  # the carousel's shape, filled in at drill-down
    amp: bool = False      # stars Amp → builds through the charge-cycle mechanic

    def build_seed(self) -> str:
        """The text handed to the build (create/start, mode=idea)."""
        base = (self.angle or self.title).strip()
        if self.hook.strip():
            base += f" — open with: {self.hook.strip()}"
        return base

    def is_detailed(self) -> bool:
        """True once it's been drilled down (it carries the treatment, not just
        the headline). A sketch is still buildable — build_seed falls back to
        the title + hook — so the editor never has to wait for the drill."""
        return bool(self.why.strip() and self.angle.strip())

    def as_payload(self) -> dict:
        return {
            **self.model_dump(),
            "build_seed": self.build_seed(),
            "detailed": self.is_detailed(),
        }


SKETCH_SYSTEM = """You are the creative director for CHRGD, a premium UK gym/supplement brand on TikTok. It posts ONE photo carousel a day, so every concept has to earn its reach.

You are PITCHING, fast. Give the editor five headline-level concepts to choose from — the creative LEAP in one line each, plus the slide-1 hook. No essays, no reasoning out loud: the chosen one gets developed properly afterwards.

FIVE GENUINELY DIFFERENT IDEAS — not one idea five ways. Each concept must have its own SUBJECT: a different story, a different observation, a different argument. Two concepts about the same event with different wording are ONE concept and you have wasted a slot. Before you return the set, check each pair: if you could describe two of them with the same sentence, replace one.

THE LEAP is the whole job. Don't list what's happening — turn it into a gym idea nobody else would post. The operator's own example: Andy Burnham cutting bus fares to £2 → "make gyms free like he's making buses free". The policy is FUEL; the gym angle is the product.

Each concept must run on at least one real driver of spread — a CURIOSITY GAP only swiping closes, HIGH-AROUSAL emotion (laughter, righteous anger, "that's so me"), SOCIAL CURRENCY (sharing it makes the sharer look good), TRIBE/identity, a self-recognition TAG, or a side worth arguing over. If it hits none, bin it and write another.

Fast quality rules:
- SPECIFIC beats general — an exact number, time, place or behaviour ("the bloke who re-racks the 8kg dumbbells with a grunt"), never "annoying gym people".
- VARY the five: mix topical, stat, hot take and lived observation. Never five of the same shape, and never the safe obvious one everyone's already posting.
- Ground the facts, invent the angle: topical concepts must come from the REAL signal or editor seed given — never invent news. Any number must be plausibly true and claim-safe.
- No awareness-day / heritage-month filler. A calendar date is not a moment.
- UK-native: British spelling, £, real UK gym life (PureGym/The Gym Group, meal deals, the 6pm rush, payday, the leg-day limp).
- Claim-safe: no medical or guaranteed-outcome claims; on politics stay light-touch and non-partisan (play the analogy, never attack a named person).

Slide 1 is ~90% of reach, so the hook must be concrete and thumb-stopping — never a vague blog title.

ONE OF THE FIVE MUST STAR AMP, the getCHRGD mascot — mark it with "amp": true and flavour "amp". Amp is an electric-blue cartoon lightning bolt with a face, stubby arms and legs, and his posts run on ONE mechanic: the CHARGE CYCLE. He starts the post drained flat by a real, relatable drain event (leg day, a 4am flight, the 3pm slump, a night out before a session), the middle turns it around, and he ends fully charged. He is a lovable try-hard on the journey WITH the audience, never a superhero who has it sorted, and the humour is cheeky and dry-British. So an Amp concept is a SITUATION that drains him — pitch the drain event and the turnaround, not a slogan. Vary it every time: a new drain, a new setting, a new joke.

Return a SINGLE JSON object, no markdown, no commentary. Keep every field to one short line:
{
  "concepts": [
    {
      "flavour": "topical | stat | hot_take | observation | amp",
      "title": "the concept in one punchy line — the leap itself",
      "hook": "the slide-1 opener (concrete, thumb-stopping) — one line",
      "source": "the real signal or seed it sprang from, or ''",
      "amp": true only on the one Amp concept, otherwise omit it
    }
  ]
}
Exactly five, ordered best-first — four made from the live pool plus the one Amp concept, which does not have to be last."""


DEVELOP_SYSTEM = """You are the creative director for CHRGD, a premium UK gym/supplement brand on TikTok, developing ONE already-chosen concept into a ready-to-build brief.

The editor picked this concept from a pitch list. Your job is to make it as strong as it can be and hand back what the carousel gets written from. Keep its identity — sharpen it, don't swap it for a different idea.

WHY THINGS ACTUALLY SPREAD — the concept must be built on AT LEAST ONE of these, and you must name which in `why`:
- CURIOSITY GAP: a specific question the viewer NEEDS closed, that only swiping answers.
- HIGH-AROUSAL EMOTION: real laughter, righteous anger, "OMG that's so me", awe at a mad number. Low-arousal doesn't travel.
- SOCIAL CURRENCY: reposting it must make the SHARER look funny, in-the-know, smart or right.
- IDENTITY / TRIBE: it lets the viewer plant a flag — "this is my kind of gym".
- SELF-RECOGNITION → THE TAG: the more SPECIFIC the behaviour, the harder the tag.
- A SIDE TO TAKE → THE COMMENT WAR: a real, defensible opinion that splits the room. Never a lie.

FUNNY, DONE PROPERLY: specificity IS the joke; set an expectation then snap it; it lands because it's TRUE and nobody's said it out loud; punch UP (the industry, the fads, ourselves), never down at the viewer. If you can't say why it's funny in one line, it isn't.

INTERESTING, DONE PROPERLY: counterintuitive beats true-but-obvious; concrete beats abstract; the reframe that makes them go "I never thought of it like that". Plausibly true and claim-safe — a dodgy stat is a brand risk, not a win.

Hard rules: ground the facts, invent the angle (never invent news beyond the real signal given); UK-native throughout (British spelling, £, real UK gym life); claim-safe and brand-safe; light-touch and non-partisan on anything political.

THE SELF-AUDIT: would a cynical, hard-to-impress 18-30 UK gym-goer GENUINELY stop and react, or scroll? If it's a scroll, fix the concept — sharpen the hook, get more specific, find the better angle — before you return it.

Return a SINGLE JSON object, no markdown, no commentary:
{
  "flavour": "topical | stat | hot_take | observation",
  "title": "the concept in one punchy line (keep or sharpen the given one)",
  "hook": "the slide-1 opener — concrete and thumb-stopping",
  "why": "the SPECIFIC spread-driver it hits + the actual joke or surprise (never 'this will perform well')",
  "angle": "what to build — one clear line the carousel is written from",
  "source": "the real signal or seed it sprang from, or ''",
  "beats": ["3-6 short slide beats — the shape of the carousel, one line each"]
}"""


CONCEPT_SYSTEM = """You are the creative director for CHRGD, a premium UK gym/supplement brand on TikTok. The account is small and posts ONE photo carousel a day, so each concept has to genuinely earn reach — no filler.

Your job is NOT to list what's happening. It's to MANUFACTURE finished, ready-to-build post concepts — to make the creative LEAP from raw material into a sharp, opinionated, unmistakably-gym idea a cynical 18-30 UK gym viewer would actually stop for.

The leap is everything. The operator's own example, learn from it:
- Raw material: Andy Burnham is cutting UK bus fares to £2 and taking VAT off them.
- The concept: "Make gyms free like he's making buses free" — a cheeky, opinionated CHRGD take arguing gym memberships should be the next thing subsidised, with the actual numbers. The policy is just FUEL; the gym angle is the product.
More in that spirit:
- A stat: "The average PureGym has ~6,000 members and about 30 squat racks. Here's your real odds of getting one at 6pm." (a true number, reframed into relatable rage)
- A hot take: "Ozempic is the new pre-workout and nobody wants to admit it." (a debate worth picking a side on)
- An observation: the exact micro-behaviour in every UK gym nobody's named yet ("the bloke who films every set on a tripod").

Draw from a MIXED pool and VARY the flavour across the set — a topical take, a surprising stat, a hot take, a lived observation. Never return four of the same shape, and never lead with the safe, obvious, everyone-else-is-posting-it idea.

WHY THINGS ACTUALLY SPREAD — this is not vibes, it's mechanics. Every concept must be built on AT LEAST ONE of these real drivers, and you must be able to name which. A post that hits none of them dies in the feed, no matter how "nice" it is:
- CURIOSITY GAP: open a specific question in the viewer's head they NEED closed, that only swiping answers ("your real odds of a free 6pm squat rack" — you HAVE to see slide 2). A gap is specific; "gym tips" opens no gap.
- HIGH-AROUSAL EMOTION: the emotions that get shared are high-energy — real laughter, righteous anger, "OMG that's so me", awe at a mad number, indignation. Low-arousal (mild interest, gentle positivity) does NOT travel. Aim for a genuine spike, not a nod.
- SOCIAL CURRENCY: reposting it must make the SHARER look good — funny, in-the-know, smart, right. "If I share this, what does it say about me?" needs a good answer.
- IDENTITY / TRIBE: it lets the viewer plant a flag — "this is my kind of gym", "this is us", "this is what we think". People share what signals who they are.
- SELF-RECOGNITION → THE TAG: "that's literally me / that's Dave" makes them tag Dave in the comments. The more SPECIFIC the behaviour, the harder the tag.
- A SIDE TO TAKE → THE COMMENT WAR: a real, defensible opinion people will argue over. Never a lie — a take the brand can genuinely stand behind, sharp enough to split the room.

FUNNY, DONE PROPERLY (if a concept is comedy it has to actually be funny — not just "gym-humour flavoured"):
- SPECIFICITY is the joke. "The bloke who re-racks the 8kg dumbbells with a grunt" is funny; "annoying gym people" is not. Name the exact detail.
- INCONGRUITY / SUBVERSION: set an expectation, then snap it — the twist is the laugh.
- RECOGNITION: it lands because it's TRUE and nobody's said it out loud.
- Exaggerate to the logical extreme, or play it deadpan. Punch UP (at the industry, the fads, ourselves) — NEVER down at the viewer.
- If you can't say WHY it's funny in one line, it isn't. Bin it.

INTERESTING, DONE PROPERLY (if it's a stat / fact / idea):
- COUNTERINTUITIVE beats true-but-obvious — the best fact violates what people assume.
- CONCRETE beats abstract — a specific number, time or name sticks; a rounded generality evaporates.
- THE REFRAME: take something everyone knows and turn it 45 degrees so they go "I never thought about it like that".
- It must be plausibly TRUE and claim-safe. A fake or dodgy stat is a brand risk, not a win.

Slide 1 is ~90% of reach, so the hook must be concrete and thumb-stopping — a named situation, an exact number or time — never a vague blog title.

THE SELF-AUDIT (run this on EVERY concept before you return it — this is the step that kills lazy, random takes). Silently answer for each: (1) which spread-driver above does it hit? (2) if it's meant to be funny, what is the actual joke in one line — and if it's meant to be interesting, what is the exact question or surprise? (3) would a cynical, hard-to-impress 18-30 UK gym-goer GENUINELY stop and react, or would they scroll past? If you can't answer (1) and (2), or the honest answer to (3) is "scroll", DELETE the concept and build a better one. Only return concepts that survive this. The `why` field must state the driver plus the joke/surprise — not a generic "this will perform well".

Hard rules:
- GROUND THE FACTS, INVENT THE ANGLE. Any topical/event concept must be based on the REAL live signal provided or the editor's seed — never invent news, fake fixtures or fake results. The leap (the gym angle) is yours to invent; the underlying fact is not. Stat/observation/hot-take concepts can come from real gym truth, but any number you cite must be plausibly true and claim-safe.
- IS IT LIVE? A calendar date is not a moment. Topical concepts must be something the UK is talking about now / this week. No generic awareness-day or heritage-month filler ("a celebration of X with various events") — it's invisible and reads as corporate box-ticking.
- UK-native throughout: British spelling, £, real UK gym life (PureGym/The Gym Group/JD, meal deals, the 6pm rush, payday, the leg-day limp).
- Claim-safe and brand-safe: no medical or guaranteed-outcome claims; on anything political stay light-touch and non-partisan — play the shared cultural awareness or a policy analogy (like the Burnham one), never a partisan attack on a named person.

Return a SINGLE JSON object, no markdown, no commentary:
{
  "concepts": [
    {
      "flavour": "topical | stat | hot_take | observation",
      "title": "the concept in one punchy line — the leap itself",
      "hook": "the slide-1 opener a scroller sees first (concrete, thumb-stopping)",
      "why": "the SPECIFIC spread-driver it hits + the actual joke or surprise (not 'this will perform well')",
      "angle": "what to build — one clear line the carousel is written from",
      "source": "the real signal or seed this sprang from (or '' for an evergreen idea)"
    }
  ]
}
Return your strongest few (up to 5), ordered best-first. Quality over quantity — three brilliant concepts beat five with a dud."""


def recent_concepts(
    store: Store, *, sets: int = _RECALL_SETS, show: str = ""
) -> list[dict]:
    """Titles + sources from the last few concept sets, newest first.

    This is the engine's short-term memory. Without it every spin starts from a
    blank slate against an unchanged live pool, which is exactly why "Fresh set"
    kept returning the same core idea in new words.

    `show` scopes the memory to one show's sets. Sets are read a few extra deep
    when filtering so a show still gets a full recall window when the shows are
    interleaved across the week.
    """
    depth = max(1, sets) * (4 if show else 1)
    rows = store.conn.execute(
        "SELECT result_json FROM jobs WHERE kind = 'concepts' "
        "AND status = 'COMPLETED' ORDER BY job_id DESC LIMIT ?",
        (depth,),
    ).fetchall()
    out: list[dict] = []
    seen: set[str] = set()
    kept = 0
    for row in rows:
        try:
            data = json.loads(row["result_json"] or "{}")
        except json.JSONDecodeError:
            continue
        if show and str(data.get("show") or "") != show:
            continue
        if not show and str(data.get("show") or ""):
            continue
        kept += 1
        if kept > max(1, sets):
            break
        for c in data.get("concepts") or []:
            title = str(c.get("title") or "").strip()
            key = title.lower()
            if not title or key in seen:
                continue
            seen.add(key)
            out.append({"title": title, "source": str(c.get("source") or "").strip()})
    return out[:_RECALL_TITLES]


def _already_pitched(store: Store, *, show: str = "") -> str:
    """The banned list: what the editor has already been shown, and why a new
    wording of it doesn't count as a new idea.

    Scoped per show: an Amp situation and a Straight Up question are not
    competing for the same slot, so freshness memory shouldn't bleed between
    formats and rule out a subject one show has never used.
    """
    recent = recent_concepts(store, show=show)
    if not recent:
        return ""
    lines = [
        "ALREADY PITCHED TO THIS EDITOR (they have seen these and asked for "
        "something else — do NOT return any of them, and do NOT return a "
        "reworded, re-angled or narrower version of one. A different headline "
        "on the same underlying story or observation is the SAME concept. Go to "
        "different subject matter entirely):"
    ]
    for c in recent:
        lines.append(
            f"- {c['title']}" + (f"  [from: {c['source']}]" if c["source"] else "")
        )
    return "\n".join(lines)


def _fuel(store: Store, seed: str, *, limit: int = 14, brief: bool = False,
          rotate: int = 0) -> str:
    """The mixed live pool the leap is made from: real scouted signal + the UK
    calendar + whatever the editor just fed in. Grounds the topical concepts so
    the engine invents angles, not news.

    `brief` trims it for the fast sketch — fewer candidates, headlines only, no
    per-story rationale. Prompt size is the single biggest lever on how long the
    first paint takes, and the sketch doesn't need the long tail.

    `rotate` starts the candidate list at a different offset (wrapping round).
    The pool is ranked, so without this the same top story sat at the top of
    every spin and the model kept leading with it — the set changed wording, not
    subject. Rotating means a refresh genuinely looks at different material."""
    from .todayspick import gather_candidates
    from .uk import uk_calendar_seed

    lines: list[str] = []
    # Pull a wider pool than we show so rotation has somewhere to move to.
    cands = gather_candidates(store, limit=max(limit * 2, limit))
    if cands and rotate:
        off = rotate % len(cands)
        cands = cands[off:] + cands[:off]
    cands = cands[:limit]
    if cands:
        lines.append(
            "LIVE SIGNAL scouted for the UK right now (REAL — use as fuel for "
            "topical concepts; do not invent news beyond this):"
        )
        for c in cands:
            if brief:
                lines.append(f"- ({c.lane}) {c.title[:_SKETCH_TITLE_CHARS]}")
            else:
                lines.append(
                    f"- ({c.lane}) {c.title}" + (f" — {c.why}" if c.why else "")
                )
    cal = uk_calendar_seed()
    if cal:
        lines.append(cal)
    if seed.strip():
        lines.append(
            "THE EDITOR JUST FED YOU THIS — make your STRONGEST concepts the gym "
            "leap on it (ground the facts, invent the angle):\n" + seed.strip()
        )
    # No scouted scan signal → nudge toward the flavours that don't need it, so
    # the engine still delivers even before the radar has warmed.
    if not cands and not seed.strip():
        lines.append(
            "No live scouted signal yet — lead with evergreen stat, hot-take and "
            "observation concepts from real UK gym truth."
        )
    return "\n".join(lines)


def _evidence(store: Store) -> str:
    """The real 'what works' base the concepts must stand on — the brand's own
    voice + gold-standard examples, the proven viral shapes, and THIS account's
    actual hit/flop history. This is what stops it inventing in a vacuum and
    keeps the leaps in CHRGD's voice, not generic AI banter."""
    from .learning import performance_notes
    from .pipeline import load_brand_bible, load_playbook

    parts: list[str] = []
    bible = load_brand_bible()
    if bible:
        parts.append(
            "BRAND VOICE + GOLD-STANDARD EXAMPLES (match this exact voice and this "
            "level of craft — imitate the how, not the topics):\n" + bible
        )
    playbook = load_playbook()
    if playbook:
        parts.append(
            "PROVEN VIRAL SHAPES (anchor concepts in these where they fit):\n" + playbook
        )
    notes = performance_notes(store)  # '' until this account has real results
    if notes:
        parts.append(notes)
    return "\n\n".join(parts)


def _steering(store: Store) -> str:
    """Pillars + what's actually hit for this account — so concepts fit THIS
    account's lanes and lean toward what works, not generic best practice."""
    from .learning import insights
    from .profile import load_profile

    lines: list[str] = []
    profile = load_profile(store)
    pillars = [p.strip() for p in (profile.pillars or "").splitlines() if p.strip()]
    if pillars:
        lines.append("CONTENT PILLARS (stay roughly in these lanes): " + " · ".join(pillars))
    if (profile.voice or "").strip():
        lines.append(f"BRAND VOICE: {profile.voice.strip()}")
    hit = insights(store).get("hit_traits", [])
    if hit:
        top = ", ".join(f"{t['value']} ({t['trait']})" for t in hit[:3])
        lines.append(f"WHAT HAS HIT FOR THIS ACCOUNT: {top} — lean this way where it fits.")
    return "\n".join(lines)


def _parse_concepts(text: str, count: int) -> list[Concept]:
    text = (text or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ConceptError("no JSON object in the concept response")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ConceptError(f"could not parse concepts: {exc}") from exc
    out: list[Concept] = []
    for raw in data.get("concepts", []) or []:
        try:
            c = Concept.model_validate(raw)
        except ValidationError:
            continue
        if c.title.strip() or c.angle.strip():
            out.append(c)
    return out[:count]


def generate_concepts(
    store: Store,
    settings: Settings,
    *,
    seed: str = "",
    count: int = 4,
    generator=None,
) -> dict:
    """Manufacture today's post concepts from the mixed live pool (+ optional
    editor seed). Never raises: on any LLM/parse failure returns an empty list
    with the error, so the create screen degrades to the manual doors."""
    count = max(1, min(count, _MAX_CONCEPTS))
    try:
        if generator is None:
            from .pipeline import OpenAIChatClient

            generator = OpenAIChatClient(settings)  # creative model, high temp
        from datetime import date

        tail = (
            f"TODAY IS {date.today():%A, %-d %B %Y} — any topical concept must be "
            "genuinely live around now, never a past-year story treated as current. "
            f"Manufacture up to {count} finished, varied, ready-to-build concepts. "
            "Make the creative leap on each, then run the SELF-AUDIT and drop any "
            "that don't survive it. Return the JSON object."
        )
        user = "\n\n".join(
            block for block in (
                _fuel(store, seed),
                _evidence(store),
                _steering(store),
                tail,
            ) if block
        )
        result = generator.complete(CONCEPT_SYSTEM, user)
        concepts = _parse_concepts(result.content, count)
        return {
            "concepts": [c.as_payload() for c in concepts],
            "seeded": bool(seed.strip()),
        }
    except Exception as exc:  # noqa: BLE001 — best-effort feature, never a blocker
        # Catch broadly (LLM SDK errors, parse errors, anything) so the screen
        # always gets a clean, readable reason instead of a 500. Logged so the
        # real cause is visible server-side, and returned so the operator (this
        # is a single-user internal tool) can see it too.
        logging.getLogger(__name__).warning("concept engine failed: %s", exc)
        return {"concepts": [], "seeded": bool(seed.strip()), "error": str(exc)}


# --- stage 1: the fast sketch --------------------------------------------------

# Amp's posts are all one shape — a real drain event, then the charge back up —
# so the reliable way to keep him varied is to vary the DRAIN. These are the
# backstop: if the model returns a set with no Amp concept in it, one of these
# becomes the fifth card rather than the editor losing him for that spin. Each
# is a sketch, so drilling down still develops it properly on the creative model.
_AMP_DRAINS = (
    ("Leg day left Amp on 4%",
     "Amp can't do the stairs. Amp lives on the ground floor."),
    ("Amp at 6pm: 40 minutes waiting for a squat rack",
     "Amp's been holding this rack queue since 5:22."),
    ("The 3pm slump has Amp face-down on the desk",
     "It's 3pm. Amp has achieved nothing since the meal deal."),
    ("Amp tried to train the morning after a night out",
     "Amp said 'one drink'. Amp is now doing bicep curls at 12%."),
    ("Amp's pre-workout kicked in during the drive there",
     "Peak energy: the car park. Actual session: 9%."),
    ("Amp did January like everyone else and hit the wall in week 3",
     "Amp's new year, new me lasted 19 days."),
    ("Amp's 5am gym alarm vs Amp's 5am self",
     "Amp set the alarm. Amp did not consult Amp."),
    ("Amp forgot his headphones and had to train in silence",
     "No headphones. Just Amp, the gym radio, and his own thoughts."),
)


def _ensure_amp(concepts: list[Concept], rotate: int = 0) -> list[Concept]:
    """Guarantee the set carries an Amp concept.

    The editor wants Amp on the table every day, so his slot can't depend on
    the model remembering the instruction. If it did remember, this is a no-op;
    if it didn't, the weakest card gives way to a rotating drain-event sketch."""
    if any(c.amp for c in concepts):
        return concepts
    title, hook = _AMP_DRAINS[rotate % len(_AMP_DRAINS)]
    amp = Concept(flavour="amp", title=title, hook=hook, amp=True,
                  source="Amp — the charge cycle")
    if len(concepts) >= _MAX_CONCEPTS:
        concepts = concepts[: _MAX_CONCEPTS - 1]
    return concepts + [amp]


def sketch_concepts(
    store: Store,
    settings: Settings,
    *,
    seed: str = "",
    count: int = _MAX_CONCEPTS,
    fresh: bool = False,
    generator=None,
    show: str = "",
) -> dict:
    """Five headline-level concepts, FAST — the create screen's first paint.

    Everything here is tuned for latency: the cheap scout model, a lean prompt
    (no brand bible, no playbook, trimmed live signal) and a capped response.
    The heavy evidence base and the full creative treatment belong to
    `develop_concept`, which runs on the ONE concept the editor actually taps.

    `fresh` is the ↻ Fresh set path and it means what it says: the recent sets
    go in as a banned list and the live pool is rotated, so the editor gets new
    SUBJECTS rather than the same idea reworded.

    `show` pitches five concepts INSIDE one show's format rather than five
    generic ones — five Amp situations, or five Straight Up questions. This is
    the highest-leverage part of the show layer: everything else improves how a
    post is made, this improves what gets proposed, which is upstream of all of
    it. Off a show (the default) the behaviour is exactly as it was.

    With no show, one of the five stars Amp (the mascot), so his charge-cycle
    format is always on the table without the editor going to find it. On a
    show that slot would cost a concept, so it's dropped.

    Never raises: on any LLM/parse failure returns an empty list with the error,
    so the create screen degrades to the manual doors."""
    count = max(1, min(count, _MAX_CONCEPTS))
    try:
        if generator is None:
            from .pipeline import OpenAIChatClient

            # The scout model, not the creative one: this is a pitch list, not
            # shipped copy, and it has to land in seconds.
            generator = OpenAIChatClient(
                settings, temperature=0.9,
                model=settings.scout_model, max_tokens=_SKETCH_MAX_TOKENS,
            )
        from datetime import date

        # A different slice of the pool per spin, so a refresh has genuinely
        # different raw material in front of it rather than the same ranked list.
        rotate = len(recent_concepts(store)) if fresh else 0
        from .shows import get_show

        chosen = get_show(show)
        tail = (
            f"TODAY IS {date.today():%A, %-d %B %Y} — any topical concept must be "
            "genuinely live around now, never a past-year story treated as current. "
            f"Pitch {count} varied concepts, headline-level only (title + hook), "
            "strongest first. "
            + ("" if chosen is not None else "One of them stars Amp. ")
            + "Return the JSON object."
        )
        # The show's brief replaces the generic pitch rules: every concept must
        # be an episode of THIS show, so the editor picks between five versions
        # of the thing they actually came to make.
        show_block = ""
        if chosen is not None:
            show_block = (
                chosen.brief_block(include_length=False)
                + "\n\nPitch concepts that are EPISODES OF THIS SHOW — five "
                "different subjects that all fit its format, voice and spine. "
                "A concept that would work just as well on another show is the "
                "wrong concept."
            )
        if fresh:
            tail += (
                "\n\nTHIS IS A REFRESH — the editor rejected the last set. Five "
                "NEW subjects: different stories, different observations, "
                "different arguments. If your first instinct is one of the "
                "already-pitched ideas above, that's the one to throw away."
            )
        user = "\n\n".join(
            block for block in (
                show_block,
                _fuel(store, seed, limit=_SKETCH_CANDIDATES, brief=True,
                      rotate=rotate),
                _already_pitched(store, show=show) if fresh else "",
                _steering(store),
                tail,
            ) if block
        )
        result = generator.complete(SKETCH_SYSTEM, user)
        concepts = _parse_concepts(result.content, count)
        # The forced Amp slot exists to smuggle the mascot into a generic set.
        # On a show it's redundant (AMP is its own show now) and it would cost
        # a concept, so it only runs off-format.
        if chosen is None:
            concepts = _ensure_amp(concepts, rotate)
        return {
            "concepts": [c.as_payload() for c in concepts],
            "seeded": bool(seed.strip()),
            "stage": "sketch",
            "show": show,
        }
    except Exception as exc:  # noqa: BLE001 — best-effort feature, never a blocker
        logging.getLogger(__name__).warning("concept sketch failed: %s", exc)
        return {
            "concepts": [], "seeded": bool(seed.strip()),
            "stage": "sketch", "show": show, "error": str(exc),
        }


# --- stage 2: the drill-down (one concept, full treatment) ---------------------


def _parse_concept(text: str) -> Concept:
    text = (text or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ConceptError("no JSON object in the concept response")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ConceptError(f"could not develop the concept: {exc}") from exc
    # Tolerate a model that wraps the single concept in the list shape.
    if isinstance(data.get("concepts"), list) and data["concepts"]:
        data = data["concepts"][0]
    try:
        return Concept.model_validate(data)
    except ValidationError as exc:
        raise ConceptError(f"could not develop the concept: {exc}") from exc


def _amp_brief() -> str:
    """Who Amp is and how his posts work, for the drill-down.

    Pulled from `character.py` rather than restated here, so the concept engine
    and the image/build path can never drift on who he is."""
    from .character import MECHANIC_LABEL, PERSONA, SIGN_OFF

    return "\n".join([
        f"THIS IS AN AMP POST — the '{MECHANIC_LABEL}' format, starring the "
        "getCHRGD mascot: an electric-blue cartoon lightning bolt with a face "
        "and stubby limbs.",
        f"VOICE: {PERSONA}",
        "THE CYCLE IS THE STORY: slide 1 opens on Amp DRAINED by a real, "
        "specific, relatable drain event (write the drain, don't explain it), "
        "the middle slides genuinely turn it around, and the last slide lands "
        "him fully charged with the payoff. The `beats` you return must follow "
        "that arc, drained → charging → charged.",
        f"The final beat ends on '{SIGN_OFF}'. The recovery/hydration payoff is "
        "what recharged him — never an ad read.",
        "`angle` must name the DRAIN EVENT and the TURNAROUND, so the build has "
        "a story to write rather than a slogan.",
    ])


def develop_concept(
    store: Store,
    settings: Settings,
    concept: dict,
    *,
    seed: str = "",
    generator=None,
) -> dict:
    """Drill ONE sketched concept down into a ready-to-build brief.

    This is where the expensive work now lives: the creative model, the full
    evidence base (brand bible, playbook, this account's real results) and the
    live signal — spent on the single concept the editor chose instead of on
    five they mostly won't read.

    Never raises: on failure it returns the original sketch untouched plus the
    error, so the card keeps whatever it already had and stays buildable."""
    base = Concept.model_validate(
        {k: v for k, v in (concept or {}).items() if k in Concept.model_fields}
    )
    try:
        if generator is None:
            from .pipeline import OpenAIChatClient

            generator = OpenAIChatClient(settings)  # creative model, high temp
        from datetime import date

        chosen = "\n".join(
            f"{label}: {value}" for label, value in (
                ("FLAVOUR", base.flavour), ("TITLE", base.title),
                ("HOOK", base.hook), ("SOURCE", base.source),
                ("ANGLE SO FAR", base.angle),
            ) if value.strip()
        )
        tail = (
            f"TODAY IS {date.today():%A, %-d %B %Y}.\n\n"
            "THE CONCEPT THE EDITOR CHOSE — develop THIS one:\n" + chosen
            + "\n\nSharpen it, then return the JSON object."
        )
        user = "\n\n".join(
            block for block in (
                _fuel(store, seed),
                _evidence(store),
                _steering(store),
                _amp_brief() if base.amp else "",
                tail,
            ) if block
        )
        result = generator.complete(DEVELOP_SYSTEM, user)
        out = _parse_concept(result.content)
        # Never lose what the sketch already had if the drill came back thin.
        merged = Concept(
            flavour=out.flavour or base.flavour,
            title=out.title or base.title,
            hook=out.hook or base.hook,
            why=out.why or base.why,
            angle=out.angle or base.angle,
            source=out.source or base.source,
            beats=out.beats or base.beats,
            amp=base.amp,  # the editor's card decides this, never the model
        )
        return {"concept": merged.as_payload(), "stage": "developed"}
    except Exception as exc:  # noqa: BLE001 — the drill is a bonus, never a blocker
        logging.getLogger(__name__).warning("concept drill-down failed: %s", exc)
        return {"concept": base.as_payload(), "stage": "sketch", "error": str(exc)}


# --- today's set, cached -------------------------------------------------------


def cached_concepts(
    store: Store, *, max_age_hours: float = 8.0, show: str = ""
) -> dict | None:
    """The last completed unseeded concept set, if it's still fresh.

    Reopening /create used to kick a brand-new engine run and stare at
    skeletons for the length of an LLM call, every single time. The set barely
    changes within a session, so the screen now paints from this instantly and
    only spends when it's stale or the editor asks for a fresh set."""
    from datetime import datetime

    from .shows import job_show_filter

    # Per show: a cached AMP set must never paint onto STRAIGHT UP.
    clause, params = job_show_filter(show)
    row = store.conn.execute(
        "SELECT job_id, result_json, updated_at FROM jobs "
        "WHERE kind = 'concepts' AND status = 'COMPLETED' "
        "AND COALESCE(params_json,'{}') LIKE '%\"seed\": \"\"%' "
        + clause +
        "ORDER BY job_id DESC LIMIT 1",
        params,
    ).fetchone()
    if not row:
        return None
    try:
        data = json.loads(row["result_json"] or "{}")
    except json.JSONDecodeError:
        return None
    if not data.get("concepts"):
        return None
    age_hours = None
    try:
        age = datetime.now().astimezone() - datetime.fromisoformat(row["updated_at"])
        age_hours = round(age.total_seconds() / 3600, 2)
    except (TypeError, ValueError):
        pass
    if age_hours is not None and age_hours > max_age_hours:
        return None
    return {**data, "job_id": row["job_id"], "age_hours": age_hours, "cached": True}
