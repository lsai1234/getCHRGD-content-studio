"""Pipeline runner — the brain.

Loads the CHRGD Content Engine instructions as the system prompt, sends one
seed idea to the OpenAI API, and gets a finished post back as JSON. The
result is validated against the `Post` model and checked against the QA gate
from the prompt. If it misses, we re-request once; still failing, it's
flagged for `chrgd review` rather than shipped.

The OpenAI SDK is imported lazily so the base install (milestone 1) doesn't
require it, and so tests can inject a fake client.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field, ValidationError

from .config import Settings
from .db import Store
from .models import Idea, Post, Status

PROMPT_FILE = Path(__file__).resolve().parent.parent / "content_engine_prompt.md"

# Appended to the loaded instructions so the engine returns machine-readable
# JSON instead of its default paste-ready text. Mirrors the Post model.
JSON_CONTRACT = """
---

## Output contract (STRICT — this run only)

Return a SINGLE JSON object and nothing else. No markdown, no commentary.
Run all six stages internally as instructed above, then emit only the result
in exactly this shape:

{
  "post_type": "carousel",
  "hook": "string — the STRONGEST of the three hook_options",
  "hook_options": [
    "string — 3 genuinely different hook angles for slide 1, strongest first"
  ],
  "slides": [
    {
      "headline": "string",
      "supporting": "string",
      "image_prompt": "string — a COMPLETE graphic-design brief for this slide as a piece of scroll-stopping social media art: the visual concept (subject, setting, action, mood), the composition and layout, the art direction (photographic/illustrated/graphic style, colour, lighting), and how the typography should be treated as part of the design (placement, scale, energy). Think agency-level concept art for TikTok, not a stock photo description. Do NOT invent text beyond the approved slide copy.",
      "visual_intent": "string: subject, setting, action, prop, double-take"
    }
    // EXACTLY 5 slide objects, in order
  ],
  "caption": "string",
  "comment_trigger": "string",
  "hashtags": ["string", "..."],
  "route": {
    "mechanic": "string (one of the virality mechanics)",
    "visual_engine": "string (one of the visual engines)",
    "primary_goal": "string",
    "build_note": "mechanic · visual engine · primary goal · QA overall",
    "qa": {
      "hook": 0, "swipe_loop": 0, "identity_recognition": 0,
      "group_chat_share": 0, "comment_fight": 0, "saveability": 0,
      "visual_originality": 0, "dopamine_density": 0, "clarity": 0,
      "layout_safety": 0, "claim_safety": 0, "overall": 0
    }
  }
}

