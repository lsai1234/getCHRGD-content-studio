"""The slide-1 concept gate — an independent quality check on the PLAN for
slide 1, run BEFORE a penny is spent generating the image.

Slide 1 is the whole first-pool verdict on TikTok, and it's now generated once,
at high quality. So instead of rolling several images and picking the best after
paying for all of them, we move the quality control upstream of the spend: the
slide-1 *concept* (its on-image headline + the visual brief the image will be
built from) has to earn the render. That whole loop is text-only and cheap; the
single expensive image is generated only once the concept has genuinely earned
it.

Sharpening one opener over and over is not enough, though. A refine loop only
ever improves the concept it was handed — it makes the same idea louder, it does
not make it *cleverer*, because it never leaves the neighbourhood of the first
thing the build happened to write. So the gate runs four stages:

  A. RIVALS      — invent N genuinely different openers for the same post, each
                   forced onto a different curiosity mechanic, each having to
                   name the exact question it plants. The built slide 1 enters
                   as the incumbent and has to survive.
  B. TOURNAMENT  — judge the whole field head-to-head on intrigue, originality
                   and instant legibility, and pick the one to spend on. This is
                   where "a hundred other gym posts could open with this" gets
                   caught: in isolation a derivative opener scores fine, next to
                   three sharper rivals it loses.
  C. SCORE       — the winner faces the brutal solo judge, and below the bar is
                   sharpened against its named weakness and re-judged (bounded
                   rounds). Unchanged in spirit, sharper in rubric.
  D. GLANCE      — the "prove it" step. The judge is shown ONLY what a stranger
                   actually perceives in the fraction of a second before the
                   thumb decides: the headline and the shape of the image, with
                   none of the reasoning. If the idea doesn't land at that speed
                   it doesn't land at all, and it gets one targeted fix.

Where this sits among the checks:
  * the build QA gate self-scores the whole post inside the write call;
  * THIS gate independently develops and judges the slide-1 concept BEFORE the
    image spend;
  * the cold scroll test (scrolltest.py) judges the RENDERED pixels afterwards.

Both the judge and the creative client are injectable so tests run offline with
no key. Every stage is individually config-gated and none of them can block a
render: on any LLM or parse failure the gate logs and lets the render proceed
with the best concept it has.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel, ValidationError, field_validator

from .config import Settings
from .db import Store
from .models import Idea


class ConceptGateError(RuntimeError):
    pass


class ConceptVerdict(BaseModel):
    """The judge's read on the slide-1 concept (pre-render)."""

    score: int = 0          # 0-10, brutal score of the slide-1 concept
    # The three things that actually make a first slide work, scored separately
    # so a sharpen has a specific lever instead of "make it better".
    intrigue: int = 0       # 0-10, does it plant a question you need answered
    originality: int = 0    # 0-10, or could a hundred other gym posts open here
    instant: int = 0        # 0-10, does it land in under a second
    weakness: str = ""      # the ONE biggest thing stopping it being a 10
    fix_direction: str = ""  # the concrete instruction that feeds the refine
    note: str = ""          # one honest line of reasoning


class ConceptCandidate(BaseModel):
    """One rival opener for the same post — a whole slide-1 concept."""

    headline: str = ""
    supporting: str = ""
    image_prompt: str = ""
    visual_intent: str = ""
    angle: str = ""            # the curiosity mechanic it's built on
    question: str = ""         # the exact question it plants in the viewer's head
    why_not_obvious: str = ""  # what stops this being the obvious opener
    origin: str = "invented"   # "built" for the incumbent from the write call


class CandidateScore(BaseModel):
    index: int = 0
    intrigue: int = 0
    originality: int = 0
    instant: int = 0
    verdict: str = ""

    @property
    def total(self) -> int:
        return self.intrigue + self.originality + self.instant


class TournamentVerdict(BaseModel):
    """The head-to-head read on the whole field of openers."""

    winner: int = 0
    why: str = ""              # why this one, in one line
    ranking: list[CandidateScore] = []


class GlanceVerdict(BaseModel):
    """What actually registers in the fraction of a second before the thumb decides."""

    took_away: str = ""   # the ONE thing they came away with
    question: str = ""    # the question it left them with (empty = it left none)
    stops: bool = False   # did that make them stop
    fix: str = ""         # what to change so it lands at that speed

    @field_validator("stops", mode="before")
    @classmethod
    def _coerce(cls, v):
        if isinstance(v, str):
            return v.strip().lower() in {"true", "yes", "stop", "1"}
        return bool(v)


