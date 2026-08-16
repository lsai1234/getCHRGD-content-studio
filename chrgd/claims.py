"""The claims gate — the compliance check STRAIGHT UP runs on a finished post.

D2 drew the line: say what the evidence supports, never promise an outcome,
never use medical framing, and be opinionated about the *industry* (pricing,
labelling, marketing) rather than about physiology. That line is only worth
anything if something enforces it, and a self-scored `claim_safety` number
inside the write call is the model marking its own homework on the one topic
where a UK supplement brand carries real outside risk.

So this is a separate pass over the WHOLE post — every slide, the caption, the
comment trigger — in two layers:

  * **the lint** — deterministic patterns, no API key, always runs, instant.
    Catches the unambiguous stuff ("clinically proven", "boosts your immune
    system", "guaranteed results", "studies show" with no study). It is
    offline and testable, so it works in CI and on a dev box with no key.
  * **the judge** — a cheap LLM pass for the things a regex can't see: an
    implied promise, a hedge that isn't really a hedge, a physiology opinion
    dressed as fact. Injectable, config-gated, and never fatal on an API error.

**What a failure does.** Unlike the slide-1 concept gate, this one is allowed
to stop a post: a flagged post is marked `review` rather than shipped, so a
human sees it. That's the right default for a compliance check — the cost of a
false positive is thirty seconds of an editor's time; the cost of a false
negative is a claim on a public account. It only escalates for shows whose gate
profile is `claims`; everywhere else the flags are advisory and ride along in
`route_json` for visibility.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel

from .config import Settings
from .models import Idea

log = logging.getLogger(__name__)

#: The gate profile that makes claims failures blocking rather than advisory.
CLAIMS_PROFILE = "claims"


@dataclass(frozen=True)
class Pattern:
    name: str
    regex: str
    why: str


#: Deterministic red flags. Every one of these is a phrasing that a UK
#: supplement brand should not put on a public slide, regardless of context —
#: which is what makes them safe to match without a model.
#:
#: Deliberately NOT here: anything that needs context to judge. "Boosts" on its
#: own is fine ("boosts the flavour"); "boosts your immune system" is not. Over
#: -matching would train the editor to ignore the gate, which is worse than not
#: having one.
PATTERNS: tuple[Pattern, ...] = (
    Pattern("guaranteed", r"\bguarantee(?:d|s)?\b",
            "promises an outcome — describe what it does, never what it will do for them"),
    Pattern("clinically_proven", r"\bclinically\s+proven\b",
            "a regulated-sounding claim; 'studied for' is the honest form"),
    Pattern("scientifically_proven", r"\b(?:scientifically|medically)\s+proven\b",
            "overstates evidence — nothing here is 'proven'"),
    Pattern("cures", r"\b(?:cure|cures|curing|heals?|treats?|treating)\b",
            "medical/therapeutic framing — this is a supplement, not a treatment"),
    Pattern("prevents_disease", r"\bprevents?\s+(?:illness|disease|cancer|injury|colds?)\b",
            "a health claim about disease — never on this account"),
    Pattern("immune_claim", r"\b(?:boosts?|strengthens?|supports?)\s+(?:your\s+)?immun\w+",
            "an immune-function health claim"),
    Pattern("detox", r"\bdetox(?:es|ing|ify\w*)?\b",
            "'detox' has no defensible meaning here and reads as pseudoscience"),
    Pattern("melts_fat", r"\b(?:melts?|burns?|torch(?:es)?|blasts?)\s+(?:away\s+)?(?:fat|calories)\b",
            "implies a supplement drives fat loss on its own"),
    Pattern("testosterone_boost", r"\b(?:boosts?|increases?|raises?)\s+(?:your\s+)?testosterone\b",
            "a hormonal claim far bigger than the evidence carries"),
    Pattern("doctor_recommended", r"\b(?:doctor|gp|pharmacist)[- ]?(?:recommended|approved)\b",
            "implies medical endorsement"),
    Pattern("vague_studies", r"\b(?:studies|science|research)\s+(?:show|shows|prove[sd]?|say|says)\b",
            "'studies show' without a nameable study — say which, or say it's mixed"),
    # `#` is not a word character, so a leading \b never matches before it.
    Pattern("number_one", r"\bno\.?\s*1\b|#\s*1\b|\bbest\s+in\s+the\s+world\b",
            "an unsubstantiated superiority claim"),
    Pattern("outcome_promise", r"\b(?:you\s+will|you'll)\s+(?:lose|gain|build|burn|drop)\b",
            "promises the viewer a specific result"),
    # --- The quiz's own risk (config/campaign.toml [campaign.compliance]) ----
    # A recommendation built from six typed answers is not an assessment of a
    # body, and the gap between those two things is the whole compliance
    # exposure of a personalisation product. These match the framings that
    # cross it — every one of which is a phrasing that would test WELL, which
    # is exactly why a regex has to hold the line rather than an editor's
    # judgement at 11pm.
    Pattern("deficiency_claim",
            r"\b(?:what|which)\b[^.?!]{0,40}\byour\s+body\s+(?:needs|is\s+missing|lacks)\b",
            "implies the quiz assesses a body — it reads answers, it cannot know this"),
    Pattern("deficiency_word", r"\bdeficien(?:t|cy|cies)\b",
            "a clinical finding this brand cannot make from a questionnaire"),
    Pattern("diagnose", r"\bdiagnos(?:e|es|ed|ing|is|tic)\b",
            "diagnostic framing — the quiz recommends, it never diagnoses"),
    Pattern("quiz_knows_body",
            r"\b(?:quiz|test|scan|assessment)\b[^.?!]{0,30}\b(?:analys|assess|detect|reveal)\w*\b",
            "implies clinical assessment; the quiz reads typed answers and nothing else"),
    Pattern("tailored_to_body",
            r"\b(?:tailored|personalised|personalized|matched|formulated)\s+to\s+your\s+(?:body|biology|dna|genetics|blood|hormones)\b",
            "implies biological personalisation — it is personalised to your ANSWERS"),
    Pattern("prescribe", r"\bprescri(?:be|bes|bed|bing|ption)\b",
            "prescribing is a regulated act — this is a recommendation"),
    # --- Claims about our own machinery (config/campaign.toml [campaign.tech])
    # A different risk from the health claims above: these are claims about US.
    # Superiority and technology-leadership claims have to be substantiated on
    # demand under UK advertising rules, and they read as ordinary marketing
    # rather than as something to be careful with — which is exactly why an
    # editor waves them through and a pattern has to catch them.
    Pattern("state_of_the_art",
            r"\b(?:state[- ]of[- ]the[- ]art|cutting[- ]edge|next[- ]gen(?:eration)?|"
            r"revolutionary|world[- ]class|industry[- ]leading)\b",
            "an unsubstantiated superiority claim — describe what it does instead"),
    Pattern("first_of_its_kind",
            r"\b(?:first|only)\s+(?:of\s+its\s+kind|(?:one|brand)\s+to)\b",
            "a 'first/only' claim needs evidence we can produce on request"),
    Pattern("ai_knows_you",
            r"\b(?:ai|algorithm|technology)\b[^.?!]{0,40}\b(?:knows|works\s+out|"
            r"understands|figures\s+out)\b[^.?!]{0,20}\byou(?:r\s+body)?\b",
            "implies the system knows something about you it wasn't told"),
)

_COMPILED = tuple((p, re.compile(p.regex, re.I)) for p in PATTERNS)


class ClaimFlag(BaseModel):
    where: str          # "slide 3" / "caption" / "comment trigger"
    text: str           # the offending fragment, trimmed
    rule: str           # the pattern name or "judge"
    why: str

    def line(self) -> str:
        return f"{self.where}: “{self.text}” — {self.why}"


class JudgeVerdict(BaseModel):
    safe: bool = True
    flags: list[ClaimFlag] = []
    note: str = ""


@dataclass
class ClaimsResult:
    checked: bool = False
    safe: bool = True
    blocking: bool = False          # does a failure hold this post for review?
    flags: list[ClaimFlag] = field(default_factory=list)
    note: str = ""
    spend_usd: float = 0.0
    error: str = ""

    def as_payload(self) -> dict:
        return {
            "checked": self.checked,
            "safe": self.safe,
            "blocking": self.blocking,
            "flags": [f.model_dump() for f in self.flags],
            "note": self.note,
            "error": self.error,
        }

    def reasons(self) -> list[str]:
        return [f.line() for f in self.flags]


class ClaimsJudge(Protocol):
    def judge(self, system: str, user: str) -> str: ...


# --- layer 1: the lint ------------------------------------------------------


def _fragments(post: dict) -> list[tuple[str, str]]:
    """Every piece of viewer-facing text in a built post, with its location."""
    out: list[tuple[str, str]] = []
    for i, slide in enumerate(post.get("slides") or [], 1):
        for key in ("headline", "supporting", "body"):
            text = str((slide or {}).get(key) or "").strip()
            if text:
                out.append((f"slide {i}", text))
    for key, label in (("caption", "caption"),
                       ("comment_trigger", "comment trigger")):
        text = str(post.get(key) or "").strip()
        if text:
            out.append((label, text))
    return out


def _snippet(text: str, match: re.Match, width: int = 60) -> str:
    start = max(0, match.start() - width // 2)
    end = min(len(text), match.end() + width // 2)
    return ("…" if start else "") + text[start:end].strip() + ("…" if end < len(text) else "")


def lint_post(post: dict) -> list[ClaimFlag]:
    """Deterministic claim flags across a whole built post.

    No API key, no network, no model. Runs on every post everywhere — a
    guaranteed-outcome promise is a problem whichever show wrote it.
    """
    flags: list[ClaimFlag] = []
    seen: set[tuple[str, str]] = set()
    for where, text in _fragments(post):
        for pattern, rx in _COMPILED:
            match = rx.search(text)
            if not match:
                continue
            key = (where, pattern.name)
            if key in seen:
                continue
            seen.add(key)
            flags.append(ClaimFlag(
                where=where, text=_snippet(text, match),
                rule=pattern.name, why=pattern.why,
            ))
    return flags


# --- layer 2: the judge -----------------------------------------------------


JUDGE_SYSTEM = """You are the compliance check for a UK gym/supplement brand's social posts, reviewing a finished carousel before it is published.

