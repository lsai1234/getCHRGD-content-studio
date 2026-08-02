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
    #: The comic idea this was written from — shown to the editor so they can
    #: see the joke the episode was aiming at, not just the prose.
    the_joke: str = ""

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
        if self.the_joke:
            lines.append(
                f"THE JOKE this episode is landing (protect it — the slide "
                f"that carries it must not be softened): {self.the_joke}")
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


class Premise(BaseModel):
    """A comic idea, before a word of the episode is written.

    The stage that was missing. Both failed episodes had a want, an obstacle
    and a choice — and no joke, because nothing ever asked for one. A premise
    is not a situation; it is a character's flaw plus the situation that
    punishes them for it.
    """

    lead: str = ""          # roster key of the character it happens to
    flaw: str = ""          # what they cannot help doing, in five words
    trap: str = ""          # the situation that is worst for exactly that flaw
    self_inflicted: str = ""  # how their own flaw springs the trap
    reversal: str = ""      # they get what they wanted and it punishes them
    the_joke: str = ""      # "the funny is: ..." — one sentence
    cost: str = ""          # what they lose, publicly

    def is_usable(self) -> bool:
        # The joke and the self-infliction are the two that can't be missing:
        # without them it's an anecdote about a gym.
        return bool(self.the_joke.strip() and self.self_inflicted.strip())

    def as_brief(self) -> str:
        lines = ["THE COMIC IDEA — write THIS episode, not another one:"]
        for label, value in (
            ("Who it happens to", self.lead),
            ("Their flaw", self.flaw),
            ("The trap", self.trap),
            ("How they spring it themselves", self.self_inflicted),
            ("The reversal", self.reversal),
            ("THE JOKE", self.the_joke),
            ("What it costs them", self.cost),
        ):
            if str(value).strip():
                lines.append(f"- {label}: {value}")
        lines.append(
            "Everything in the episode serves THE JOKE above. If a sentence "
            "does not build toward it, cut the sentence."
        )
        return "\n".join(lines)


PREMISE_SYSTEM = """You are the comedy writer on a comic serial set in a knackered UK gym. Before anyone writes an episode, you pitch the COMIC IDEAS.

You are not pitching situations. A situation is "there is one squat rack and everyone wants it" — that is a setting, and it is not funny on its own. A comic idea is a character's FLAW plus the one situation that punishes them for having it.

THE METHOD, follow it exactly for each pitch:

1. PICK ONE CHARACTER and name the flaw in about five words. Not what they look like — the thing they CANNOT HELP DOING. "Has to tell you his 5k time." "Cannot refuse credit." "Cannot be seen being kind."

2. BUILD THE TRAP. Ask: what is the single worst situation for someone with exactly that flaw? This question is where the comedy actually comes from, and almost nothing else is.

3. MAKE THEM SPRING IT THEMSELVES. The flaw must CAUSE the trouble, not just sit next to it. Bad luck is not comedy; self-infliction is. "He brags about his 5k. The one person who would act on that hears him, and signs him up as a squat partner. He talked himself into leg day."

4. FIND THE REVERSAL. Strongest shapes first: they get exactly what they wanted and it is a punishment; their lie comes true in the worst way; they win and the prize is worthless; they dodge the thing and dodging costs more.

5. NAME THE JOKE IN ONE SENTENCE, starting "the funny is:". If you cannot write that sentence, the pitch is dead — bin it and do another. This is the check that matters most.

6. NAME THE COST. Somebody loses something, ideally their dignity, ideally in public. "He stands still and does nothing" is not a cost.

HARD RULES:
- The lead must be ONE character from the roster you are given, and the flaw must be one they actually have.
- Small and specific beats big and clever. A gym, a machine, a queue, a sign-up sheet.
- Never pitch a situation with no person trapped in it.
- Five genuinely different pitches — different characters, different flaws, different traps. Two pitches that could share a joke are one pitch.

Return a SINGLE JSON object, no markdown:
{
  "premises": [
    {
      "lead": "roster key",
      "flaw": "what they cannot help doing, about five words",
      "trap": "the situation that is worst for that exact flaw",
      "self_inflicted": "how their own flaw springs the trap — one line",
      "reversal": "they get what they wanted and it punishes them — one line",
      "the_joke": "the funny is: ...",
      "cost": "what they lose, and who sees it"
    }
  ]
}
Exactly five, funniest first."""