@dataclass
class GateResult:
    idea: Idea
    passed: bool = False
    skipped: bool = False       # gate off / nothing to judge / judge unavailable
    refined: bool = False       # slide 1 was rewritten at least once
    rounds: int = 0
    score: int = 0
    min_score: int = 0          # the bar it had to clear
    verdict: ConceptVerdict | None = None
    # Stage A/B — the field of rival openers and how the winner was chosen.
    candidates: list[ConceptCandidate] = field(default_factory=list)
    tournament: TournamentVerdict | None = None
    swapped: bool = False       # a rival beat the opener the build wrote
    # Stage D — the fraction-of-a-second test on the winner.
    glance: GlanceVerdict | None = None
    glance_fixed: bool = False
    spend_usd: float = 0.0
    error: str | None = None


# --- Stage A: rival openers --------------------------------------------------

# The divergence axes. Handing the model a menu of mechanics and demanding a
# different one per candidate is what stops "invent 4 openers" collapsing into
# four rewordings of the same sentence — which is exactly what it does when
# asked cold.
ANGLE_MENU = """\
- THE CONTRADICTION — two things the viewer believes that can't both be true.
- THE ACCUSATION — name the exact person in the room doing the exact thing.
- THE WRONG NUMBER — a number that is not the number they expected, no context.
- MID-SCENE — drop them into the middle of something already going wrong.
- THE CONFESSION — admit the thing nobody in this audience admits out loud.
- THE RECEIPT — show the artefact (a screenshot, a label, a note, a till receipt)
  and let it do the talking with almost no words.
- THE WRONG RANKING — a list whose top entry is obviously, arguably wrong.
- THE FALSE FRIEND — state the popular belief as flat fact, so the swipe is the
  correction they now need.
- THE MISSING PIECE — show the aftermath and withhold the cause.
- THE DARE — a claim so specific they want to prove you wrong in the comments."""


RIVALS_CONTRACT = """
---

## Output contract (STRICT — this run only)

You are inventing RIVAL OPENERS for an existing carousel: several genuinely
different ways to open the SAME post. You are not rewording one hook — you are
finding the cleverest possible way in, from scratch, for each one.

Hard rules:
1. Every candidate must use a DIFFERENT mechanic from the menu you are given. If
   two candidates could be described by the same mechanic, one of them is wasted.
2. Every candidate must name, in `question`, the EXACT question it plants in a
   stranger's head. If you cannot write that question as a real sentence, the
   candidate is not finished — replace it.
3. Every candidate must pass the hundred-posts test: if a hundred other UK gym
   accounts could plausibly open a post this way, it is dead on arrival. Say in
   `why_not_obvious` what specifically stops it being the obvious opener. "It's
   punchy" is not an answer; a specific detail, an unexpected framing or a
   genuinely surprising claim is.
4. The opener must still set up the post that follows — it must lead naturally
   into the next slide you are shown, and it must not promise something the post
   does not pay off.
5. It must land in under a second, small and fast, on a phone. Clever but slow
   loses to clever and instant, every time. Max ~10 words on the image.
6. The visual brief must read as NATIVE content — a candid phone photo, a
   screenshot, a meme, a photo-dump frame — never a polished brand advert or
   generic AI art. Stay claim-safe and in the brand voice.

Return a SINGLE JSON object, nothing else:
{
  "candidates": [
    {
      "headline": "the on-image headline — max ~10 words, concrete, thumb-stopping",
      "supporting": "the supporting line (may be an empty string)",
      "image_prompt": "the full native-first visual brief for slide 1",
      "visual_intent": "subject, setting, action, prop, one double-take detail",
      "angle": "which mechanic from the menu this is",
      "question": "the exact question this plants in the viewer's head",
      "why_not_obvious": "what specifically stops a hundred other gym posts opening this way"
    }
  ]
}
"""


def _slide_text(slide: dict, key: str) -> str:
    return (slide.get(key) or "").strip()


def _rivals_payload(idea: Idea, slides: list, count: int) -> str:
    slide0 = slides[0] if slides else {}
    lines = [
        f"Invent {count} rival openers for this carousel — {count} different, "
        "genuinely clever ways into the same post.",
        "",
        "MECHANIC MENU (use a different one for each candidate):",
        ANGLE_MENU,
        "",
        f"THE POST IS ABOUT: {idea.concept_note.strip() or _slide_text(slide0, 'headline')}",
        f"THE OPENER THE BUILD WROTE (beat it): {_slide_text(slide0, 'headline')}",
    ]
    if _slide_text(slide0, "image_prompt"):
        lines.append(f"ITS VISUAL BRIEF: {_slide_text(slide0, 'image_prompt')}")
    if len(slides) > 1:
        nxt = _slide_text(slides[1], "headline")
        if nxt:
            lines.append(
                f"WHAT THE VIEWER SEES NEXT (your opener must set this up): {nxt}"
            )
    if idea.target_viewer.strip():
        lines.append(f"AIMED AT: {idea.target_viewer.strip()}")
    lines += [
        "",
        f"Return the JSON with exactly {count} candidates. Different mechanics, "
        "each one naming the question it plants.",
    ]
    return "\n".join(lines)


