"""The cold scroll test — an honest, calibrated verdict on the FINISHED post.

Everything else in the pipeline reasons about a *plan*: the QA gate scores the
copy before an image exists, and the same model that wrote the post grades its
own homework. This is different. It looks at the actual rendered slide 1 — the
real pixels a viewer sees in-feed — and answers the only question that matters:
would a real UK 18-30 gym-tok user's thumb STOP, or keep scrolling?

It is deliberately adversarial. Real feed behaviour is to scroll, so the judge
defaults to scroll and a post has to genuinely earn a stop. It returns a
binary verdict, the single biggest thing killing it, and one concrete fix
(regenerate the image or sharpen the hook) — not a vanity score.

Calibration: the account's own hit/flop history (via `learning.performance_notes`)
is fed in, so the verdict measures "would *this* audience stop", grounded in
what has actually worked — and sharpens every time a real result is logged.

The vision model is injectable so tests run offline with no key or spend.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ValidationError

from .config import Settings
from .db import Store
from .models import Idea, Slide


class ScrollTestError(RuntimeError):
    pass


class ScrollVerdict(BaseModel):
    """The judge's read on the finished slide 1."""

    verdict: str = "scroll"          # "stop" | "scroll"
    confidence: int = 0              # 0-100: chance a target viewer stops
    killer: str = ""                 # the ONE biggest reason it'd be scrolled
    fix_kind: str = "none"           # "regenerate_image" | "sharpen_hook" | "none"
    fix: str = ""                    # the specific, actionable fix
    note: str = ""                   # one honest line of reasoning

    @property
    def stops(self) -> bool:
        return self.verdict.strip().lower() == "stop"


SYSTEM_PROMPT = """You are a real UK gym-tok viewer, 18-30, scrolling TikTok fast on your phone.
You are NOT a marketer, a brand or a cheerleader — you are a hard-to-impress
member of the exact audience this post is chasing, and your default is to keep
scrolling. Almost everything gets scrolled past; a post has to genuinely EARN a
stop in the first half-second.

You are shown the FIRST image of a photo carousel exactly as it appears in the
feed, plus the hook text. Judge only the thumb-stop moment: the image + the
first line, seen small and fast. Be brutally honest — flattery here costs the
editor a dead post.

A stop is earned by things like: a hook that opens a genuine curiosity gap,
tension or callout; instant self-recognition ("that's me"/"that's my mate"); a
striking or unexpected image; a promise of a fight, a confession or a payoff.
A scroll is caused by: a flat/statement hook with no tension, generic or
obviously-AI imagery with nothing to look at, too much text to read in-feed,
a muddy focal point, or "so what" content.

Return a SINGLE JSON object, no markdown, no commentary:
{
  "verdict": "stop" | "scroll",
  "confidence": integer 0-100 (your honest estimate of the % of THIS audience
    who would actually stop — most posts are well under 50),
  "killer": "the ONE biggest thing making people scroll (empty string if it stops)",
  "fix_kind": "regenerate_image" | "sharpen_hook" | "none",
  "fix": "one specific, actionable fix — if sharpen_hook, give the actual better hook line",
  "note": "one honest sentence explaining the verdict"
}
Rules: default to scroll unless it truly earns the stop. Pick the single
highest-leverage fix, not a list. If the image is the problem, fix_kind is
regenerate_image; if the words are, sharpen_hook. Never invent praise."""


class ScrollJudge(Protocol):
    def judge(self, system: str, user: str, image_b64: str, mime: str) -> str:
        """Run a vision-grounded completion, return the model's text."""
        ...


