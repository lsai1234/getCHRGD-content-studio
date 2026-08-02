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
import re

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


STORY_SYSTEM = """You are the writer on a comic serial published as a TikTok photo carousel. You write ONE episode as PROSE. No slides, no image direction — just the story.

THE ONE THING THAT MATTERS MOST IS THAT IT IS EASY TO FOLLOW. A reader gets one pass, on a phone, at speed, with no context. If they have to re-read a sentence, you have lost them. Clear and funny beats clever every single time — and "clever" writing that costs comprehension is the most common way this show fails.

TWO CHARACTERS. Three at the absolute most, and the third barely speaks. Four characters in a short episode gives everyone one line each and nobody a story. One of them WANTS something; the other is in the way. That is the whole engine.

ONE THING PER SENTENCE. Short, plain, declarative. "She taped bunting to the rack. She was holding a clipboard." — not "she pirouetted, taping bunting to the uprights, a clipboard like a crown". Stacking three actions into one sentence reads as machine-written, because that is what machines do.

NO SIMILES AND NO METAPHORS. None. These are all real failures from a previous attempt and every one of them is banned: "a clipboard like a crown", "bent like a soft apology", "form like a threat to art", "ballots lifted like migrating birds". They sound like writing and they mean nothing. Say what actually happened instead.

ORDINARY WORDS. Write it the way you would tell it to a mate in the pub. If a sentence contains a word you would not say out loud, replace it.

ESTABLISH BEFORE YOU USE — this is the big one. You may not refer to any rule, object, event or stake the reader has not already been shown ON THE PAGE. A previous attempt mentioned "the vote", "the ballots", "the 4am footage" and "the sample tub", none of which had been explained, and the episode became unreadable. If your story needs a rule, show it happening in the first two sentences, in plain words, or pick a story that does not need one.

THE PREMISE MUST BE SAYABLE IN ONE PLAIN LINE that a stranger instantly understands. "There is one squat rack and two people want it at six o'clock" — yes. "At 6:00 the rack will decide who couples, unless someone trains on it" — no: nobody knows what that means.

CATCHPHRASES ARE A TOOL, NOT A TAX. Use one, if it fits the moment. You do NOT have to make every character perform their bit — a story where each character does their gag once in turn is a parade, not a plot.

NEVER WRITE A STRUCTURAL LABEL. A previous attempt literally wrote "Choice time." in the prose. Do not name the beats; just write them.

THE SHAPE:
- Somebody wants something small and specific. The last rack. A spot on the sign-in sheet. To not be caught doing something.
- Something gets in the way, and it comes from who a character IS, not from coincidence.
- They make a choice that costs them.
- It ends on an image, and one open question.

WHERE THE COMEDY COMES FROM: the situation and the characters, not the sentences. A person behaving exactly like themselves in a situation that punishes them for it is funny. A decorated sentence is not. Punch at ego and status, never at appearance.

LENGTH: 150-200 words. Around 12-18 short sentences.

BEFORE YOU RETURN IT, read it once as a stranger and ask: could I retell this to someone in one sentence, and did I have to guess at anything? If you had to guess, rewrite it simpler.

Return a SINGLE JSON object, no markdown:
{
  "title": "a short episode title in plain words",
  "logline": "X wants Y but Z — one plain line a stranger would understand",
  "prose": "the episode, 150-200 words, short plain sentences, no similes",
  "change": "the one thing that is different at the end, one line",
  "unresolved": "the question the ending leaves hanging, one line",
  "cast": ["roster keys of the two or three characters actually in it"]
}"""


GATE_SYSTEM = """You have been handed one episode of a comic serial. You know nothing else — no cast list, no world, no previous episodes. Read it ONCE, at the speed someone scrolling a phone reads.

Your job is a COMPREHENSION test, not a taste test. Do not be generous, and do not fill in gaps using your own knowledge — if something was not explained on the page, it was not explained.

1. RETELL IT in one plain sentence, as if to a friend. If you cannot, it fails.
2. LIST EVERYTHING YOU HAD TO GUESS AT — any rule, object, event or stake that was referred to but never established on the page. A previous failure mentioned "the vote", "the ballots" and "the 4am footage" with no explanation. If this list is not empty, it FAILS.
3. WHO WANTED WHAT, and what stopped them? Name a person and a want, or it fails.
4. WHAT DID IT COST them? If nothing was at stake, it fails.
5. HOW MANY CHARACTERS spoke or acted? More than three is a fail — it means nobody got a story.
6. DID ANY SENTENCE MAKE YOU STOP AND RE-READ IT? Quote it. Confusing writing fails even when the plot is sound.

Be strict. A confident pass on something unreadable puts a bad post on a real account and wastes eight paid images. "I had to guess what the vote was" is the single most useful thing you can report.

Return a SINGLE JSON object, no markdown:
{
  "is_a_story": true or false,
  "retell": "one plain sentence — what happened",
  "had_to_guess": ["things referred to but never explained; empty list if none"],
  "who_wanted_what": "one sentence",
  "what_it_cost": "one sentence",
  "character_count": 0,
  "confusing_lines": ["any sentence you had to re-read; empty list if none"],
  "verdict": "one line: what specifically to fix, or why it works"
}"""