def invent_rivals(
    idea: Idea,
    slides: list,
    settings: Settings,
    *,
    count: int,
    client=None,
) -> tuple[list[ConceptCandidate], float]:
    """Stage A — several genuinely different openers for the same post."""
    from .pipeline import OpenAIChatClient, engine_base, estimate_cost

    if client is None:
        client = OpenAIChatClient(settings)
    system = engine_base() + "\n" + RIVALS_CONTRACT
    result = client.complete(system, _rivals_payload(idea, slides, count))
    spend = estimate_cost(
        settings.openai_model, result.prompt_tokens, result.completion_tokens
    )
    try:
        data = json.loads(result.content)
    except json.JSONDecodeError as exc:
        raise ConceptGateError(f"could not parse rival openers: {exc}") from exc
    raw = data.get("candidates")
    if not isinstance(raw, list):
        raise ConceptGateError("rival openers came back without a candidates list")

    out: list[ConceptCandidate] = []
    for item in raw[:count]:
        if not isinstance(item, dict):
            continue
        try:
            cand = ConceptCandidate.model_validate(item)
        except ValidationError:
            continue
        if cand.headline.strip():
            out.append(cand)
    return out, spend


def _incumbent(idea: Idea, slide0: dict) -> ConceptCandidate:
    """The opener the build wrote, entered into the tournament as candidate 0."""
    return ConceptCandidate(
        headline=_slide_text(slide0, "headline") or idea.hook.strip(),
        supporting=_slide_text(slide0, "supporting"),
        image_prompt=_slide_text(slide0, "image_prompt"),
        visual_intent=_slide_text(slide0, "visual_intent"),
        angle="the opener the build wrote",
        origin="built",
    )


# --- Stage B: the tournament -------------------------------------------------


TOURNAMENT_SYSTEM = """You are picking which ONE opener a UK gym/supplement TikTok brand should spend its single high-quality image on.

You are shown several rival slide-1 concepts for the SAME post — the one the build wrote, plus alternatives. A photo carousel's slide 1 is the thumbnail AND the entire scroll-stopper: a stranger on the For You feed gives it a fraction of a second before the thumb moves. Your job is to choose the one that wins that fraction of a second, and to be honest about why the others lose.

Score each candidate out of 10 on three things, separately:
- INTRIGUE — does it plant a specific question the viewer now needs answered? Not "is it interesting" — can you write the actual question? If you can't, it's under 5.
- ORIGINALITY — could a hundred other UK gym accounts open a post this way? If yes, it's under 4, no matter how competent it is. Reward a genuinely unexpected framing, a specific detail nobody else would have, a claim that makes you go "wait, what". Punish the familiar shapes: "X mistakes", "nobody talks about", "this changed everything", generic motivation, anything that reads like a content farm.
- INSTANT — does it land small and fast on a phone, in under a second, with no reading? Clever but slow loses to clever and instant. Deduct hard for anything you had to parse twice, and for a visual brief that would look like an advert or obvious AI art rather than native content.

Then pick the winner. The winner is the one you would genuinely bet real money on stopping a cold, cynical thumb — not the safest one and not the loudest one. A candidate that is merely competent must never beat one that is genuinely clever AND instantly legible. If the opener the build wrote is honestly the best of the field, say so and pick it — do not swap for the sake of swapping. Also check the winner actually sets up the post that follows; an opener that promises something the post can't pay off is a bad bet however clever it is.

Return a SINGLE JSON object, no markdown, no commentary:
{
  "winner": integer index of the candidate you'd spend on,
  "why": "one line: what this one does that the others don't",
  "ranking": [
    {"index": integer, "intrigue": 0-10, "originality": 0-10, "instant": 0-10,
     "verdict": "one honest line — for the losers, the specific reason it lost"}
  ]
}
Score every candidate. Calibrate low and do not be kind — a soft call here costs the brand its whole first pool."""


