"""The story stage — write the episode as prose BEFORE it becomes slides.

Episode 1 came back as "8pm: Rack's guest list only": a premise, elaborated
across eight slides. Nobody wanted anything, nobody did anything, and there was
no question pulling anyone to slide 2.

The diagnosis wasn't that the brief was too loose. It was that the engine was
being asked to invent the premise, the plot, the character beats, the on-slide
copy, the caption AND the art direction in a single call, while obeying a
spine, a canon, a likeness gate and a worked example. Something has to give,
and what gives is the hardest thing in that list: the story.

So this splits the job, which is the same move that fixed concept quality in
Phase 0 (sketch fast, develop the one that's chosen):

    STAGE 1  write_story   → 150 words of PROSE. No slides, no art direction.
                             Who wants what, what goes wrong, what they choose,
                             how it ends, what's left hanging.
    STAGE 2  gate_story    → a cold read. A judge who has seen only the prose
                             must be able to say what happened and why they'd
                             swipe. It cannot answer that about a premise.
    STAGE 3  the build     → the existing write call, handed a finished story
                             and told to CUT it into slides, not invent one.

Writing a story and formatting a story are different jobs. Collapsing them is
why the story loses.

The prose is persisted on the idea, so the editor can read the episode in the
create journey **before** any image is paid for — a bad episode costs ten
seconds to reject instead of eight renders. That's the cheapest quality gate
available anywhere in this pipeline.

Every stage is injectable for offline tests, and nothing here is fatal: a
failure at any point falls through to the previous behaviour with whatever it
has, because an episode that writes itself imperfectly beats a journey that
dead-ends.
"""

from __future__ import annotations

import json
import logging

from pydantic import BaseModel, Field

from .config import Settings

log = logging.getLogger(__name__)

#: Route key the finished prose rides on.
ROUTE_KEY = "story"

#: How many rewrites a story gets after a failed cold read. One is enough: if
#: the second attempt still isn't a story, the problem is the premise and a
#: third go just spends money agreeing with itself.
MAX_REWRITES = 1


class Story(BaseModel):
    title: str = ""
    #: One line: who wants what, and what's in the way.
    logline: str = ""
    #: The episode itself, as prose. This is the thing that has to be good.
    prose: str = ""
    #: The one status change.
    change: str = ""
    #: What the ending deliberately leaves hanging.
    unresolved: str = ""
    cast: list[str] = Field(default_factory=list)

    def is_usable(self) -> bool:
        return bool(self.prose.strip() and self.change.strip())

    def as_brief(self) -> str:
        """The finished story, handed to the slide-cutting call."""
        lines = [
            "THE STORY — THIS EPISODE IS ALREADY WRITTEN. Your job is to CUT "
            "it into slides, not to invent a new one. Do not add plot, do not "
            "change who does what, do not resolve anything it left open. Keep "
            "the dialogue: lines that are good in the prose are good on a "
            "slide, and rewriting them is how the jokes get lost.",
            "",
        ]
        if self.title:
            lines.append(f"TITLE: {self.title}")
        if self.logline:
            lines.append(f"THE SPINE: {self.logline}")
        lines.append("")
        lines.append(self.prose.strip())
        lines.append("")
        if self.change:
            lines.append(f"THE ONE STATUS CHANGE: {self.change}")
        if self.unresolved:
            lines.append(f"LEFT DELIBERATELY OPEN (do not resolve it): {self.unresolved}")
        lines.append("")
        lines.append(
            "HOW TO CUT IT: each slide is one beat of the story above, in "
            "order. A beat is something HAPPENING — an action, a line of "
            "dialogue, a reveal. Every slide but the last must end on the "
            "question the next one answers. If a beat needs two slides, split "
            "it; if two beats are the same beat, merge them. Never pad to hit "
            "a slide count."
        )
        return "\n".join(lines)


STORY_SYSTEM = """You are the writer on a comic serial published as a TikTok photo carousel by a UK gym brand. You write ONE episode, as PROSE. No slides, no image direction, no formatting — just the story.

This is the only job. Get the story right and the rest of the pipeline works; get it wrong and no amount of art saves it.

WHAT AN EPISODE IS. Somebody WANTS something. Something gets in the way — and the obstacle comes from who they are, not from bad luck. They make a CHOICE that costs them. Something changes. One question is left hanging.

If you cannot say "X wants Y but Z" about your episode, you have written a premise, not a story, and it will fail. A premise is a situation ("the squat rack has a guest list"). A story is a person in trouble ("Tracy Beaker fixed the machine at 3am and now has to watch someone else take the credit, because being caught being kind would end her").

HARD RULES:
- ONE character drives it. Others react. An ensemble with no lead is a scene, not an episode.
- The want must be SPECIFIC and small. Not "respect" — the last squat rack, a spot on the sign-in sheet, one uninterrupted set.
- The obstacle must come from a character's established trait. Coincidence is not plot.
- The choice must COST them something they care about. That cost is the episode.
- End on an image and an open question, not a moral.

MAKE IT FUNNY THE WAY THIS SHOW IS FUNNY: specificity is the joke. "Put down his coffee for the first time since March" is funny; "was annoyed" is not. Punch at status and ego, never at appearance. Play each character's established bits — a running gag that escalates beats a new joke.

WRITE IN SCENES. Actual dialogue, actual objects, actual actions. Around 150-200 words. Every sentence should either move the plot or land a joke; if it does neither, cut it.

Return a SINGLE JSON object, no markdown:
{
  "title": "a short episode title",
  "logline": "X wants Y but Z — one line",
  "prose": "the episode, 150-200 words, in scenes, with dialogue",
  "change": "the one thing that is different at the end, one line",
  "unresolved": "the question the ending deliberately leaves hanging, one line",
  "cast": ["roster keys of the characters actually in it"]
}"""