PICK_SYSTEM = """You are picking which ONE comic idea gets written into an episode of a gym comic serial. You will be shown five pitches.

Judge them on one thing: WHICH IS ACTUALLY FUNNIEST — not which is cleverest, not which is most original, not which has the best premise on paper.

What wins:
- The joke can be understood instantly, with no setup. If you have to explain the rules of a game for it to land, it loses.
- The character springs their OWN trap. Bad luck is not funny; a person destroyed by their own favourite habit is.
- The cost is real and public. Losing face in front of others beats a private inconvenience every time.
- You can picture it happening in a real gym.

What loses:
- A situation with nobody trapped in it.
- A joke that needs a rule the reader does not have.
- "Mild inconvenience" — someone being slightly delayed is not a story.

Return a SINGLE JSON object, no markdown:
{
  "winner": 0,
  "why": "one line: what makes this the funniest of the five",
  "epitaphs": ["one short line per losing pitch, in order, saying why it lost"]
}
`winner` is the 0-based index."""


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

YOU HAVE BEEN GIVEN THE JOKE. Your entire job is to land it. Every sentence builds toward it; anything that does not is cut. You are not looking for a new idea — you are executing one.

THE SHAPE, and it is not negotiable:
1. Show the flaw ONCE, in action, in the first two sentences. Do not describe it — let them do it.
2. The flaw causes the trouble. Show the exact moment their own behaviour springs the trap.
3. Escalate in THREES: it happens, it happens again worse, then the third time it breaks. Two is not a pattern; four is a list.
4. The reversal lands. They get the thing, and the thing is a punishment.
5. THE LAST LINE IS THE PUNCHLINE. It is the funniest or most damning line in the episode, and you should know what it is before you write the first line. Never trail off. "He did not move" is not an ending — it is a writer running out of story.

CLEAR IS THE DELIVERY, FUNNY IS THE JOB. A previous attempt was perfectly readable and had no joke in it anywhere: a man wanted a treadmill, ended up on a sign-up sheet, and stood still. Plain sentences describing nothing happening is not an improvement on confusing sentences describing nothing happening. Both are failures.

SOMEBODY LOSES SOMETHING, and other characters see it. Public humiliation is the currency of this show. A private inconvenience is not a story.

WHERE THE COMEDY COMES FROM: a person behaving exactly like themselves in the one situation that punishes them for it. Not from decorated sentences, not from random absurdity, not from a character saying their catchphrase. Punch at ego and status, never at appearance.

LENGTH: 150-200 words. Around 12-18 short sentences.

BEFORE YOU RETURN IT: find the funniest line in what you wrote. If you cannot point at one, you have written the failure described above — go back and land the joke you were given.

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
7. WHERE IS THE LAUGH? Quote the single funniest line, and say in a few words why it is funny. If nothing in it is funny, say so plainly — that is a FAIL, and it is the most common one. A previous episode was completely clear and contained no joke at all: a man wanted a treadmill, got signed up for squats, and stood still. Perfectly readable, entirely pointless.
8. DID ANYBODY LOSE ANYTHING, in front of anyone else? If nothing was lost, it FAILS.
9. IS THE LAST LINE THE STRONGEST LINE? An episode that trails off has no ending.

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
  "funniest_line": "quote it, or empty string if nothing in it is funny",
  "why_its_funny": "a few words, or empty string",
  "who_lost_what": "one line, or empty string if nobody lost anything",
  "ends_on_its_best_line": true or false,
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
    funniest_line: str = ""
    why_its_funny: str = ""
    who_lost_what: str = ""
    ends_on_its_best_line: bool = True
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
            # The one that "First names on the board" would have failed: it was
            # clear, it was a story by shape, and nothing in it was funny.
            and bool(self.funniest_line.strip())
            and bool(self.who_lost_what.strip())
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
        if not self.funniest_line.strip():
            bits.append(
                "THERE IS NO JOKE IN IT. A cold reader could not point at a "
                "single funny line. This is the main failure: clear writing "
                "about nothing happening is still nothing happening. Land the "
                "comic idea you were given — the flaw has to cause the trouble, "
                "and the reversal has to actually land."
            )
        if not self.who_lost_what.strip():
            bits.append(
                "Nobody lost anything. Somebody has to be humiliated, in front "
                "of other people. A private inconvenience is not a story."
            )
        if not self.ends_on_its_best_line:
            bits.append(
                "It trails off. The last line must be the funniest or most "
                "damning line in the episode."
            )
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