def _tournament_payload(idea: Idea, candidates: list[ConceptCandidate], slides: list) -> str:
    parts = [
        "Pick the opener to spend the image budget on.",
        "",
        f"THE POST IS ABOUT: {idea.concept_note.strip()}",
    ]
    if len(slides) > 1 and _slide_text(slides[1], "headline"):
        parts.append(
            "WHAT SLIDE 2 SAYS (the winner must set this up): "
            f"{_slide_text(slides[1], 'headline')}"
        )
    if idea.target_viewer.strip():
        parts.append(f"AIMED AT: {idea.target_viewer.strip()}")
    parts.append("")
    for i, c in enumerate(candidates):
        block = [f"--- CANDIDATE {i}" + (" (the opener the build wrote)" if c.origin == "built" else "")]
        block.append(f"HEADLINE: {c.headline.strip()}")
        if c.supporting.strip():
            block.append(f"SUPPORTING: {c.supporting.strip()}")
        if c.image_prompt.strip():
            block.append(f"VISUAL BRIEF: {c.image_prompt.strip()}")
        if c.angle.strip():
            block.append(f"MECHANIC IT CLAIMS: {c.angle.strip()}")
        if c.question.strip():
            block.append(f"QUESTION IT CLAIMS TO PLANT: {c.question.strip()}")
        if c.why_not_obvious.strip():
            block.append(f"WHY IT CLAIMS NOT TO BE OBVIOUS: {c.why_not_obvious.strip()}")
        parts.append("\n".join(block))
    parts += [
        "",
        "A candidate's own claims about itself are a pitch, not evidence — judge "
        "the opener, not the pitch. Score all of them and name the winner.",
    ]
    return "\n\n".join(parts)


def parse_tournament_verdict(text: str) -> TournamentVerdict:
    payload = _extract_json(text, "tournament verdict")
    try:
        v = TournamentVerdict.model_validate(payload)
    except ValidationError as exc:
        raise ConceptGateError(f"could not parse tournament verdict: {exc}") from exc
    for r in v.ranking:
        r.intrigue = max(0, min(10, int(r.intrigue)))
        r.originality = max(0, min(10, int(r.originality)))
        r.instant = max(0, min(10, int(r.instant)))
    return v


def _resolve_winner(verdict: TournamentVerdict, count: int) -> int:
    """The judge's pick, or — if it named an index that doesn't exist — the
    highest-scoring candidate it actually ranked. A malformed index must not
    silently hand the render to candidate 0."""
    if 0 <= verdict.winner < count:
        return verdict.winner
    ranked = [r for r in verdict.ranking if 0 <= r.index < count]
    if ranked:
        return max(ranked, key=lambda r: r.total).index
    return 0


# --- Stage C: the solo score -------------------------------------------------


JUDGE_SYSTEM = """You are the toughest slide-1 concept judge on the internet, grading photo-carousel openers for a UK gym/supplement TikTok brand.

Slide 1 of a photo carousel is the ENTIRE first-pool verdict: it is the thumbnail AND the scroll-stopper for a stranger on the For You feed. You are shown the PLAN for slide 1 — its on-image headline, its supporting line, and the visual brief the image will be generated from — and you score how close it is to a perfect 10/10 scroll-stopper for a hard-to-impress UK 18-30 gym-tok viewer. You are judging the concept, not finished pixels.

A 10/10 slide-1 concept:
- lands one of these in under 2 seconds — a genuine curiosity gap, a bold claim, or instant self-recognition ("that's me" / "that's my mate");
- is genuinely CLEVER, not merely loud: it comes at the subject from an angle the viewer hasn't already been served fifty times this week;
- has a headline that is concrete and specific (a named situation, an exact number or time), never a vague blog title or generic motivation;
- has a visual brief that reads as NATIVE content — a candid phone photo, a screenshot, a meme, a photo-dump frame — NOT a polished brand advert or generic AI art;
- makes the words and the image work together to stop the thumb and point to one clear later action (swipe / rank / confess / tag / argue / save).

Score BRUTALLY and calibrate low. Most concepts are a 6 or 7 — fine, but not thumb-stopping. Reserve 9 and 10 for concepts you would genuinely bet on stopping a cold, cynical viewer mid-scroll. Deduct hard for: a flat or statement headline with no tension, a vague "content-farm" hook, a visual brief that would look like an ad or obvious AI art, a muddy or generic image, "so what" content, or anything a hundred other gym posts could open with. If you cannot write the exact question the hook plants in the viewer's head, it is not a 9. An opener that is competent, loud and completely familiar is capped at 6 however well executed it is — familiarity is the thing that kills reach, and it is the single most common failure here.

Return a SINGLE JSON object, no markdown, no commentary:
{
  "score": integer 0-10 (honest, brutal — your overall bet on this concept),
  "intrigue": integer 0-10 (does it plant a specific question the viewer needs answered),
  "originality": integer 0-10 (or could a hundred other gym posts open this way),
  "instant": integer 0-10 (does it land small and fast, in under a second, no re-reading),
  "weakness": "the ONE biggest thing stopping this being a 10 (empty string ONLY if it truly is a 10)",
  "fix_direction": "one concrete instruction to fix that weakness — exactly what to change in the headline and/or the visual brief",
  "note": "one honest sentence explaining the score"
}
Never inflate a score to be kind — a soft 9 on a real 6 costs the brand a dead post."""


