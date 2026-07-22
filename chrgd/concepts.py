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

Concept/text only — it renders nothing and never touches the image budget. Runs
on the creative model, because the leap is the value. The generator is injectable
so tests run offline with no key.
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


class ConceptError(RuntimeError):
    pass


class Concept(BaseModel):
    flavour: str = ""      # topical | stat | hot_take | observation (or free-form)
    title: str = ""        # the concept in one line — the creative leap itself
    hook: str = ""         # the slide-1 opener a scroller sees first
    why: str = ""          # one line: why this will actually perform
    angle: str = ""        # what to build — the seed the carousel is written from
    source: str = ""       # the real signal/seed it sprang from (transparency)

    def build_seed(self) -> str:
        """The text handed to the build (create/start, mode=idea)."""
        base = (self.angle or self.title).strip()
        if self.hook.strip():
            base += f" — open with: {self.hook.strip()}"
        return base


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


def _fuel(store: Store, seed: str) -> str:
    """The mixed live pool the leap is made from: real scouted signal + the UK
    calendar + whatever the editor just fed in. Grounds the topical concepts so
    the engine invents angles, not news."""
    from .todayspick import gather_candidates
    from .uk import uk_calendar_seed

    lines: list[str] = []
    cands = gather_candidates(store, limit=14)
    if cands:
        lines.append(
            "LIVE SIGNAL scouted for the UK right now (REAL — use as fuel for "
            "topical concepts; do not invent news beyond this):"
        )
        for c in cands:
            lines.append(f"- ({c.lane}) {c.title}" + (f" — {c.why}" if c.why else ""))
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
            "concepts": [
                {**c.model_dump(), "build_seed": c.build_seed()} for c in concepts
            ],
            "seeded": bool(seed.strip()),
        }
    except Exception as exc:  # noqa: BLE001 — best-effort feature, never a blocker
        # Catch broadly (LLM SDK errors, parse errors, anything) so the screen
        # always gets a clean, readable reason instead of a 500. Logged so the
        # real cause is visible server-side, and returned so the operator (this
        # is a single-user internal tool) can see it too.
        logging.getLogger(__name__).warning("concept engine failed: %s", exc)
        return {"concepts": [], "seeded": bool(seed.strip()), "error": str(exc)}
