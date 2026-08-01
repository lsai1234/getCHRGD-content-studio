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
from .shows import get_show

PROMPT_FILE = Path(__file__).resolve().parent.parent / "content_engine_prompt.md"
# Persona + gold-standard examples — the single biggest quality lever. Optional:
# if present it's injected into the system prompt so the engine imitates a
# specific voice and specific winners instead of reasoning from rules alone.
BRAND_BIBLE_FILE = Path(__file__).resolve().parent.parent / "brand_bible.md"
# The evidence library: proven TikTok shapes the concept stage must anchor to.
# A living file the editor extends whenever they see a carousel bang.
PLAYBOOK_FILE = Path(__file__).resolve().parent.parent / "viral_playbook.md"

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
      "body": "string — OPTIONAL detail block, empty string for most slides. Use it ONLY on a slide that genuinely earns density (usually the escalation or payoff): 2-5 tight sentences or a short list telling the full story/mechanism/details — the slide people stop and actually READ, then screenshot or save. Written to be read on a phone: short sentences, concrete specifics, no filler. Never pad a slide with a body just to look thorough.",
      "role": "string — this slide's job in the arc: one of hook / recognition / escalation / payoff / cta. The slides in order must form a real story with rising tension, NOT parallel restatements of the theme.",
      "swipe_trigger": "string — the OPEN LOOP this slide leaves dangling: the specific reason the viewer swipes to the NEXT slide (a question raised, a reveal promised, a tension unresolved). The last slide's trigger is the social action (rank/confess/tag/argue/save). Every non-final slide MUST hand off to the next one.",
      "image_prompt": "string — a COMPLETE visual brief for this slide that a stranger scrolling the FYP would read as NATIVE TikTok content, NOT an advert. Default to the platform's native visual formats: a candid phone photo (harsh direct flash, slightly awkward framing, a real UK gym), a screenshot-style artefact (notes app, a group-chat thread, a poster stuck on the gym wall), a meme-shaped image, or a photo-dump frame. Reserve a designed/illustrated treatment ONLY for a slide whose concept IS the design (a tier chart, a fake receipt/document, a ranking). Write it as ONE FRAME of the shared design_system below (same palette, recurring motif and type treatment as its siblings) — but the continuity should feel like the same person's camera roll, not a branded template. Cover the visual concept (subject, setting, action, mood) and the composition, then say what has CHANGED from the previous frame so the swipe shows visible motion/escalation. HARD RULE: if the finished image could pass for a supplement brand's paid ad or for generic AI art, the brief has failed — rewrite it rawer and more native. Do NOT invent text beyond the approved slide copy.",
      "visual_intent": "string: subject, setting, action, prop, double-take",
      "feature_character": "boolean — does the account's recurring person/mascot genuinely belong in THIS slide's scene? true ONLY when a human is the point of the frame (reacting, demonstrating, being the subject, holding the prop). false for object, product, chart, text-led or pure environment slides. Do NOT default to true — most sets show the person on only a couple of beats (usually the hook and the payoff), not every slide. When true, the person should be framed for THIS beat (its own angle, distance, pose), not repeated identically frame to frame."
    }
    // One object per slide, in order — 1 to 10 slides, however many the
    // Architect stage decided this idea deserves
  ],
  "caption": "string — the TikTok caption. Line 1 is a punchy TITLE that stops the scroll (can use an emoji). Then 1-2 short DESCRIPTION sentences giving context and driving a comment/save. Do NOT put hashtags in here — they go in the hashtags array below.",
  "comment_trigger": "string — one comment prompt that is EFFORTLESS and SELF-DEFINING to answer: a self-categorisation ('which one are you'), a slightly-wrong ranking people must correct, or a confession invite. Answerable in under 5 words without thinking. Never 'what do you think?'",
  "hashtags": ["6-10 relevant hashtags: a mix of broad UK gym/fitness reach tags and 2-3 niche/topical ones for this exact post; each starts with #"],
  "route": {
    "mechanic": "string (one of the virality mechanics)",
    "visual_engine": "string (one of the visual engines)",
    "primary_goal": "string",
    "engagement_play": "string — the ONE deliberate off-screen action this post is BUILT to earn, chosen and named, exactly one of: 'save' (reference/checklist value — the final slide AND/OR the caption must literally tell them to save or screenshot it), 'share' (the caption or final slide must name WHO to send it to, e.g. 'send this to the mate who still skips leg day'), or 'comment' (the comment_trigger below is the play). Pick one and EXECUTE it explicitly in the actual copy — a declared play that never appears in a slide or the caption is an auto-fail.",
    "psych": {
      "emotion": "string — the ONE high-arousal target emotion (amusement / indignation / awe / recognition-shock / anxiety-relief). If the honest label is 'interested' or 'fair point', you routed wrong — go back.",
      "hook_question": "string — the exact question slide 1 plants in the viewer's head, phrased in THEIR words. If you cannot write this question, there is no curiosity gap and the hook fails.",
      "share_identity": "string — complete the sentence: 'sending this to a mate says ___ about me'. This is why the post gets shared; if the blank can't be filled, it won't be.",
      "named_unnamed": "string — the line in the post that names something the viewer has felt but never said out loud (the deepest recognition trigger). Empty string ONLY if the post genuinely runs on a different engine (e.g. pure awe fact)."
    },
    "throughline": "string — one line describing the single journey the swipe takes the viewer on, start to finish (the spine every slide is a beat of).",
    "design_system": {
      "palette": "string — the ONE colour palette used on every slide (name 2-4 specific colours + how they're used). Shared across the whole set so it reads as one piece.",
      "type_style": "string — the ONE typographic treatment for headlines + supporting text on every slide (font character, weight, case, how text sits in the layout).",
      "motif": "string — a recurring visual anchor carried across all slides so the viewer feels continuity: PREFER an object or graphic device (a colour block, a prop, a symbol) that can sit in every frame. It may be the recurring person, but a person does NOT have to appear on every slide — mark each slide's `feature_character` and only show them where they're the point.",
      "layout": "string — the shared layout grid every slide follows (where the headline sits, where the subject sits, consistent margins), so the set feels like one designed template, not random posters.",
      "evolution": "string — how the shared design VISIBLY changes as the story escalates (e.g. palette heats up, the character's state shifts, the composition tightens, a progress motif fills). This is what makes swiping feel like motion."
    },
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
Revise internally first to make the post as strong as you genuinely can — but
then SCORE THE POST YOU ACTUALLY WROTE, honestly. Do NOT inflate scores to
clear the thresholds. A true 6 reported as 6 gets this post one targeted
rewrite and comes back stronger; a true 6 reported as 9 ships a weak post to a
real audience and poisons the account's own performance data. Honest failure
is cheap; dishonest passing is expensive. Use only the approved slide text
inside image prompts.
"""

# Rough USD price per 1M tokens (input, output), for spend logging only —
# estimates, confirm on your account. Falls back to the default for unknowns.
_PRICES: dict[str, tuple[float, float]] = {
    "gpt-4o": (2.5, 10.0),
    "gpt-4o-mini": (0.15, 0.6),
    "gpt-4.1": (2.0, 8.0),
    "gpt-4.1-mini": (0.4, 1.6),
    "gpt-4.1-nano": (0.1, 0.4),
    "gpt-5": (1.25, 10.0),
    "gpt-5-mini": (0.25, 2.0),
    "gpt-5-nano": (0.05, 0.4),
}
_DEFAULT_PRICE = (2.5, 10.0)


def _price_for(model: str) -> tuple[float, float]:
    # Longest matching prefix wins, so "gpt-4o-mini" isn't mispriced as "gpt-4o"
    # and "gpt-5-mini"/"gpt-5-nano" aren't mispriced as "gpt-5".
    best: tuple[str, tuple[float, float]] | None = None
    for prefix, price in _PRICES.items():
        if model.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, price)
    if best is not None:
        return best[1]
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


def _temperature_unsupported(exc: Exception) -> bool:
    """Newer OpenAI models reject an explicit `temperature` (only the default
    is allowed) with a 400 like: "'temperature' does not support 0.9 with this
    model. Only the default (1) value is supported." Detect that specific case
    so we can retry without the parameter."""
    s = str(exc).lower()
    return "temperature" in s and (
        "does not support" in s or "unsupported" in s or "only the default" in s
    )


def _max_tokens_unsupported(exc: Exception) -> bool:
    """Newer OpenAI models reject `max_tokens` and want `max_completion_tokens`
    instead ("Unsupported parameter: 'max_tokens' is not supported with this
    model. Use 'max_completion_tokens'"). Detect it so we can retry with the
    other spelling rather than losing the cap (or the call)."""
    s = str(exc).lower()
    return "max_tokens" in s and (
        "not supported" in s or "unsupported" in s or "max_completion_tokens" in s
    )


def chat_json_create(client, *, model, system, user, temperature, max_tokens=None):
    """A JSON-mode chat completion that survives models with restrictive params.

    Tries with the requested temperature and an optional output cap; on the
    specific "temperature unsupported" 400 it retries WITHOUT the temperature,
    and on the "use max_completion_tokens" 400 it retries with that spelling.
    Every JSON chat call in the app routes through here so one restrictive model
    can't break builds, the judges, the selector or the concept engine at once.

    `max_tokens` is a latency lever as much as a cost one: a capped, short JSON
    response comes back markedly faster, which is why the fast concept sketch
    sets it."""
    def _call(temp, token_key):
        kwargs = {
            "model": model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if temp is not None:
            kwargs["temperature"] = temp
        if max_tokens:
            kwargs[token_key] = max_tokens
        return client.chat.completions.create(**kwargs)

    temp, token_key = temperature, "max_tokens"
    while True:
        try:
            return _call(temp, token_key)
        except Exception as exc:  # noqa: BLE001
            if temp is not None and _temperature_unsupported(exc):
                temp = None  # this model only allows the default — drop it
                continue
            if max_tokens and token_key == "max_tokens" and _max_tokens_unsupported(exc):
                token_key = "max_completion_tokens"
                continue
            raise


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

    def __init__(
        self,
        settings: Settings,
        temperature: float = 0.9,
        *,
        model: str | None = None,
        max_tokens: int | None = None,
    ):
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
        # Defaults to the creative model; callers that only need speed (the
        # concept sketch) pass the cheap scout model instead.
        self._model = model or settings.openai_model
        self._temperature = temperature
        self._max_tokens = max_tokens

    def complete(self, system: str, user: str) -> LLMResult:
        try:
            resp = chat_json_create(
                self._client, model=self._model, system=system, user=user,
                temperature=self._temperature, max_tokens=self._max_tokens,
            )
        except Exception as exc:  # noqa: BLE001 - surface any SDK failure uniformly
            raise LLMError(_describe_llm_error(exc)) from exc

        usage = getattr(resp, "usage", None)
        return LLMResult(
            content=resp.choices[0].message.content or "",
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )


def load_brand_bible() -> str:
    """The persona + gold-standard examples, if the file exists ('' otherwise)."""
    if BRAND_BIBLE_FILE.exists():
        return BRAND_BIBLE_FILE.read_text(encoding="utf-8").strip()
    return ""


def load_playbook() -> str:
    """The proven-shapes library, if the file exists ('' otherwise)."""
    if PLAYBOOK_FILE.exists():
        return PLAYBOOK_FILE.read_text(encoding="utf-8").strip()
    return ""


def engine_base() -> str:
    """The engine instructions + brand bible + viral playbook — shared by every
    reasoning call (build, takes, angles, concept development) so the voice AND
    the evidence base are consistent everywhere."""
    base = PROMPT_FILE.read_text(encoding="utf-8")
    bible = load_brand_bible()
    if bible:
        base += (
            "\n---\n\n# BRAND BIBLE — voice + gold-standard examples "
            "(match this level; imitate the voice and structure, not the topics)\n\n"
            + bible
        )
    playbook = load_playbook()
    if playbook:
        base += (
            "\n---\n\n# VIRAL PLAYBOOK — proven shapes (anchor concepts in "
            "these precedents, or in the live trend the seed carries)\n\n"
            + playbook
        )
    # UK market spine — every reasoning call writes UK-native (Bet 2).
    from .uk import uk_context_block

    spine = uk_context_block()
    if spine:
        base += "\n---\n\n# " + spine
    return base


def load_system_prompt() -> str:
    """Content engine instructions + brand bible + the strict JSON contract."""
    return engine_base() + "\n" + JSON_CONTRACT


# The editor's optional up-front length nudge → the instruction the engine
# sees. Deliberately loose ranges — a preference, not a category system.
LENGTH_PREFS = {
    "quick": (
        "the editor wants a quick hit — roughly 3–4 slides, a fast punch that "
        "still gives the algorithm swipes to count. Go below 3 only if the idea "
        "genuinely dies stretched; run longer if it truly needs the room."
    ),
    "standard": (
        "the editor wants a classic carousel — roughly 4–6 slides with a "
        "proper arc."
    ),
    "deep": (
        "the editor wants a fuller deep-dive — roughly 7–10 slides. Every "
        "extra slide must still earn its swipe; if the idea runs out of "
        "genuine material sooner, stop sooner."
    ),
}


def build_user_message(
    idea: Idea,
    retry_reasons: list[str] | None = None,
    performance_notes: str = "",
) -> str:
    """Render a seed row into the instruction the engine builds from."""
    lines = ["Build one finished post from this backlog row. Preserve the core idea."]
    lines.append("")
    if performance_notes:
        lines.append(performance_notes)
        lines.append("")
    prefs = creation_prefs(idea)
    lines.extend(_seed_context(idea, prefs))

    # The show's brief: what turns one general-purpose engine into five
    # recognisable formats. It sits directly after the seed so every later
    # instruction (take, concept brief, length) is read in the show's context.
    # Absent for an off-format post — the message is then byte-identical to
    # what the engine built before shows existed.
    show = get_show(str(prefs.get("show") or ""))
    if show is not None:
        lines.append("")
        # An explicit editor length choice outranks the show's own range.
        lines.append(
            show.brief_block(include_length=not prefs.get("length_pref"))
        )

    # A take the editor chose from a fan-out is the agreed direction — the
    # full write must BE that take, not a fresh interpretation of the seed.
    take = prefs.get("take")
    if take:
        lines.append("")
        lines.append(
            "CHOSEN TAKE — the editor saw several competing takes and picked "
            "THIS one; the post must be this take executed brilliantly, not "
            "a different interpretation:"
        )
        for key in ("title", "angle", "hook", "mechanic", "emotion",
                    "share_identity", "precedent", "pillar", "sketch"):
            if take.get(key):
                lines.append(f"- {key}: {take[key]}")
        if take.get("tweak"):
            lines.append(
                f"- EDITOR'S TWEAK (apply faithfully): {take['tweak']}"
            )

    # A concept brief the human developed and approved outranks free choice —
    # the full write must follow the agreed direction.
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

    # An up-front length nudge from the editor. Soft by design: the Architect
    # stage still owns the final count, this just tells it where to aim.
    length = LENGTH_PREFS.get(prefs.get("length_pref", ""))
    if length:
        lines.append("")
        lines.append(f"LENGTH: {length}")

    if retry_reasons:
        lines.append("")
        lines.append(
            "Your previous attempt failed QA/validation for these reasons — "
            "fix them and return a stronger post:"
        )
        for reason in retry_reasons:
            lines.append(f"- {reason}")

    return "\n".join(lines)


def _seed_context(idea: Idea, prefs: dict) -> list[str]:
    """The seed's full context, shared by the fan-out (takes) and the build
    so every stage reasons from the same brief: row fields, the cultural
    moment it rides, any locked format, and the blank-canvas subject rules."""
    lines: list[str] = []
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

    # A moment/trend-anchored seed carries the full context it rides. The
    # account's biggest win (the 2am England-game post) worked because the
    # moment was the star — so every stage must keep that inversion explicit.
    moment = prefs.get("moment")
    if moment and moment.get("kind") == "ragebait":
        lines.append("")
        lines.append(
            "RAGEBAIT POST — this post is deliberately divisive: it states "
            "one side of a genuine debate at maximum confidence so the "
            "comment section does the distribution. The ARGUMENT is the star; "
            "the brand is the loudmouth mate who started it and can back "
            "every word:"
        )
        for key in ("title", "why", "when", "category", "angle"):
            if moment.get(key):
                lines.append(f"- {key}: {moment[key]}")
        lines.append(
            "Rules of the fight: COMMIT to the take — no hedging, no 'in my "
            "opinion', no both-sides slide; the post is the mate who says it "
            "outright. Leave ONE obvious counter-argument conspicuously "
            "unaddressed — that gap is what the comments rush to fill (a "
            "slightly-wrong-on-purpose ranking works the same way). The "
            "comment trigger must invite the OTHER side to fight back, never "
            "ask for agreement. Weight the writing toward comment_fight and "
            "group_chat_share — this post lives or dies on the argument it "
            "starts. Hard limits, non-negotiable: the take stays a genuinely "
            "defensible OPINION — never a fabricated fact, invented statistic "
            "or debunked myth presented as truth; punch at behaviours and "
            "ideas, never named people, protected groups or bodies; nothing "
            "medical or guaranteed-outcome; the brand must be able to stand "
            "behind every word when it blows up."
        )
    elif moment and moment.get("kind") == "trending":
        lines.append("")
        lines.append(
            "LIVE TREND — this post rides a format/meme/discourse the "
            "audience is actively participating in. The trend's FORMAT is "
            "the vehicle: follow it faithfully (that recognition is what "
            "makes it land), and pour the brand's world into it:"
        )
        for key in ("title", "why", "when", "peak", "category", "angle"):
            if moment.get(key):
                lines.append(f"- {key}: {moment[key]}")
        lines.append(
            "Rules: the viewer must recognise the trend from their own feed "
            "in the first frame — same structure, same rhythm, same joke "
            "shape, gym/energy subject matter. Don't explain the trend, BE "
            "the trend. Any product mention must fit inside the format "
            "naturally and stay light; a sales post wearing a trend's "
            "clothes gets clocked instantly. If the trend is a spike, lean "
            "fully into right-now energy."
        )
    elif moment:
        lines.append("")
        lines.append(
            "SHARED CULTURAL MOMENT — this post rides a moment the audience "
            "is collectively living through. The MOMENT is the star of the "
            "post; the brand is the sidekick that shows up inside it:"
        )
        for key in ("title", "why", "when", "peak", "category", "angle"):
            if moment.get(key):
                lines.append(f"- {key}: {moment[key]}")
        lines.append(
            "Rules: the viewer must instantly feel 'this is about the thing "
            "I'm living through right now' — use the moment's real, specific "
            "details (names, kick-off times, the 2am alarm, the episode). "
            "Any product/supplement mention must arise naturally FROM the "
            "moment (staying up late → energy) and stay light; if it feels "
            "bolted on, leave it at brand voice only. Never bury the moment "
            "under brand talk."
        )

    # A mechanic chosen up-front (create journey "format" door) constrains
    # Stage 2 instead of leaving format selection free.
    lock = prefs.get("mechanic_lock")
    if lock:
        lines.append("")
        lines.append(
            f"FORMAT CONSTRAINT: build this as a '{lock.get('name')}' carousel."
        )
        skeleton = lock.get("skeleton") or []
        if skeleton:
            lines.append(
                "Follow this skeleton (one line per slide; adapt the slide "
                "count only if the idea genuinely wants fewer or more):"
            )
            for i, step in enumerate(skeleton, 1):
                lines.append(f"  {i}. {step}")


    # STRAIGHT UP builds from an ingredient in the library: the question, the
    # evidence as it stands, the myth, and where the show may be sharp.
    if prefs.get("ingredient"):
        from .ingredients import get_ingredient

        ingredient = get_ingredient(str(prefs["ingredient"]))
        if ingredient is not None:
            lines.append("")
            lines.append(ingredient.brief_block())

    # THE SESSION builds from a point in the variant matrix rather than a
    # topic — that segmentation IS the show.
    if prefs.get("session_variant"):
        from .sessions import brief_block as session_brief

        block = session_brief(prefs["session_variant"])
        if block:
            lines.append("")
            lines.append(block)

    # An Amp post needs the mascot in the WORDS too — without this the engine
    # writes a normal carousel that only looks like Amp once the images render.
    # Outside the mechanic branch on purpose: Amp is now a SHOW, so a post
    # started from the AMP tile carries no mechanic lock and would otherwise
    # never get this. Pass-through for every post that isn't Amp's.
    from .character import amp_brief_for, is_amp_route

    if is_amp_route(prefs):
        lock = prefs.get("mechanic_lock") or {}
        lines.append("")
        lines.append(amp_brief_for(prefs, len(lock.get("skeleton") or []) or 5))

    # Blank-canvas builds (mechanic picked, no subject given) are where the
    # engine drifts into obscure trivia nobody holds — pin it to the middle
    # of the audience's actual lived experience instead.
    if idea.concept_note.strip().lower() in ("", "blank canvas"):
        lines.append("")
        lines.append(
            "SUBJECT CHOICE (no concept was given): choose the subject "
            "yourself, from the heart of this brand's world — training, gym "
            "culture, energy/caffeine, protein, recovery, discipline. Pick "
            "the most WIDELY-LIVED version you can: something most gym-goers "
            "have personally believed, argued about in the group chat, or "
            "done themselves this month. Reject obscure trivia, fringe "
            "debates, and myths nobody actually holds — if a typical member "
            "of the audience wouldn't recognise it from their own life, "
            "pick again."
        )

    return lines


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
        for k in (
            "style", "mechanic_lock", "concept_brief",
            "length_pref", "moment", "take", "casting_intensity",
            # The show this post is an episode of (chrgd/shows.py). Absent on
            # every off-format post, which is what keeps this a pass-through.
            "show",
            # AMP's journey: the situation he's in, the state he's in, and the
            # tip the post owes the viewer (chrgd/character.py). Only set by
            # the AMP show's screen.
            "amp_state", "amp_situation", "amp_situation_text", "amp_tip",
            # STRAIGHT UP's subject and THE SESSION's point in the variant
            # matrix — each written by that show's own screen.
            "ingredient", "session_variant",
        )
        if k in route
    }


def _build_extra_route(store: Store, idea: Idea) -> dict:
    """Creation prefs to re-apply on a build, plus the profile's casting level
    stamped in on the first build so the learning loop can bucket by it. Once
    stamped it rides route_json, so creation_prefs carries it forward after."""
    extra = creation_prefs(idea)
    if "casting_intensity" not in extra:
        from .profile import load_profile

        ci = load_profile(store).casting_intensity.strip().lower()
        if ci in ("natural", "elevated"):
            extra = {**extra, "casting_intensity": ci}
    return extra


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
    idea: Idea,
    client: ChatClient,
    model: str,
    on_attempt=None,
    performance_notes: str = "",
) -> BuildResult:
    """Run the pipeline for one idea: call, validate, QA-gate, re-request once.

    `on_attempt(n)` fires before each engine call so callers can surface
    progress (attempt 1 = first write, attempt 2 = post-QA rewrite).
    `performance_notes` steers the write with the account's real results.
    """
    system = load_system_prompt()
    spend = 0.0
    retry_reasons: list[str] | None = None
    last_failures: list[str] = []
    last_post: Post | None = None

    for attempt in range(1, 3):  # first try + one re-request
        if on_attempt:
            on_attempt(attempt)
        user = build_user_message(idea, retry_reasons, performance_notes)
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

    from .learning import performance_notes as _perf_notes
    from .profile import brand_build_notes as _brand_notes
    from .trends import meta_notes as _meta_notes

    # The brand profile + the account's own history + the live meta, together.
    notes = "\n\n".join(
        x for x in (_brand_notes(store), _perf_notes(store), _meta_notes(store)) if x
    )

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
                    idea, client, settings.openai_model, performance_notes=notes
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
                fields = build_fields_from_post(result.post, _build_extra_route(store, idea))
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

    # Narrate the actual call lifecycle so the queue shows real steps, not a
    # spinner: request sent → waiting → response received → validating.
    class _NarratedClient:
        def __init__(self, inner, model):
            self._inner, self._model, self._pct = inner, model, 20

        def complete(self, system, user):
            _prog(
                self._pct,
                f"request sent to {self._model} — waiting for the engine "
                "(a full write takes 30-90s)",
            )
            result = self._inner.complete(system, user)
            self._pct = min(self._pct + 30, 85)
            _prog(self._pct, "response received — validating + running the QA gate")
            return result

    client = _NarratedClient(client, settings.openai_model)

    run_id = store.start_run("build") if record_run else None
    store.mark_processing(idea_id)
    _attempt_notes = {
        1: (15, "composing the build instructions"),
        2: (60, "QA gate missed — asking for a stronger rewrite"),
    }
    from .learning import performance_notes
    from .profile import brand_build_notes as _brand_notes
    from .trends import meta_notes as _build_meta_notes

    try:
        result = run_pipeline_for_idea(
            idea,
            client,
            settings.openai_model,
            on_attempt=lambda n: _prog(*_attempt_notes.get(n, (80, f"attempt {n}"))),
            performance_notes="\n\n".join(
                x for x in (
                    _brand_notes(store), performance_notes(store),
                    _build_meta_notes(store),
                ) if x
            ),
        )
    except LLMError as exc:
        store.set_status(idea_id, Status.queued)
        if run_id is not None:
            store.finish_run(run_id, notes=f"error: {exc}")
        return BuildResult(idea_id=idea_id, status=Status.queued, error=str(exc))

    if result.post is not None:
        extra = _build_extra_route(store, idea)
        # The claims gate: a separate pass over the finished post, because a
        # self-scored `claim_safety` inside the write is the model marking its
        # own homework on the one topic carrying real outside risk. The lint
        # runs on every post; the judge and the hold-for-review only apply to a
        # claims-gated show (STRAIGHT UP), so nothing else changes behaviour.
        from .claims import check_claims

        claims = check_claims(result.post.model_dump(mode="json"), idea, settings)
        extra = {**extra, "claims": claims.as_payload()}
        result.spend_usd += claims.spend_usd
        if claims.blocking and not claims.safe:
            _prog(90, f"claims gate flagged {len(claims.flags)} thing(s) — holding for review")
            result.status = Status.review
            result.qa_failures = result.qa_failures + claims.reasons()

        fields = build_fields_from_post(result.post, extra)
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
  "outline": ["one line per slide describing what it does — 1 to 10 lines, as many slides as the idea deserves"],
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
    system = engine_base() + "\n" + BRIEF_CONTRACT

    lines = ["Develop the concept for this backlog row (do NOT write the full post):", ""]
    prefs = creation_prefs(idea)
    lines.extend(_seed_context(idea, prefs))

    # The show's brief: what turns one general-purpose engine into five
    # recognisable formats. It sits directly after the seed so every later
    # instruction (take, concept brief, length) is read in the show's context.
    # Absent for an off-format post — the message is then byte-identical to
    # what the engine built before shows existed.
    show = get_show(str(prefs.get("show") or ""))
    if show is not None:
        lines.append("")
        # An explicit editor length choice outranks the show's own range.
        lines.append(
            show.brief_block(include_length=not prefs.get("length_pref"))
        )
    take = prefs.get("take")
    if take:
        lines += ["", "CHOSEN TAKE — the editor picked this direction from a "
                      "fan-out; the brief must develop THIS take, not drift:",
                  json.dumps(take, indent=2)]
    current = prefs.get("concept_brief")
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
would perform on TikTok for this brand. Angles must run on genuinely different
ENGINES, not rewordings — anchor each to a named playbook shape (myth-bust,
slightly-wrong ranking, "the unsaid thing", receipt breakdown, "wait WHAT"
mechanism, archetype taxonomy, hot take). Do NOT open an angle with a banned
generic hook ("nobody tells you this", "the truth about", "this changed
everything"). Respect claim safety — drop facts that can't be made safe.

For every angle, run stage 0 before you write it: name its ONE high-arousal
target emotion (amusement / indignation / awe / recognition-shock /
anxiety-relief — never "interested"), and complete the line "sending this to a
mate says ___ about me". An angle whose emotion is "interested" or whose
share line can't be completed doesn't belong in the list — replace it.

Return a SINGLE JSON object, nothing else:

{{
  "angles": [
    {{
      "title": "short label for the picker UI",
      "mechanic": "one of the virality mechanics / playbook shapes",
      "hook": "the slide-1 hook this angle would open with (thumb-stopping, not a blog title)",
      "emotion": "the ONE high-arousal target emotion",
      "share_identity": "sending this to a mate says ___ about me — completed",
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
    emotion: str = ""
    share_identity: str = ""
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
    system = engine_base() + "\n" + ANGLES_CONTRACT.format(count=per_fact)
    user = "Here are the facts/research to turn into angles:\n\n" + facts.strip()
    result = client.complete(system, user)
    spend = estimate_cost(
        settings.openai_model, result.prompt_tokens, result.completion_tokens
    )
    data = json.loads(result.content)
    angles = [Angle.model_validate(a) for a in data.get("angles", [])]
    return AnglesResult(angles=angles, spend_usd=spend)


# --- the fan-out: competing takes before the expensive write -------------------
#
# The moment of highest leverage is "which concept" — and confidence comes from
# CHOOSING between visibly different takes, not receiving one. Every non-manual
# door runs this cheap divergence stage before the full six-stage write: one
# call, several genuinely different takes, the human picks/steers, THEN we pay
# for the build.

TAKES_CONTRACT = """
---

## Output contract (STRICT — this run only)

You are NOT writing a post this run. You are the DIVERGENCE stage: from the
seed below, propose exactly {count} genuinely different takes a human editor
will choose between. Rules:

- EVIDENCE FIRST: every take must be anchored in something that has already
  worked — a named shape from the VIRAL PLAYBOOK above, or the live trend the
  seed carries. State the anchor in `precedent` and WHY that shape fits this
  seed. A take with no precedent is a guess; you may include at most ONE
  guess per fan-out, flagged as "no precedent — experimental" in its
  precedent field, and only when it's genuinely worth the risk.
- No two takes may run on the same playbook shape — competing takes means
  competing engines, not one shape reworded. Test: if two of your takes
  could open with the same slide 1, replace one of them.
- Spread the risk: at least one safe-banker take, at least one unhinged-but-
  clear take, at least one built primarily for comments/debate.
- Run stage 0 on every take: name its high-arousal emotion and complete its
  'sending this says ___ about me' line honestly. A take whose emotion is
  'interested' doesn't belong in the list — replace it.
- If WHAT WORKS FOR THIS ACCOUNT notes are provided, weight the fan-out
  toward the shapes/categories that hit and away from the ones that flopped
  — the account's own history outranks generic instinct.
- PILLAR: if the brand profile lists content pillars, every take names the ONE
  pillar it belongs to (a take that fits no pillar is off-strategy — replace
  it); across the fan-out, cover at least TWO different pillars so the editor
  can steer the account's balance. If no pillars are given, leave `pillar` an
  empty string.
- Honour the seed's constraints (cultural moment as the star, trend format
  followed faithfully, format lock, claim safety) in every take.

Return a SINGLE JSON object, nothing else:

{{
  "takes": [
    {{
      "title": "3-6 word label for the picker",
      "angle": "the take in one sharp line — what the post IS",
      "hook": "the slide-1 line this take opens with (final-copy quality)",
      "mechanic": "one of the virality mechanics",
      "emotion": "the high-arousal target emotion",
      "share_identity": "sending this to a mate says ___ about me — completed",
      "precedent": "the playbook shape or live trend this take runs on + one line on why it has banged before and fits here",
      "pillar": "the ONE content pillar from the brand profile this take belongs to (empty string if no pillars are defined)",
      "sketch": "the arc in 2-3 short beats, ' → ' separated",
      "concept_note": "1-2 sentence buildable brief for the full write"
    }}
  ]
}}
Order by your honest bet on performance, strongest first.
"""


class Take(BaseModel):
    """One competing direction from the fan-out — pickable before the build."""

    title: str
    angle: str = ""
    hook: str = ""
    mechanic: str = ""
    emotion: str = ""
    share_identity: str = ""
    # The evidence anchor: the playbook shape or live trend this take runs on,
    # plus why it has banged before. "no precedent — experimental" is allowed
    # at most once per fan-out.
    precedent: str = ""
    # The content pillar this take belongs to (from the brand profile). Rides
    # route_json with the chosen take so the learning loop can track it.
    pillar: str = ""
    sketch: str = ""
    concept_note: str = ""


@dataclass
class TakesResult:
    takes: list[Take] = field(default_factory=list)
    spend_usd: float = 0.0


def generate_takes(
    idea: Idea,
    settings: Settings,
    *,
    count: int = 5,
    feedback: str = "",
    prior: list[dict] | None = None,
    performance_notes: str = "",
    client: ChatClient | None = None,
) -> TakesResult:
    """One divergence round: seed (+ prior takes + editor feedback) → takes.

    `performance_notes` carries the account's own hit/flop history so the
    fan-out is weighted by what has actually worked HERE, not just broadly."""
    if client is None:
        client = OpenAIChatClient(settings)
    system = engine_base() + "\n" + TAKES_CONTRACT.format(count=count)

    lines = ["Propose competing takes for this seed:", ""]
    if performance_notes:
        lines += [performance_notes, ""]
    lines.extend(_seed_context(idea, creation_prefs(idea)))
    if prior:
        lines += ["", "TAKES ALREADY SHOWN (the editor passed on all of these "
                      "— every new take must differ from them too):"]
        for t in prior:
            lines.append(f"- {t.get('title', '')}: {t.get('angle', '')}")
    if feedback.strip():
        lines += ["", "EDITOR FEEDBACK on what's missing — aim the new takes "
                      "at this:", feedback.strip()]

    result = client.complete(system, "\n".join(lines))
    spend = estimate_cost(
        settings.openai_model, result.prompt_tokens, result.completion_tokens
    )
    data = json.loads(result.content)
    takes = [Take.model_validate(t) for t in data.get("takes", [])]
    return TakesResult(takes=takes[:count], spend_usd=spend)


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
