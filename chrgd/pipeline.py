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

from pydantic import ValidationError

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
  "hook": "string",
  "slides": [
    {
      "headline": "string",
      "supporting": "string",
      "image_prompt": "string, uses ONLY approved slide text",
      "visual_intent": "string: subject, setting, action, prop, double-take"
    }
    // One object per slide, in order.
    // Sketch format: EXACTLY 5 slides. Playbook format: 7-10 slides.
  ],
  "caption": "string",
  "comment_trigger": "string",
  "pinned_comments": ["string", "string (1-2 comments to pin after posting)"],
  "hashtags": ["string", "..."],
  "route": {
    "format": "sketch | playbook",
    "mechanic": "string (one of the virality mechanics)",
    "visual_engine": "string (one of the visual engines)",
    "primary_goal": "string",
    "build_note": "format · mechanic · visual engine · primary goal · QA overall",
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

        kwargs = {"api_key": settings.openai_api_key}
        if settings.openai_base_url:
            kwargs["base_url"] = settings.openai_base_url
        self._client = OpenAI(**kwargs)
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
            raise LLMError(str(exc)) from exc

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

    if retry_reasons:
        lines.append("")
        lines.append(
            "Your previous attempt failed QA/validation for these reasons — "
            "fix them and return a stronger post:"
        )
        for reason in retry_reasons:
            lines.append(f"- {reason}")

    return "\n".join(lines)


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


def run_pipeline_for_idea(idea: Idea, client: ChatClient, model: str) -> BuildResult:
    """Run the pipeline for one idea: call, validate, QA-gate, re-request once."""
    system = load_system_prompt()
    spend = 0.0
    retry_reasons: list[str] | None = None
    last_failures: list[str] = []
    last_post: Post | None = None

    for attempt in range(1, 3):  # first try + one re-request
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


def build_fields_from_post(post: Post) -> dict:
    """Serialise a Post into the DB build-field columns."""
    from datetime import datetime, timezone

    return {
        "post_type": post.post_type.value,
        "hook": post.hook,
        "slides_json": json.dumps([s.model_dump() for s in post.slides]),
        "caption": post.caption,
        "comment_trigger": post.comment_trigger,
        "pinned_comments_json": json.dumps(post.pinned_comments),
        "hashtags": json.dumps(post.hashtags),
        "route_json": json.dumps(post.route),
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
                fields = build_fields_from_post(result.post)
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