REFINE_CONTRACT = """
---

## Output contract (STRICT — this run only)

You are sharpening ONLY slide 1 of an existing carousel — its thumbnail and scroll-stopper — using a tough judge's verdict. Keep the post's core idea, topic and the rest of the slides untouched; your job is to make slide 1 a 10/10 scroll-stopper by fixing the exact weakness the judge named. Rewrite its on-image headline, its supporting line, and its visual brief. The visual brief MUST read as native TikTok content (candid phone photo / screenshot / meme / photo-dump), never a polished brand ad or generic AI art. Stay claim-safe and in the brand voice.

Do not fix a weakness by making the opener louder — louder is not cleverer, and a familiar shape shouted is still a familiar shape. If the judge said it was obvious or derivative, change the ANGLE, not the volume.

Return a SINGLE JSON object, nothing else:
{
  "headline": "the slide-1 on-image headline — max ~10 words, concrete, thumb-stopping",
  "supporting": "the slide-1 supporting line (may be an empty string)",
  "image_prompt": "the full native-first visual brief for slide 1",
  "visual_intent": "subject, setting, action, prop, one double-take detail"
}
"""


class SlideOneRewrite(BaseModel):
    headline: str = ""
    supporting: str = ""
    image_prompt: str = ""
    visual_intent: str = ""


# --- Stage D: the fraction-of-a-second test ----------------------------------


GLANCE_SYSTEM = """You are a UK gym-tok viewer, 18-30, scrolling fast. You are NOT reading — you are glancing. Your thumb is already moving.

You are shown ONLY what actually reaches your eyes in the fraction of a second before you decide: the words that will be on the image, and the shape of the picture behind them. You get no explanation of the idea, no reasoning, no context — because a stranger in the feed gets none either. Whatever you can't take from that glance, the post does not have.

Answer honestly, as that viewer:
- what is the ONE thing you came away with? (if it's mush, say so — "something about the gym" is a real and damning answer)
- what question, if any, did it leave in your head? Write it as the actual sentence. If it left none, leave it empty.
- did you stop? Your default is no. You stop only if that glance genuinely made you need something you don't have yet.

A concept can be clever and still fail here: too many words, an idea that needs a second read, a picture whose subject isn't obvious small, a joke whose setup arrives too late. That is exactly what you are catching. Be harsh — this is the last check before real money is spent on the image.

Return a SINGLE JSON object, no markdown, no commentary:
{
  "took_away": "the ONE thing you came away with, in your own words",
  "question": "the question it left you with, as a real sentence (empty string if none)",
  "stops": true or false,
  "fix": "if you didn't stop: the one change that would make it land at glance speed (empty string if you stopped)"
}"""


def _glance_payload(slide0: dict) -> str:
    """Deliberately thin: the headline, and the shape of the image. Nothing else
    — no topic, no intent, no reasoning. That asymmetry IS the test."""
    parts = [
        "You glance at this in the feed. Half a second, thumb moving.",
        "",
        f"WORDS ON THE IMAGE: {_slide_text(slide0, 'headline')}",
    ]
    if _slide_text(slide0, "supporting"):
        parts.append(f"SMALLER LINE UNDER IT: {_slide_text(slide0, 'supporting')}")
    shape = _slide_text(slide0, "visual_intent") or _slide_text(slide0, "image_prompt")
    if shape:
        parts.append(f"WHAT THE PICTURE IS: {shape}")
    parts += ["", "What did you take away, and did you stop?"]
    return "\n".join(parts)


def parse_glance_verdict(text: str) -> GlanceVerdict:
    payload = _extract_json(text, "glance verdict")
    try:
        return GlanceVerdict.model_validate(payload)
    except ValidationError as exc:
        raise ConceptGateError(f"could not parse glance verdict: {exc}") from exc


# --- the LLM clients ---------------------------------------------------------


class ConceptJudge(Protocol):
    def judge(self, system: str, user: str) -> str:
        """Run a JSON completion for the concept verdict, return the text."""
        ...