class StoryVerdict(BaseModel):
    is_a_story: bool = True
    retell: str = ""
    had_to_guess: list[str] = Field(default_factory=list)
    who_wanted_what: str = ""
    what_it_cost: str = ""
    character_count: int = 0
    confusing_lines: list[str] = Field(default_factory=list)
    verdict: str = ""

    def passed(self) -> bool:
        """A pass needs comprehension, not just a story shape.

        The judge can pattern-match "this is a story" onto something nobody
        could actually follow — that is exactly what happened to the episode
        that shipped with an unexplained vote in it. So the objective answers
        override the subjective one.
        """
        return (
            self.is_a_story
            and not self.had_to_guess
            and not self.confusing_lines
            and self.character_count <= 3
        )

    def failure_note(self) -> str:
        bits = [f"A cold reader's honest read: \"{self.retell or 'they could not say what happened'}\"."]
        if self.had_to_guess:
            bits.append(
                "They had to GUESS at things you never explained on the page: "
                + "; ".join(self.had_to_guess)
                + ". Either show these plainly in the first two sentences, or "
                "write a story that does not need them."
            )
        if self.confusing_lines:
            bits.append(
                "They had to re-read these lines: "
                + "; ".join(f'"{line}"' for line in self.confusing_lines)
                + ". Rewrite them as short, plain, one-thing-per-sentence."
            )
        if self.character_count > 3:
            bits.append(
                f"{self.character_count} characters acted. Cut it to TWO. "
                "Everyone getting one line is why nobody got a story."
            )
        if not self.who_wanted_what.strip():
            bits.append("Nobody wanted anything. Give one character one small, specific want.")
        if self.verdict:
            bits.append(f"Their note: {self.verdict}")
        return " ".join(bits)


# --- the deterministic half: things a judge shouldn't have to notice --------

_SIMILE = re.compile(r"\b(?:like|as if|as though)\s+(?:a|an|the|it|he|she|they)\b", re.I)
_STRUCTURAL = re.compile(
    r"\b(?:choice time|the turn|the reveal|the payoff|beat \d|slide \d"
    r"|cold open|the stakes|act (?:one|two|three))\b", re.I,
)
#: A sentence this long on a phone is one nobody finishes.
_MAX_WORDS_PER_SENTENCE = 28


def lint_prose(prose: str) -> list[str]:
    """Texture failures, caught without spending a judge call.

    Every pattern here comes from a real generated episode. A judge *might*
    notice a simile; a regex always does, and these are the tells that make
    writing read as machine-made no matter how sound the plot is.
    """
    text = (prose or "").strip()
    if not text:
        return []
    notes: list[str] = []

    similes = _SIMILE.findall(text)
    if similes:
        notes.append(
            f"{len(similes)} simile(s) — cut every one. 'A clipboard like a "
            "crown' sounds like writing and means nothing. Say what happened."
        )
    label = _STRUCTURAL.search(text)
    if label:
        notes.append(
            f"'{label.group(0)}' is a structural label, not prose — you named "
            "a beat instead of writing it. Never do this."
        )
    long_ones = [
        sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", text)
        if len(sentence.split()) > _MAX_WORDS_PER_SENTENCE
    ]
    if long_ones:
        notes.append(
            f"{len(long_ones)} sentence(s) run past {_MAX_WORDS_PER_SENTENCE} "
            "words. Break them up: one thing per sentence, short and plain. "
            f'First one: "{long_ones[0][:90]}…"'
        )
    return notes


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

        # The deterministic checks run first — they're free, and there's no
        # sense paying a judge to read prose we already know is over-decorated.
        texture = lint_prose(story.prose)
        verdict, gate_spend = gate_story(story, settings, judge=judge)
        spend += gate_spend

        if not texture and (verdict is None or verdict.passed()):
            if verdict is not None:
                _note(f"a cold reader followed it: {verdict.retell}")
            return story, spend

        parts = []
        if verdict is not None and not verdict.passed():
            parts.append(verdict.failure_note())
        if texture:
            parts.append("Also, the writing itself: " + " ".join(texture))
        note = " ".join(parts)
        _note("the story needs another pass — "
              + (verdict.verdict if verdict is not None and verdict.verdict
                 else "the writing is too dense to follow"))

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