All QA scores are integers 0–10 and must reflect the honest, brutal QA stage.
Apply the QA thresholds and REVISE internally before returning — only emit a
post you would pass. Use only the approved slide text inside image prompts.
"""

# Rough USD price per 1M tokens (input, output), for spend logging only.
# Falls back to the default for unknown models.
_PRICES: dict[str, tuple[float, float]] = {
    "gpt-4o": (2.5, 10.0),
    "gpt-4o-mini": (0.15, 0.6),
    "gpt-4.1": (2.0, 8.0),
    "gpt-4.1-mini": (0.4, 1.6),
    "gpt-4.1-nano": (0.1, 0.4),
}
_DEFAULT_PRICE = (2.5, 10.0)


def _price_for(model: str) -> tuple[float, float]:
    for prefix, price in _PRICES.items():
        if model.startswith(prefix):
            return price
    return _DEFAULT_PRICE


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    pin, pout = _price_for(model)
    return (prompt_tokens * pin + completion_tokens * pout) / 1_000_000


class LLMError(RuntimeError):
    """Raised when the LLM call itself fails (network/auth/etc.)."""


def _describe_llm_error(exc: Exception) -> str:
    """A failure message that keeps the underlying cause.

    The OpenAI SDK's APIConnectionError stringifies to just 'Connection
    error.', hiding whether it was DNS, TLS, a timeout or a refused route —
    exactly what you need to know when it happens on a server.
    """
    msg = str(exc)
    cause = exc.__cause__ or exc.__context__
    if cause and str(cause) and str(cause) != msg:
        msg = f"{msg} [{type(cause).__name__}: {cause}]"
    return msg


@dataclass
class LLMResult:
    content: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


class ChatClient(Protocol):
    """Minimal interface the runner needs — real client or a test fake."""

    def complete(self, system: str, user: str) -> LLMResult: ...


class OpenAIChatClient:
    """Thin wrapper over the OpenAI SDK, JSON-mode chat completions."""

    def __init__(self, settings: Settings, temperature: float = 0.9):
        if not settings.openai_api_key:
            raise LLMError("OPENAI_API_KEY is not set — add it to your .env")
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - install-time guard
            raise LLMError(
                "openai package not installed. Run: pip install -e '.[llm]'"
            ) from exc

        # base_url is ALWAYS passed explicitly so a stray empty
        # OPENAI_BASE_URL env var can never reach the SDK.
        self._client = OpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.get_openai_base_url(),
        )
        self._model = settings.openai_model
        self._temperature = temperature

    def complete(self, system: str, user: str) -> LLMResult:
        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                temperature=self._temperature,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
        except Exception as exc:  # noqa: BLE001 - surface any SDK failure uniformly
            raise LLMError(_describe_llm_error(exc)) from exc

        usage = getattr(resp, "usage", None)
        return LLMResult(
            content=resp.choices[0].message.content or "",
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )


def load_system_prompt() -> str:
    """The content engine instructions plus the strict JSON output contract."""
    base = PROMPT_FILE.read_text(encoding="utf-8")
    return base + "\n" + JSON_CONTRACT


def build_user_message(idea: Idea, retry_reasons: list[str] | None = None) -> str:
    """Render a seed row into the instruction the engine builds from."""
    lines = ["Build one finished post from this backlog row. Preserve the core idea."]
    lines.append("")
    fields = {
        "idea_id": idea.idea_id,
        "content_category": idea.content_category,
        "target_viewer": idea.target_viewer,
        "pain_point": idea.pain_point,
        "core_tension": idea.core_tension,
        "concept_note": idea.concept_note,
        "learning_tag": idea.learning_tag,
        "post_type": (idea.post_type.value if idea.post_type else "carousel"),
    }
    for key, value in fields.items():
        if value:
            lines.append(f"- {key}: {value}")

    # A concept brief the human developed and approved outranks free choice —
    # the full write must follow the agreed direction.
    prefs = creation_prefs(idea)
    brief = prefs.get("concept_brief")
    if brief:
        lines.append("")
        lines.append(
            "CONCEPT BRIEF — a human editor developed and approved this "
            "direction with you; the full post MUST follow it:"
        )
        for key in ("angle", "hook_direction", "tone", "visual_direction"):
            if brief.get(key):
                lines.append(f"- {key}: {brief[key]}")
        for i, step in enumerate(brief.get("outline") or [], 1):
            lines.append(f"  slide {i}: {step}")

    # A mechanic chosen up-front (create journey "blank canvas" door) constrains
    # Stage 2 instead of leaving format selection free.
    lock = prefs.get("mechanic_lock")
    if lock:
        lines.append("")
        lines.append(
            f"FORMAT CONSTRAINT: build this as a '{lock.get('name')}' carousel."
        )
        skeleton = lock.get("skeleton") or []
        if skeleton:
            lines.append("Follow this 5-slide skeleton (one line per slide):")
            for i, step in enumerate(skeleton, 1):
                lines.append(f"  {i}. {step}")

    if retry_reasons:
        lines.append("")
        lines.append(
            "Your previous attempt failed QA/validation for these reasons — "
            "fix them and return a stronger post:"
        )
        for reason in retry_reasons:
            lines.append(f"- {reason}")

    return "\n".join(lines)


def creation_prefs(idea: Idea) -> dict:
    """Pre-build choices (style, mechanic lock) stashed in route_json.

    The create journey writes these when the idea is started; builds must
    carry them forward because `save_build` overwrites route_json.
    """
    if not idea.route_json:
        return {}
    try:
        route = json.loads(idea.route_json)
    except json.JSONDecodeError:
        return {}
    return {
        k: route[k]
        for k in ("style", "mechanic_lock", "render_mode", "concept_brief")
        if k in route
    }


def parse_post(content: str) -> Post:
    """Parse and validate the model's JSON into a Post."""
    data = json.loads(content)
    return Post.model_validate(data)


@dataclass
class BuildResult:
    idea_id: str
    status: Status
    post: Post | None = None
    spend_usd: float = 0.0
    attempts: int = 0
    qa_failures: list[str] = field(default_factory=list)
    error: str | None = None