class OpenAIConceptJudge:
    """Text-only concept verdict via the cheap judge model (steady, low temp)."""

    def __init__(self, settings: Settings):
        if not settings.openai_api_key:
            raise ConceptGateError("OPENAI_API_KEY is not set — add it to your .env")
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise ConceptGateError(
                "openai not installed. Run: pip install -e '.[llm]'"
            ) from exc
        self._client = OpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.get_openai_base_url(),
        )
        # The same cheap rubric model the scroll test uses — a judge should be
        # steady, not creative, and this is not shipped copy.
        self._model = settings.judge_model

    def judge(self, system: str, user: str) -> str:
        from .pipeline import _describe_llm_error, chat_json_create

        try:
            resp = chat_json_create(
                self._client, model=self._model, system=system, user=user,
                temperature=0.2,
            )
        except Exception as exc:  # noqa: BLE001
            raise ConceptGateError(_describe_llm_error(exc)) from exc
        return resp.choices[0].message.content or ""


# --- helpers ----------------------------------------------------------------


def _extract_json(text: str, what: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{") :]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ConceptGateError(f"no JSON object found in {what}")
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ConceptGateError(f"could not parse {what}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConceptGateError(f"{what} was not a JSON object")
    return payload


def _design_system(idea: Idea) -> dict:
    if not idea.route_json:
        return {}
    try:
        return json.loads(idea.route_json).get("design_system", {}) or {}
    except json.JSONDecodeError:
        return {}


def _concept_payload(idea: Idea, slide0: dict) -> str:
    """The slide-1 plan handed to the judge."""
    headline = (slide0.get("headline") or idea.hook or "").strip()
    parts = [
        "Judge this slide-1 concept for a UK gym/supplement photo carousel.",
        "",
        f"ON-IMAGE HEADLINE (the first line a scroller sees): {headline}",
    ]
    if slide0.get("supporting", "").strip():
        parts.append(f"SUPPORTING LINE: {slide0['supporting'].strip()}")
    if slide0.get("image_prompt", "").strip():
        parts.append(f"VISUAL BRIEF (what the image will be): {slide0['image_prompt'].strip()}")
    if slide0.get("visual_intent", "").strip():
        parts.append(f"VISUAL INTENT: {slide0['visual_intent'].strip()}")
    ds = _design_system(idea)
    if ds.get("palette") or ds.get("motif"):
        parts.append(
            "SHARED LOOK (context): "
            + " · ".join(x for x in (ds.get("palette"), ds.get("motif")) if x)
        )
    if idea.target_viewer.strip():
        parts.append(f"AIMED AT: {idea.target_viewer.strip()}")
    parts.append(
        "\nScore it 0-10 and return the JSON verdict. Calibrate low; reserve 9-10 "
        "for a concept you'd bet on stopping a cold thumb."
    )
    return "\n".join(parts)


def parse_concept_verdict(text: str) -> ConceptVerdict:
    payload = _extract_json(text, "concept verdict")
    try:
        v = ConceptVerdict.model_validate(payload)
    except ValidationError as exc:
        raise ConceptGateError(f"could not parse concept verdict: {exc}") from exc
    v.score = max(0, min(10, int(v.score)))
    v.intrigue = max(0, min(10, int(v.intrigue)))
    v.originality = max(0, min(10, int(v.originality)))
    v.instant = max(0, min(10, int(v.instant)))
    return v


def refine_slide_one(
    idea: Idea,
    slide0: dict,
    verdict: ConceptVerdict,
    settings: Settings,
    *,
    client=None,
) -> tuple[SlideOneRewrite, float]:
    """One creative pass that sharpens slide 1 against the judge's weakness."""
    from .pipeline import (
        OpenAIChatClient,
        engine_base,
        estimate_cost,
    )

    if client is None:
        client = OpenAIChatClient(settings)
    system = engine_base() + "\n" + REFINE_CONTRACT

    lines = [
        "Sharpen slide 1 of this carousel to a 10/10 scroll-stopper.",
        "",
        f"CURRENT HEADLINE: {(slide0.get('headline') or idea.hook or '').strip()}",
    ]
    if slide0.get("supporting", "").strip():
        lines.append(f"CURRENT SUPPORTING: {slide0['supporting'].strip()}")
    if slide0.get("image_prompt", "").strip():
        lines.append(f"CURRENT VISUAL BRIEF: {slide0['image_prompt'].strip()}")
    if idea.concept_note.strip():
        lines.append(f"POST TOPIC (keep it): {idea.concept_note.strip()}")
    lines += [
        "",
        f"THE JUDGE SCORED IT {verdict.score}/10.",
        f"BIGGEST WEAKNESS: {verdict.weakness}" if verdict.weakness else "",
        f"FIX DIRECTION (apply this): {verdict.fix_direction}"
        if verdict.fix_direction else "",
        "",
        "Return the sharpened slide-1 JSON.",
    ]
    result = client.complete(system, "\n".join(x for x in lines if x))
    spend = estimate_cost(
        settings.openai_model, result.prompt_tokens, result.completion_tokens
    )
    rewrite = SlideOneRewrite.model_validate(json.loads(result.content))
    return rewrite, spend


def _apply_rewrite(idea: Idea, slides: list, rewrite: SlideOneRewrite) -> None:
    """Fold a slide-1 rewrite into the slides list + the post hook, in place."""
    slide0 = slides[0]
    if rewrite.headline.strip():
        slide0["headline"] = rewrite.headline.strip()
        # The hook is the slide-1 scroll-stopper; keep it aligned with the
        # on-image headline (the UI's sharpen action does the same).
        idea.hook = rewrite.headline.strip()
    # supporting may legitimately become empty
    slide0["supporting"] = rewrite.supporting.strip()
    if rewrite.image_prompt.strip():
        slide0["image_prompt"] = rewrite.image_prompt.strip()
    if rewrite.visual_intent.strip():
        slide0["visual_intent"] = rewrite.visual_intent.strip()


def _apply_candidate(idea: Idea, slides: list, cand: ConceptCandidate) -> None:
    """Install a tournament winner as slide 1."""
    _apply_rewrite(
        idea,
        slides,
        SlideOneRewrite(
            headline=cand.headline,
            supporting=cand.supporting,
            image_prompt=cand.image_prompt,
            visual_intent=cand.visual_intent,
        ),
    )


# --- the gate ----------------------------------------------------------------


def _run_tournament(
    idea: Idea,
    slides: list,
    settings: Settings,
    judge: ConceptJudge,
    refiner,
    result: GateResult,
    note,
) -> None:
    """Stages A + B — invent rival openers and pick the one worth spending on.

    Contained: any failure here leaves the built slide 1 in place and the solo
    score still runs. A field we couldn't invent is not a reason to skip the
    check we can still do.
    """
    count = settings.concept_candidates
    if count < 1:
        return
    note(f"inventing {count} rival openers before committing to one")
    rivals, spend = invent_rivals(idea, slides, settings, count=count, client=refiner)
    result.spend_usd += spend
    if not rivals:
        return

    field_ = [_incumbent(idea, slides[0]), *rivals]
    result.candidates = field_
    note(f"judging {len(field_)} openers head-to-head on intrigue, originality and speed")
    verdict = parse_tournament_verdict(
        judge.judge(TOURNAMENT_SYSTEM, _tournament_payload(idea, field_, slides))
    )
    winner = _resolve_winner(verdict, len(field_))
    verdict.winner = winner
    result.tournament = verdict
    if winner > 0:
        result.swapped = True
        _apply_candidate(idea, slides, field_[winner])
        note(f"a sharper opener won: {field_[winner].headline}")
    else:
        note("the opener the build wrote survived the field")


def _run_glance_test(
    idea: Idea,
    slides: list,
    settings: Settings,
    judge: ConceptJudge,
    refiner,
    result: GateResult,
    note,
) -> None:
    """Stage D — does it land in the fraction of a second, with no explanation?

    One bounded fix: if the glance fails, sharpen for legibility-at-speed and
    re-glance once. We keep the second verdict either way — an honest "still
    doesn't land" is worth more to the editor than hiding it.
    """
    note("glance test: what a stranger takes from it in half a second")
    glance = parse_glance_verdict(judge.judge(GLANCE_SYSTEM, _glance_payload(slides[0])))
    result.glance = glance
    if glance.stops or not glance.fix.strip():
        return

    note(f"it didn't land at glance speed ({glance.took_away}) — fixing and re-checking")
    rewrite, spend = refine_slide_one(
        idea,
        slides[0],
        ConceptVerdict(
            score=result.score,
            weakness="a stranger glancing at it for half a second took away "
                     f"only: {glance.took_away}",
            fix_direction=glance.fix,
        ),
        settings,
        client=refiner,
    )
    result.spend_usd += spend
    _apply_rewrite(idea, slides, rewrite)
    result.refined = True
    result.glance_fixed = True
    result.glance = parse_glance_verdict(
        judge.judge(GLANCE_SYSTEM, _glance_payload(slides[0]))
    )


def gate_slide_one(
    idea: Idea,
    settings: Settings,
    store: Store,
    *,
    judge: ConceptJudge | None = None,
    refiner=None,
    notify=None,
) -> GateResult:
    """Develop, judge and (if needed) sharpen the slide-1 concept before rendering.

    Returns a GateResult whose `.idea` is the (possibly updated) idea to render.
    Never raises for an LLM/parse failure — the gate is a quality booster, not a
    hard dependency, so on any judge/refiner error it logs and lets the render
    proceed with the best concept it has.
    """
    result = GateResult(idea=idea)
    if not settings.concept_gate_enabled:
        result.skipped = True
        return result
    if not idea.slides_json:
        result.skipped = True
        return result

    def _note(msg: str) -> None:
        if notify:
            notify(msg)

    try:
        slides = json.loads(idea.slides_json)
    except json.JSONDecodeError:
        result.skipped = True
        return result
    if not slides:
        result.skipped = True
        return result

    min_score = settings.concept_gate_min_score
    max_rounds = max(1, settings.concept_gate_max_rounds)
    result.min_score = min_score

    try:
        judge = judge or OpenAIConceptJudge(settings)

        # Stages A + B — find the cleverest way in before committing to one.
        # Isolated so a failed tournament still leaves the solo score to run.
        if settings.concept_tournament_enabled:
            try:
                _run_tournament(idea, slides, settings, judge, refiner, result, _note)
            except (ConceptGateError, ValidationError, json.JSONDecodeError) as exc:
                result.error = str(exc)
                _note(f"couldn't run the opener tournament ({exc}) — scoring the built one")

        # Stage C — the winner has to clear the bar on its own.
        verdict: ConceptVerdict | None = None
        for rnd in range(1, max_rounds + 1):
            result.rounds = rnd
            _note(
                f"checking the slide 1 concept (round {rnd}) before spending on "
                "the image"
            )
            verdict = parse_concept_verdict(
                judge.judge(JUDGE_SYSTEM, _concept_payload(idea, slides[0]))
            )
            result.verdict = verdict
            result.score = verdict.score
            if verdict.score >= min_score:
                result.passed = True
                break
            if rnd < max_rounds:
                _note(
                    f"slide 1 concept scored {verdict.score}/{min_score} — "
                    "sharpening it and re-checking"
                )
                rewrite, spend = refine_slide_one(
                    idea, slides[0], verdict, settings, client=refiner
                )
                result.spend_usd += spend
                _apply_rewrite(idea, slides, rewrite)
                result.refined = True

        # Stage D — prove it survives the half-second it will actually get.
        if settings.concept_glance_test:
            try:
                _run_glance_test(idea, slides, settings, judge, refiner, result, _note)
            except (ConceptGateError, ValidationError, json.JSONDecodeError) as exc:
                result.error = str(exc)
                _note(f"couldn't run the glance test ({exc}) — rendering as is")
    except (ConceptGateError, ValidationError, json.JSONDecodeError) as exc:
        # Don't block the paid render on a gate hiccup — proceed with what we have.
        result.error = str(exc)
        _note(f"concept gate skipped ({exc}) — rendering the current concept")

    # Persist whatever we ended with: the (possibly sharpened) slide 1 and the
    # verdict, so the render uses the improved concept and the UI/learning loop
    # can see how it scored.
    _persist(idea, store, slides, result)
    return result


def _persist(idea: Idea, store: Store, slides: list, result: GateResult) -> None:
    try:
        route = json.loads(idea.route_json) if idea.route_json else {}
    except json.JSONDecodeError:
        route = {}
    if result.verdict is not None or result.tournament is not None:
        cg: dict = {
            "min_score": result.min_score,
            "passed": result.passed,
            "rounds": result.rounds,
        }
        if result.verdict is not None:
            cg.update(
                score=result.verdict.score,
                intrigue=result.verdict.intrigue,
                originality=result.verdict.originality,
                instant=result.verdict.instant,
                weakness=result.verdict.weakness,
                note=result.verdict.note,
            )
        if result.tournament is not None:
            cg["tournament"] = {
                "candidates": len(result.candidates),
                "winner": result.tournament.winner,
                "swapped": result.swapped,
                "why": result.tournament.why,
                "beaten": [
                    {"headline": c.headline, "angle": c.angle, "verdict": _verdict_line(result, i)}
                    for i, c in enumerate(result.candidates)
                    if i != result.tournament.winner
                ],
            }
        if result.glance is not None:
            cg["glance"] = {
                "took_away": result.glance.took_away,
                "question": result.glance.question,
                "stops": result.glance.stops,
                "fixed": result.glance_fixed,
            }
        route["concept_gate"] = cg
    new_slides_json = json.dumps(slides)
    new_route_json = json.dumps(route)
    store.conn.execute(
        "UPDATE ideas SET slides_json = ?, hook = ?, route_json = ? WHERE idea_id = ?",
        (new_slides_json, idea.hook, new_route_json, idea.idea_id),
    )
    store.conn.commit()
    idea.slides_json = new_slides_json
    idea.route_json = new_route_json


def _verdict_line(result: GateResult, index: int) -> str:
    if result.tournament is None:
        return ""
    for r in result.tournament.ranking:
        if r.index == index:
            return r.verdict
    return ""
