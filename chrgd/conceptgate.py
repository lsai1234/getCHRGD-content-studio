"""The slide-1 concept gate — an independent quality check on the PLAN for
slide 1, run BEFORE a penny is spent generating the image.

Slide 1 is the whole first-pool verdict on TikTok, and it's now generated once,
at high quality. So instead of rolling several images and picking the best after
paying for all of them, we move the quality control upstream of the spend: an
independent judge scores the slide-1 *concept* (its on-image headline + the
visual brief the image will be built from). If it falls short of the bar, a
creative pass sharpens the concept using the judge's specific weakness, and it
is re-judged. That loop is text-only and cheap; the single expensive image is
generated only once the concept has genuinely earned it.

Where this sits among the three checks:
  * the build QA gate self-scores the whole post inside the write call;
  * THIS gate independently judges the slide-1 concept BEFORE the image spend;
  * the cold scroll test (scrolltest.py) judges the RENDERED pixels afterwards.

Both the judge and the refiner are injectable so tests run offline with no key.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, ValidationError

from .config import Settings
from .db import Store
from .models import Idea


class ConceptGateError(RuntimeError):
    pass


class ConceptVerdict(BaseModel):
    """The judge's read on the slide-1 concept (pre-render)."""

    score: int = 0          # 0-10, brutal score of the slide-1 concept
    weakness: str = ""      # the ONE biggest thing stopping it being a 10
    fix_direction: str = ""  # the concrete instruction that feeds the refine
    note: str = ""          # one honest line of reasoning


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
    spend_usd: float = 0.0
    error: str | None = None


JUDGE_SYSTEM = """You are the toughest slide-1 concept judge on the internet, grading photo-carousel openers for a UK gym/supplement TikTok brand.

Slide 1 of a photo carousel is the ENTIRE first-pool verdict: it is the thumbnail AND the scroll-stopper for a stranger on the For You feed. You are shown the PLAN for slide 1 — its on-image headline, its supporting line, and the visual brief the image will be generated from — and you score how close it is to a perfect 10/10 scroll-stopper for a hard-to-impress UK 18-30 gym-tok viewer. You are judging the concept, not finished pixels.

A 10/10 slide-1 concept:
- lands one of these in under 2 seconds — a genuine curiosity gap, a bold claim, or instant self-recognition ("that's me" / "that's my mate");
- has a headline that is concrete and specific (a named situation, an exact number or time), never a vague blog title or generic motivation;
- has a visual brief that reads as NATIVE content — a candid phone photo, a screenshot, a meme, a photo-dump frame — NOT a polished brand advert or generic AI art;
- makes the words and the image work together to stop the thumb and point to one clear later action (swipe / rank / confess / tag / argue / save).

Score BRUTALLY and calibrate low. Most concepts are a 6 or 7 — fine, but not thumb-stopping. Reserve 9 and 10 for concepts you would genuinely bet on stopping a cold, cynical viewer mid-scroll. Deduct hard for: a flat or statement headline with no tension, a vague "content-farm" hook, a visual brief that would look like an ad or obvious AI art, a muddy or generic image, "so what" content, or anything a hundred other gym posts could open with. If you cannot write the exact question the hook plants in the viewer's head, it is not a 9.

Return a SINGLE JSON object, no markdown, no commentary:
{
  "score": integer 0-10 (honest, brutal),
  "weakness": "the ONE biggest thing stopping this being a 10 (empty string ONLY if it truly is a 10)",
  "fix_direction": "one concrete instruction to fix that weakness — exactly what to change in the headline and/or the visual brief",
  "note": "one honest sentence explaining the score"
}
Never inflate a score to be kind — a soft 9 on a real 6 costs the brand a dead post."""


REFINE_CONTRACT = """
---

## Output contract (STRICT — this run only)

You are sharpening ONLY slide 1 of an existing carousel — its thumbnail and scroll-stopper — using a tough judge's verdict. Keep the post's core idea, topic and the rest of the slides untouched; your job is to make slide 1 a 10/10 scroll-stopper by fixing the exact weakness the judge named. Rewrite its on-image headline, its supporting line, and its visual brief. The visual brief MUST read as native TikTok content (candid phone photo / screenshot / meme / photo-dump), never a polished brand ad or generic AI art. Stay claim-safe and in the brand voice.

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
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{") :]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ConceptGateError("no JSON object found in concept verdict")
    try:
        v = ConceptVerdict.model_validate(json.loads(text[start : end + 1]))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ConceptGateError(f"could not parse concept verdict: {exc}") from exc
    v.score = max(0, min(10, int(v.score)))
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


def gate_slide_one(
    idea: Idea,
    settings: Settings,
    store: Store,
    *,
    judge: ConceptJudge | None = None,
    refiner=None,
    notify=None,
) -> GateResult:
    """Validate (and, if needed, sharpen) the slide-1 concept before rendering.

    Returns a GateResult whose `.idea` is the (possibly updated) idea to render.
    Never raises for an LLM/parse failure — the gate is a quality booster, not a
    hard dependency, so on any judge/refiner error it logs and lets the render
    proceed with the current concept.
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
    if result.verdict is not None:
        route["concept_gate"] = {
            "score": result.verdict.score,
            "min_score": result.min_score,
            "passed": result.passed,
            "rounds": result.rounds,
            "weakness": result.verdict.weakness,
            "note": result.verdict.note,
        }
    new_slides_json = json.dumps(slides)
    new_route_json = json.dumps(route)
    store.conn.execute(
        "UPDATE ideas SET slides_json = ?, hook = ?, route_json = ? WHERE idea_id = ?",
        (new_slides_json, idea.hook, new_route_json, idea.idea_id),
    )
    store.conn.commit()
    idea.slides_json = new_slides_json
    idea.route_json = new_route_json