The brand's line, which you enforce exactly:
- DESCRIBE what a supplement or ingredient does and what the evidence supports. NEVER promise an outcome to the viewer.
- NEVER use medical, therapeutic or diagnostic framing — no curing, treating, preventing or healing anything.
- Evidence claims must be honest: "studies show" without a nameable study is a fail, and so is stating a contested finding as settled. Saying the evidence is mixed or thin is CORRECT and must never be flagged.
- The brand IS allowed to be sharp, opinionated and rude about the INDUSTRY: pricing, labelling, proprietary blends, underdosing, marketing lies. That is not a claim and must never be flagged.
- Opinions about PHYSIOLOGY (what happens inside a body) must be reported neutrally, not argued.

You are checking for real risk, not tone. Do NOT flag: strong writing, humour, a blunt opinion about a company or a price, an honest "we don't really know", or ordinary training advice that makes no supplement claim. Over-flagging is itself a failure — it trains the editor to ignore you.

Return a SINGLE JSON object, no markdown:
{
  "safe": true or false,
  "note": "one short line: the overall verdict",
  "flags": [
    {"where": "slide 3 | caption | comment trigger", "text": "the exact offending words", "rule": "judge", "why": "one line: which rule it breaks and how to fix it"}
  ]
}
An empty flags array with "safe": true is the expected result for a well-written post."""


def _judge_payload(post: dict) -> str:
    lines = ["Review this finished post.", ""]
    for where, text in _fragments(post):
        lines.append(f"[{where}] {text}")
    return "\n".join(lines)


def parse_verdict(text: str) -> JudgeVerdict:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1] if "```" in raw[3:] else raw[3:]
        raw = raw.split("\n", 1)[-1] if raw[:4].lower().startswith("json") else raw
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in the claims verdict")
    return JudgeVerdict.model_validate(json.loads(raw[start:end + 1]))


class OpenAIClaimsJudge:
    """The default judge: the cheap judge model, same as the concept gate."""

    def __init__(self, settings: Settings):
        from .pipeline import OpenAIChatClient

        self._client = OpenAIChatClient(
            settings, temperature=0.0, model=settings.judge_model
        )

    def judge(self, system: str, user: str) -> str:
        return self._client.complete(system, user).content


# --- the gate ---------------------------------------------------------------


def is_blocking(idea: Idea) -> bool:
    """True when a claims failure should hold this post for review.

    Tied to the show's gate profile rather than to a show key, so a new show
    that needs the same discipline gets it by naming the profile.
    """
    from .shows import show_for_idea

    show = show_for_idea(idea)
    return bool(show and show.gate_profile == CLAIMS_PROFILE)


def check_claims(
    post: dict,
    idea: Idea,
    settings: Settings,
    *,
    judge: ClaimsJudge | None = None,
    use_judge: bool | None = None,
) -> ClaimsResult:
    """Run the claims gate over a finished post.

    The lint always runs. The judge runs when the post is on a claims-gated
    show (or `use_judge=True`), and its failure is never fatal — a compliance
    check that crashes the build teaches everyone to switch it off.
    """
    result = ClaimsResult(checked=True, blocking=is_blocking(idea))
    result.flags = lint_post(post)

    wants_judge = result.blocking if use_judge is None else use_judge
    if wants_judge:
        try:
            judge = judge or OpenAIClaimsJudge(settings)
            verdict = parse_verdict(judge.judge(JUDGE_SYSTEM, _judge_payload(post)))
            result.note = verdict.note
            # Dedupe against the lint: the judge often restates what a pattern
            # already caught, and two flags for one phrase reads as two problems.
            seen = {(f.where, f.text) for f in result.flags}
            for flag in verdict.flags:
                if (flag.where, flag.text) not in seen:
                    result.flags.append(flag)
        except Exception as exc:  # noqa: BLE001 — a crashing gate teaches people to switch it off
            log.warning("claims judge failed for %s: %s", idea.idea_id, exc)
            result.error = str(exc)

    result.safe = not result.flags
    return result