GATE_SYSTEM = """You are a reader who has been handed one episode of a comic serial and knows nothing else about it. Read it once, at speed, the way somebody scrolling would.

Then answer honestly. You are not being asked whether it is good — you are being asked whether it is a STORY:

1. WHAT HAPPENED? Say it in one sentence, as events. If the honest answer is a description of a situation rather than a sequence of events, that is a FAIL.
2. WHO WANTED WHAT, and what stopped them? If you cannot name a person and a want, that is a FAIL.
3. WHAT WOULD MAKE YOU READ ON after the opening? Name the actual question. "It seems interesting" is a FAIL.
4. WHAT DID IT COST the person who made the choice? If nothing was at stake, that is a FAIL.

Be strict and be honest. A confident pass on a premise wastes eight paid images and puts a bad post on a real account. Saying "this is a situation, not a story" is the single most useful thing you can do here.

Return a SINGLE JSON object, no markdown:
{
  "is_a_story": true or false,
  "what_happened": "one sentence, as events",
  "who_wanted_what": "one sentence",
  "why_read_on": "the actual question it plants",
  "what_it_cost": "one sentence",
  "verdict": "one line: what specifically needs fixing, or why it works"
}"""


class StoryVerdict(BaseModel):
    is_a_story: bool = True
    what_happened: str = ""
    who_wanted_what: str = ""
    why_read_on: str = ""
    what_it_cost: str = ""
    verdict: str = ""

    def failure_note(self) -> str:
        return (
            "A cold reader could not follow this as a story. Their read: "
            f"what happened = '{self.what_happened or 'nothing they could name'}'; "
            f"who wanted what = '{self.who_wanted_what or 'nobody'}'; "
            f"why read on = '{self.why_read_on or 'no reason given'}'; "
            f"what it cost = '{self.what_it_cost or 'nothing'}'. "
            f"Fix: {self.verdict or 'give one character a specific want, an obstacle from their own character, and a choice that costs them.'}"
        )


def _parse(text: str, model):
    raw = (text or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in the response")
    return model.model_validate(json.loads(raw[start:end + 1]))


def _payload(idea, store, cast_keys: list[str], note: str = "") -> str:
    from .series import episode_brief

    parts = [episode_brief(store, cast_keys)]
    seed = (getattr(idea, "concept_note", "") or "").strip()
    if seed and seed.lower() not in ("", "the next episode", "blank canvas"):
        parts.append(
            "THE EDITOR'S PREMISE — build the episode on THIS. It is the "
            f"direction they want, not a suggestion: {seed}"
        )
    if note:
        parts.append(
            "YOUR PREVIOUS ATTEMPT FAILED A COLD READ. Do not tidy it — write "
            "a different episode that fixes this:\n" + note
        )
    parts.append("Write the episode. Return the JSON object.")
    return "\n\n".join(p for p in parts if p)


def write_story(
    idea,
    settings: Settings,
    store,
    *,
    cast_keys: list[str],
    client=None,
    judge=None,
    notify=None,
) -> tuple[Story | None, float]:
    """Write the episode as prose, and make it survive a cold read.

    Returns `(story, spend)`. A `None` story means the stage failed and the
    caller should fall through to the single-call behaviour — this is a quality
    booster, never a hard dependency.
    """

    def _note(msg: str) -> None:
        if notify:
            notify(msg)

    from .pipeline import OpenAIChatClient, estimate_cost

    spend = 0.0
    try:
        client = client or OpenAIChatClient(settings, temperature=0.95)
    except Exception as exc:  # noqa: BLE001 — no key: the build still runs
        log.warning("story stage unavailable: %s", exc)
        return None, 0.0

    story: Story | None = None
    note = ""
    for attempt in range(MAX_REWRITES + 1):
        _note("writing the episode as a story first"
              if attempt == 0 else "that wasn't a story — writing a different one")
        try:
            result = client.complete(STORY_SYSTEM, _payload(idea, store, cast_keys, note))
            spend += estimate_cost(
                settings.openai_model, result.prompt_tokens, result.completion_tokens
            )
            candidate = _parse(result.content, Story)
        except Exception as exc:  # noqa: BLE001
            log.warning("story write failed for %s: %s", idea.idea_id, exc)
            break
        if not candidate.is_usable():
            note = "It returned nothing usable. Write the actual episode."
            continue
        story = candidate

        verdict, gate_spend = gate_story(story, settings, judge=judge)
        spend += gate_spend
        if verdict is None or verdict.is_a_story:
            if verdict is not None:
                _note(f"the story holds up: {verdict.what_happened}")
            return story, spend
        _note(f"cold read failed: {verdict.verdict}")
        note = verdict.failure_note()

    return story, spend


def gate_story(
    story: Story, settings: Settings, *, judge=None
) -> tuple[StoryVerdict | None, float]:
    """The cold read. `None` means the gate couldn't run — never a failure."""
    from .pipeline import estimate_cost

    try:
        if judge is None:
            from .claims import OpenAIClaimsJudge

            judge = OpenAIClaimsJudge(settings)
        text = judge.judge(GATE_SYSTEM, story.prose.strip())
        return _parse(text, StoryVerdict), estimate_cost(settings.judge_model, 500, 200)
    except Exception as exc:  # noqa: BLE001 — a gate that crashes gets removed
        log.warning("story gate failed: %s", exc)
        return None, 0.0


def story_from_route(route: dict | None) -> Story | None:
    """The stored story for an idea, if the stage has run."""
    raw = (route or {}).get(ROUTE_KEY)
    if not isinstance(raw, dict):
        return None
    try:
        story = Story.model_validate(raw)
    except Exception:  # noqa: BLE001
        return None
    return story if story.is_usable() else None