def _payload(idea, store, cast_keys: list[str], note: str = "",
             premise=None) -> str:
    from .series import episode_brief

    parts = [episode_brief(store, cast_keys)]
    if premise is not None:
        parts.append(premise.as_brief())
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


def pitch_premises(
    idea, settings: Settings, store, *, cast_keys: list[str], client=None
) -> tuple[list[Premise], float]:
    """Five comic ideas, before a word of the episode exists."""
    from .pipeline import estimate_cost
    from .roster import cast_block, resolve

    from .roster import load_process

    parts = [load_process(), cast_block(resolve(cast_keys))]
    seed = (getattr(idea, "concept_note", "") or "").strip()
    if seed and seed.lower() not in ("", "the next episode", "blank canvas"):
        parts.append(
            "THE EDITOR'S DIRECTION — every pitch must be a version of this: "
            + seed
        )
    parts.append("Pitch five comic ideas. Return the JSON object.")

    result = client.complete(PREMISE_SYSTEM, "\n\n".join(parts))
    spend = estimate_cost(settings.openai_model, result.prompt_tokens,
                          result.completion_tokens)
    raw = (result.content or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in the premise pitch")
    data = json.loads(raw[start:end + 1])
    premises = [Premise.model_validate(p) for p in (data.get("premises") or [])]
    return [p for p in premises if p.is_usable()], spend


def pick_premise(
    premises: list[Premise], settings: Settings, *, judge=None
) -> tuple[Premise, str, float]:
    """The funniest of the five. Falls back to the first on any failure."""
    from .pipeline import estimate_cost

    if len(premises) == 1 or judge is None:
        return premises[0], "", 0.0
    payload = "\n\n".join(
        f"[{i}] {p.the_joke}\n  lead: {p.lead} · flaw: {p.flaw}\n"
        f"  trap: {p.trap}\n  self-inflicted: {p.self_inflicted}\n"
        f"  reversal: {p.reversal}\n  cost: {p.cost}"
        for i, p in enumerate(premises)
    )
    try:
        raw = judge.judge(PICK_SYSTEM, payload)
        start, end = raw.find("{"), raw.rfind("}")
        data = json.loads(raw[start:end + 1])
        index = int(data.get("winner", 0))
        if not 0 <= index < len(premises):
            index = 0
        return premises[index], str(data.get("why") or ""), estimate_cost(
            settings.judge_model, 400, 150)
    except Exception as exc:  # noqa: BLE001 — a failed pick is not a failed episode
        log.warning("premise pick failed: %s", exc)
        return premises[0], "", 0.0


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

    # STAGE 0: find the joke before writing a word. Both failed episodes had a
    # want, an obstacle and a choice and no comic idea underneath, because
    # nothing ever asked for one.
    premise: Premise | None = None
    try:
        premises, pitch_spend = pitch_premises(
            idea, settings, store, cast_keys=cast_keys, client=client)
        spend += pitch_spend
        if premises:
            premise, why, pick_spend = pick_premise(premises, settings, judge=judge)
            spend += pick_spend
            _note(f"the joke: {premise.the_joke}" + (f" — {why}" if why else ""))
    except Exception as exc:  # noqa: BLE001 — fall through to writing unprompted
        log.warning("premise stage failed for %s: %s", idea.idea_id, exc)

    story: Story | None = None
    note = ""
    for attempt in range(MAX_REWRITES + 1):
        _note("writing the episode as a story first"
              if attempt == 0 else "that wasn't a story — writing a different one")
        try:
            result = client.complete(
                STORY_SYSTEM, _payload(idea, store, cast_keys, note, premise))
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
        if premise is not None and not story.the_joke.strip():
            story.the_joke = premise.the_joke

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