class OpenAIScrollJudge:
    """Vision judgment via OpenAI chat completions (a data-URL image)."""

    def __init__(self, settings: Settings):
        if not settings.openai_api_key:
            raise ScrollTestError("OPENAI_API_KEY is not set — add it to your .env")
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise ScrollTestError(
                "openai not installed. Run: pip install -e '.[llm]'"
            ) from exc
        # Explicit base_url — see Settings.get_openai_base_url.
        self._client = OpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.get_openai_base_url(),
        )
        # A rubric-based vision verdict — the cheap judge model, not the
        # creative model (both must support image input).
        self._model = settings.judge_model

    def judge(self, system: str, user: str, image_b64: str, mime: str) -> str:
        from .pipeline import _describe_llm_error, _temperature_unsupported

        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user},
                    {
                        "type": "image_url",
                        # "auto" detail ≈ the small, fast in-feed view.
                        "image_url": {
                            "url": f"data:{mime};base64,{image_b64}",
                            "detail": "auto",
                        },
                    },
                ],
            },
        ]

        def _call(temp):
            kwargs = {
                "model": self._model,
                "response_format": {"type": "json_object"},
                "messages": messages,
            }
            if temp is not None:
                kwargs["temperature"] = temp
            return self._client.chat.completions.create(**kwargs)

        try:
            resp = _call(0.2)  # a judge should be steady, not creative
        except Exception as exc:  # noqa: BLE001
            if _temperature_unsupported(exc):
                try:
                    resp = _call(None)  # model only allows the default temperature
                except Exception as exc2:  # noqa: BLE001
                    raise ScrollTestError(_describe_llm_error(exc2)) from exc2
            else:
                raise ScrollTestError(_describe_llm_error(exc)) from exc
        return resp.choices[0].message.content or ""


# --- helpers ----------------------------------------------------------------


def _slide_one_path(idea: Idea) -> Path | None:
    """The rendered slide-1 image for this idea, or None if not rendered yet."""
    if idea.asset_paths_json:
        try:
            paths = json.loads(idea.asset_paths_json)
        except json.JSONDecodeError:
            paths = []
        for p in paths:
            if p and Path(p).exists():
                return Path(p)
    return None


def _mime_for(path: Path) -> str:
    return "image/webp" if path.suffix.lower() == ".webp" else "image/jpeg"


def _first_slide(idea: Idea) -> Slide | None:
    if not idea.slides_json:
        return None
    try:
        rows = json.loads(idea.slides_json)
    except json.JSONDecodeError:
        return None
    return Slide.model_validate(rows[0]) if rows else None


def _build_user_prompt(idea: Idea, perf_notes: str) -> str:
    slide = _first_slide(idea)
    hook = (idea.hook or (slide.headline if slide else "") or "").strip()
    parts = [f"HOOK (the first line on the image): {hook}"]
    if slide and slide.supporting.strip():
        parts.append(f"Supporting line: {slide.supporting.strip()}")
    if idea.target_viewer.strip():
        parts.append(f"Who it's aimed at: {idea.target_viewer.strip()}")
    if perf_notes.strip():
        # The calibration signal: judge against what has actually worked here.
        parts.append(
            "For calibration — what has actually landed for THIS account "
            "(weight your read toward this real history, not generic taste):\n"
            + perf_notes.strip()
        )
    parts.append(
        "Now look at the attached first image as it appears in the feed and "
        "return the JSON verdict. Default to scroll unless it truly earns a stop."
    )
    return "\n\n".join(parts)


def parse_verdict(text: str) -> ScrollVerdict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{") :]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ScrollTestError("no JSON object found in scroll-test response")
    try:
        v = ScrollVerdict.model_validate(json.loads(text[start : end + 1]))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ScrollTestError(f"could not parse scroll-test verdict: {exc}") from exc
    v.confidence = max(0, min(100, int(v.confidence)))
    if v.stops:
        # A stop needs no fix; keep the payload clean.
        v.fix_kind, v.fix, v.killer = "none", "", v.killer
    return v


def run_scroll_test(
    idea: Idea,
    settings: Settings,
    store: Store,
    *,
    judge: ScrollJudge | None = None,
) -> ScrollVerdict:
    """Judge the finished slide 1 for this idea. Raises if it isn't rendered."""
    path = _slide_one_path(idea)
    if path is None:
        raise ScrollTestError(
            f"{idea.idea_id} has no rendered slide 1 to test — render it first"
        )
    from .learning import performance_notes

    judge = judge or OpenAIScrollJudge(settings)
    image_b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    user = _build_user_prompt(idea, performance_notes(store))
    return parse_verdict(judge.judge(SYSTEM_PROMPT, user, image_b64, _mime_for(path)))