def run_pipeline_for_idea(
    idea: Idea, client: ChatClient, model: str, on_attempt=None
) -> BuildResult:
    """Run the pipeline for one idea: call, validate, QA-gate, re-request once.

    `on_attempt(n)` fires before each engine call so callers can surface
    progress (attempt 1 = first write, attempt 2 = post-QA rewrite).
    """
    system = load_system_prompt()
    spend = 0.0
    retry_reasons: list[str] | None = None
    last_failures: list[str] = []
    last_post: Post | None = None

    for attempt in range(1, 3):  # first try + one re-request
        if on_attempt:
            on_attempt(attempt)
        user = build_user_message(idea, retry_reasons)
        result = client.complete(system, user)
        spend += estimate_cost(model, result.prompt_tokens, result.completion_tokens)

        try:
            post = parse_post(result.content)
        except (json.JSONDecodeError, ValidationError) as exc:
            last_post = None
            last_failures = [f"invalid JSON/schema: {exc}"]
            retry_reasons = last_failures
            continue

        last_post = post
        failures = post.qa_failures()
        if not failures:
            return BuildResult(
                idea_id=idea.idea_id,
                status=Status.done,
                post=post,
                spend_usd=spend,
                attempts=attempt,
            )
        last_failures = failures
        retry_reasons = failures

    # Both attempts exhausted — flag the last post (if any) for review.
    return BuildResult(
        idea_id=idea.idea_id,
        status=Status.review,
        post=last_post,
        spend_usd=spend,
        attempts=2,
        qa_failures=last_failures,
    )


def build_fields_from_post(post: Post, extra_route: dict | None = None) -> dict:
    """Serialise a Post into the DB build-field columns.

    `extra_route` re-applies keys that must survive the build overwriting
    route_json (creation prefs like style/mechanic_lock). Hook options ride
    in route_json too — the schema has no dedicated column and doesn't need one.
    """
    from datetime import datetime, timezone

    route = dict(post.route or {})
    if post.hook_options:
        route["hook_options"] = post.hook_options
    if extra_route:
        route.update(extra_route)
    return {
        "post_type": post.post_type.value,
        "hook": post.hook,
        "slides_json": json.dumps([s.model_dump() for s in post.slides]),
        "caption": post.caption,
        "comment_trigger": post.comment_trigger,
        "hashtags": json.dumps(post.hashtags),
        "route_json": json.dumps(route),
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }


def build_ideas(
    store: Store,
    settings: Settings,
    count: int,
    *,
    client: ChatClient | None = None,
    dry_run: bool = False,
    record_run: bool = True,
) -> list[BuildResult]:
    """Build the next `count` queued ideas.

    Honours the per-run spend cap, records a `runs` row, and persists each
    build (done) or flags it for review. `dry_run` still calls the LLM (that's
    the point of the pipeline) but the caller can pass a fake client to avoid
    spend; paid image/video calls are skipped entirely in later milestones.
    `record_run=False` skips the `runs` row so an outer chain can record one
    combined run instead of double-counting spend.
    """
    if client is None:
        client = OpenAIChatClient(settings)

    ideas = store.next_unprocessed(count)
    run_id = store.start_run("build") if record_run else None
    results: list[BuildResult] = []
    total_spend = 0.0

    try:
        for idea in ideas:
            if total_spend >= settings.max_spend_per_run:
                results.append(
                    BuildResult(
                        idea_id=idea.idea_id,
                        status=Status.queued,
                        error=(
                            f"spend cap £{settings.max_spend_per_run:g} reached — "
                            "stopping before this idea"
                        ),
                    )
                )
                break

            store.mark_processing(idea.idea_id)
            try:
                result = run_pipeline_for_idea(
                    idea, client, settings.openai_model
                )
            except LLMError as exc:
                store.set_status(idea.idea_id, Status.queued)  # release for retry
                results.append(
                    BuildResult(
                        idea_id=idea.idea_id, status=Status.queued, error=str(exc)
                    )
                )
                break  # a hard LLM error (auth/network) will hit every idea

            total_spend += result.spend_usd
            if result.post is not None:
                fields = build_fields_from_post(result.post, creation_prefs(idea))
                if result.status is Status.done:
                    store.save_build(idea.idea_id, fields)
                else:
                    store.mark_review(idea.idea_id, fields)
            else:
                store.mark_review(idea.idea_id, {})
            results.append(result)
    finally:
        built = sum(1 for r in results if r.status is Status.done)
        if run_id is not None:
            store.finish_run(
                run_id,
                built=built,
                spend_usd=round(total_spend, 4),
                notes=f"dry_run={dry_run}",
            )

    return results


def build_single_idea(
    store: Store,
    settings: Settings,
    idea_id: str,
    *,
    client: ChatClient | None = None,
    record_run: bool = True,
    on_progress=None,
) -> BuildResult:
    """Build one specific idea (the create journey), regardless of queue order.

    Same persistence rules as `build_ideas`: pass → done, QA miss → review,
    LLM failure → released back to queued. `on_progress(pct, note)` surfaces
    live status to the job poller.
    """

    def _prog(pct: int, note: str) -> None:
        if on_progress:
            on_progress(pct, note)

    idea = store.get_idea(idea_id)
    if idea is None:
        raise ValueError(f"no such idea {idea_id}")
    if client is None:
        client = OpenAIChatClient(settings)

    run_id = store.start_run("build") if record_run else None
    store.mark_processing(idea_id)
    _attempt_notes = {
        1: (15, "engine writing — running all six stages"),
        2: (60, "QA gate missed — asking for a stronger rewrite"),
    }
    try:
        result = run_pipeline_for_idea(
            idea,
            client,
            settings.openai_model,
            on_attempt=lambda n: _prog(*_attempt_notes.get(n, (80, f"attempt {n}"))),
        )
    except LLMError as exc:
        store.set_status(idea_id, Status.queued)
        if run_id is not None:
            store.finish_run(run_id, notes=f"error: {exc}")
        return BuildResult(idea_id=idea_id, status=Status.queued, error=str(exc))

    if result.post is not None:
        fields = build_fields_from_post(result.post, creation_prefs(idea))
        if result.status is Status.done:
            store.save_build(idea_id, fields)
        else:
            store.mark_review(idea_id, fields)
    else:
        store.mark_review(idea_id, {})

    if run_id is not None:
        store.finish_run(
            run_id,
            built=1 if result.status is Status.done else 0,
            spend_usd=round(result.spend_usd, 4),
            notes=idea_id,
        )
    return result


# --- concept development (the "develop it with me" path) ----------------------
#
# A cheap, iterable stage BEFORE the full six-stage write: the engine sketches
# a concept brief (angle, hook direction, slide outline, tone, visual
# direction); the human edits it and feeds back in plain words as many rounds
# as they like; the approved brief then constrains the full build.

BRIEF_CONTRACT = """
---

## Output contract (STRICT — this run only)

You are NOT writing the full post this run. You are developing the CONCEPT
with a human editor. Return a SINGLE JSON object, nothing else:

{
  "angle": "the take in one sharp line",
  "hook_direction": "how slide 1 should open (direction, not final copy)",
  "outline": ["one line per slide describing what it does", "... exactly 5"],
  "tone": "the voice/energy, one line",
  "visual_direction": "the overall look for the designed slides, one line"
}

If the editor gave feedback, apply it faithfully — their input outranks your
own preferences. Keep everything claim-safe and in the brand voice.
"""


class ConceptBrief(BaseModel):
    """The evolving concept a human and the engine develop together."""

    angle: str = ""
    hook_direction: str = ""
    outline: list[str] = Field(default_factory=list)
    tone: str = ""
    visual_direction: str = ""


def develop_concept(
    idea: Idea,
    settings: Settings,
    *,
    feedback: str = "",
    client: ChatClient | None = None,
) -> tuple[ConceptBrief, float]:
    """One development round: seed (+ current brief + editor feedback) → brief."""
    if client is None:
        client = OpenAIChatClient(settings)
    base = PROMPT_FILE.read_text(encoding="utf-8")
    system = base + "\n" + BRIEF_CONTRACT

    lines = ["Develop the concept for this backlog row (do NOT write the full post):", ""]
    for key in ("content_category", "target_viewer", "pain_point", "core_tension",
                "concept_note", "learning_tag"):
        value = getattr(idea, key)
        if value:
            lines.append(f"- {key}: {value}")
    current = creation_prefs(idea).get("concept_brief")
    if current:
        lines += ["", "Current brief (evolve it, don't start over):",
                  json.dumps(current, indent=2)]
    if feedback.strip():
        lines += ["", "EDITOR FEEDBACK — apply this faithfully:", feedback.strip()]

    result = client.complete(system, "\n".join(lines))
    spend = estimate_cost(
        settings.openai_model, result.prompt_tokens, result.completion_tokens
    )
    brief = ConceptBrief.model_validate(json.loads(result.content))
    return brief, spend


# --- facts → angles (create journey, "facts" door) ---------------------------

ANGLES_CONTRACT = """
---

## Output contract (STRICT — this run only)

You are NOT building a post this run. The user gives you raw facts/research.
For EACH promising fact, propose up to {count} distinct carousel ANGLES that
would perform on TikTok for this brand. Angles must be genuinely different
takes (myth-bust, "nobody tells you this", listicle, hot take, story...), not
rewordings. Respect claim safety — drop facts that can't be made safe.

Return a SINGLE JSON object, nothing else:

{{
  "angles": [
    {{
      "title": "short label for the picker UI",
      "mechanic": "one of the virality mechanics",
      "hook": "the slide-1 hook this angle would open with",
      "concept_note": "1-2 sentence brief a builder could work from",
      "pain_point": "string",
      "core_tension": "string",
      "target_viewer": "string",
      "fact": "the source fact this angle came from, verbatim-ish"
    }}
  ]
}}
"""


class Angle(BaseModel):
    """One proposed take on a fact — pickable in the create journey."""

    title: str
    mechanic: str = ""
    hook: str = ""
    concept_note: str
    pain_point: str = ""
    core_tension: str = ""
    target_viewer: str = ""
    fact: str = ""


@dataclass
class AnglesResult:
    angles: list[Angle] = field(default_factory=list)
    spend_usd: float = 0.0


def generate_angles(
    facts: str,
    settings: Settings,
    *,
    client: ChatClient | None = None,
    per_fact: int = 3,
) -> AnglesResult:
    """Turn pasted facts/research into pickable carousel angles."""
    if client is None:
        client = OpenAIChatClient(settings)
    base = PROMPT_FILE.read_text(encoding="utf-8")
    system = base + "\n" + ANGLES_CONTRACT.format(count=per_fact)
    user = "Here are the facts/research to turn into angles:\n\n" + facts.strip()
    result = client.complete(system, user)
    spend = estimate_cost(
        settings.openai_model, result.prompt_tokens, result.completion_tokens
    )
    data = json.loads(result.content)
    angles = [Angle.model_validate(a) for a in data.get("angles", [])]
    return AnglesResult(angles=angles, spend_usd=spend)


# --- targeted revision (the scorecard's "punch it up") ------------------------

REVISE_INSTRUCTION = (
    "REVISION RUN: below is a post you already built, plus feedback from the "
    "human editor. Apply the feedback while keeping everything that already "
    "works — same core idea and slide count unless the feedback says "
    "otherwise. If the feedback names a QA metric, rewrite to maximise that "
    "metric. Re-run the brutal QA stage on the revision before returning it."
)


def revise_post(
    idea: Idea,
    focus: str,
    settings: Settings,
    *,
    client: ChatClient | None = None,
) -> tuple[Post, float]:
    """One revision pass. `focus` is a QA metric ('saveability') or freeform
    editor feedback ('make slide 3 about gym anxiety, drop the bro tone').
    Returns (post, spend)."""
    if not idea.slides_json:
        raise ValueError(f"{idea.idea_id} has no built post to revise")
    if client is None:
        client = OpenAIChatClient(settings)

    current = {
        "hook": idea.hook,
        "slides": json.loads(idea.slides_json),
        "caption": idea.caption,
        "comment_trigger": idea.comment_trigger,
        "hashtags": json.loads(idea.hashtags) if idea.hashtags else [],
    }
    user = "\n".join(
        [
            REVISE_INSTRUCTION,
            "",
            f"FEEDBACK / FOCUS: {focus}",
            "",
            "Current post:",
            json.dumps(current, indent=2),
        ]
    )
    result = client.complete(load_system_prompt(), user)
    spend = estimate_cost(
        settings.openai_model, result.prompt_tokens, result.completion_tokens
    )
    return parse_post(result.content), spend
